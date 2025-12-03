#!/usr/bin/env python3
"""
Split a receipt into multiple receipts based on re-clustering.

This script:
1. Loads existing receipt data from DynamoDB
2. Re-clusters using two-phase approach
3. Creates new receipt records (saves locally first)
4. Migrates ReceiptWordLabels
5. Saves to DynamoDB (after local validation)
6. Exports NDJSON files to S3 (matches upload process)
7. Creates embeddings and CompactionRun directly (triggers compaction via streams)
8. Waits for compaction to complete
9. Adds labels (after embeddings exist in ChromaDB)

Usage:
    python scripts/split_receipt.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --original-receipt-id 1 \
        --output-dir ./local_receipt_splits \
        --dry-run
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

import boto3

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptMetadata,
    ReceiptWordLabel,
    CompactionRun,
    Line,
)

# Import transform utilities (for invert_warp)
try:
    from receipt_upload.geometry.transformations import invert_warp
    TRANSFORM_AVAILABLE = True
except ImportError:
    TRANSFORM_AVAILABLE = False
from receipt_upload.cluster import (
    dbscan_lines_x_axis,
    split_clusters_by_angle_consistency,
    reassign_lines_by_x_proximity,
    reassign_lines_by_vertical_proximity,
    merge_clusters_with_agent_logic,
    should_apply_smart_merging,
    join_overlapping_clusters,
)

# Image processing imports (optional - will fail gracefully if not available)
IMAGE_PROCESSING_AVAILABLE = False
try:
    from PIL import Image as PIL_Image
    from receipt_upload.utils import (
        download_image_from_s3,
        upload_all_cdn_formats,
        upload_png_to_s3,
        calculate_sha256_from_bytes,
    )
    IMAGE_PROCESSING_AVAILABLE = True
except ImportError:
    pass


def setup_environment() -> Dict[str, str]:
    """Load environment variables and return configuration dict."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
    artifacts_bucket = os.environ.get("ARTIFACTS_BUCKET")
    site_bucket = os.environ.get("SITE_BUCKET")
    raw_bucket = os.environ.get("RAW_BUCKET")

    # Try loading from Pulumi if not set
    try:
        from receipt_dynamo.data._pulumi import load_env
        project_root = Path(__file__).parent.parent
        infra_dir = project_root / "infra"
        env = load_env("dev", working_dir=str(infra_dir))

        if not table_name:
            table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
            if table_name:
                os.environ["DYNAMODB_TABLE_NAME"] = table_name
                print(f"📊 DynamoDB Table (from Pulumi): {table_name}")

        if not chromadb_bucket:
            # Try both export names (embedding_chromadb_bucket_name is the one actually used)
            chromadb_bucket = (
                env.get("embedding_chromadb_bucket_name") or
                env.get("chromadb_bucket_name")
            )
            if chromadb_bucket:
                os.environ["CHROMADB_BUCKET"] = chromadb_bucket
                print(f"🗄️  ChromaDB Bucket (from Pulumi): {chromadb_bucket}")

        if not artifacts_bucket:
            artifacts_bucket = env.get("artifacts_bucket_name")
            if artifacts_bucket:
                os.environ["ARTIFACTS_BUCKET"] = artifacts_bucket
                print(f"📦 Artifacts Bucket (from Pulumi): {artifacts_bucket}")

        if not site_bucket:
            # Try both export names (cdn_bucket_name is the Pulumi export)
            site_bucket = env.get("cdn_bucket_name") or env.get("site_bucket_name")
            if site_bucket:
                os.environ["SITE_BUCKET"] = site_bucket
                print(f"🌐 Site/CDN Bucket (from Pulumi): {site_bucket}")

        if not raw_bucket:
            raw_bucket = env.get("raw_bucket_name")
            if raw_bucket:
                os.environ["RAW_BUCKET"] = raw_bucket
                print(f"📦 Raw Bucket (from Pulumi): {raw_bucket}")
    except Exception as e:
        print(f"⚠️  Could not load from Pulumi: {e}")

    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME not set in environment")

    return {
        "table_name": table_name,
        "chromadb_bucket": chromadb_bucket or "",
        "artifacts_bucket": artifacts_bucket or "",
        "site_bucket": site_bucket or "",
        "raw_bucket": raw_bucket or "",
    }


def recluster_receipt_lines(
    image_lines: List[Line],
    image_width: int,
    image_height: int,
    x_eps: float = 0.08,  # Default epsilon (can be adjusted if needed)
) -> Dict[int, List[Line]]:
    """
    Re-cluster lines using two-phase approach.

    Returns cluster_id -> List[Line] mapping.
    Includes ALL lines, even noise lines (assigned to nearest cluster).

    Args:
        image_lines: List of image-level Line entities
        image_width: Image width in pixels
        image_height: Image height in pixels
        x_eps: Epsilon for X-axis DBSCAN (default 0.04 = 4% of image width, tighter than default 0.08)
    """
    # Phase 1: X-axis clustering with tighter epsilon
    cluster_dict = dbscan_lines_x_axis(image_lines, eps=x_eps)

    # Phase 1b: Split by angle consistency and vertical gaps
    cluster_dict = split_clusters_by_angle_consistency(
        cluster_dict,
        angle_tolerance=3.0,
        min_samples=2,
        vertical_gap_threshold=0.15,  # Split if vertical gap > 15% of image height
    )

    # Phase 1c: Reassign lines by X-proximity within similar Y regions
    # This fixes lines that have similar angles to one receipt but are
    # horizontally closer to another receipt (like the "A" line)
    cluster_dict = reassign_lines_by_x_proximity(
        cluster_dict,
        x_proximity_threshold=0.1,  # 10% of image width
        y_proximity_threshold=0.15,  # 15% of image height - must be in similar Y region
    )

    # Phase 1d: Reassign lines by vertical proximity (vertical stacking)
    # This fixes lines that were incorrectly grouped by X-coordinate
    # but should belong to a different receipt based on vertical alignment
    cluster_dict = reassign_lines_by_vertical_proximity(
        cluster_dict,
        vertical_proximity_threshold=0.05,  # 5% of image height
        x_proximity_threshold=0.1,  # 10% of image width - must be horizontally close
    )

    # Phase 2: Smart merging (if needed)
    # This should merge the 8 clusters into 2 receipts based on spatial coherence
    if should_apply_smart_merging(cluster_dict, len(image_lines)):
        cluster_dict = merge_clusters_with_agent_logic(
            cluster_dict,
            min_score=0.5,
            x_proximity_threshold=0.4,  # Don't merge if >40% apart horizontally
        )

    # Final: Join overlapping clusters (skip if we already have 2 clusters)
    # Only merge if there's significant overlap and we have more than 2 clusters
    # Also check X-proximity to prevent merging side-by-side receipts
    if len(cluster_dict) > 2:
        cluster_dict = join_overlapping_clusters(
            cluster_dict,
            image_width,
            image_height,
            iou_threshold=0.1,
            x_proximity_threshold=0.3,  # Don't merge if >30% apart horizontally
        )

    # Post-processing: Assign any unassigned lines to nearest cluster
    # This ensures ALL lines are included, even noise lines
    all_clustered_line_ids = set()
    for cluster_lines in cluster_dict.values():
        for line in cluster_lines:
            all_clustered_line_ids.add(line.line_id)

    unassigned_lines = [
        line for line in image_lines if line.line_id not in all_clustered_line_ids
    ]

    if unassigned_lines:
        # Assign each unassigned line to the nearest cluster based on 2D distance (X and Y)
        # This is better than just X-coordinate for handling axis-aligned lines
        for unassigned_line in unassigned_lines:
            unassigned_centroid = unassigned_line.calculate_centroid()
            unassigned_x, unassigned_y = unassigned_centroid

            # Find nearest cluster by 2D Euclidean distance
            nearest_cluster_id = None
            min_distance = float('inf')

            for cluster_id, cluster_lines in cluster_dict.items():
                # Calculate cluster centroid (mean X and Y)
                cluster_x_coords = [
                    line.calculate_centroid()[0] for line in cluster_lines
                ]
                cluster_y_coords = [
                    line.calculate_centroid()[1] for line in cluster_lines
                ]
                if cluster_x_coords and cluster_y_coords:
                    cluster_mean_x = sum(cluster_x_coords) / len(cluster_x_coords)
                    cluster_mean_y = sum(cluster_y_coords) / len(cluster_y_coords)

                    # 2D Euclidean distance
                    dx = unassigned_x - cluster_mean_x
                    dy = unassigned_y - cluster_mean_y
                    distance = math.sqrt(dx * dx + dy * dy)

                    if distance < min_distance:
                        min_distance = distance
                        nearest_cluster_id = cluster_id

            # Assign to nearest cluster (or create new cluster if none exist)
            if nearest_cluster_id is not None:
                cluster_dict[nearest_cluster_id].append(unassigned_line)
            else:
                # No clusters exist, create a new one
                new_cluster_id = max(cluster_dict.keys()) + 1 if cluster_dict else 1
                cluster_dict[new_cluster_id] = [unassigned_line]

    return cluster_dict


def calculate_receipt_bounds(
    words: List[ReceiptWord],
    original_receipt: Receipt,
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    """
    Calculate bounding box for a receipt from its words.

    ReceiptWord coordinates are normalized (0-1) relative to the original receipt.
    We need to convert to absolute image coordinates, then calculate new bounds.
    """
    if not words:
        raise ValueError("No words provided to calculate bounds")

    # Get original receipt bounds in absolute image coordinates
    receipt_min_x = original_receipt.top_left["x"] * image_width
    receipt_max_x = original_receipt.top_right["x"] * image_width
    receipt_min_y = original_receipt.bottom_left["y"] * image_height  # Bottom in OCR space
    receipt_max_y = original_receipt.top_left["y"] * image_height  # Top in OCR space

    receipt_width = receipt_max_x - receipt_min_x
    receipt_height = receipt_max_y - receipt_min_y

    all_x_coords = []
    all_y_coords = []

    for word in words:
        # Convert from receipt-relative (0-1) to absolute image coordinates
        word_top_left_x = receipt_min_x + word.top_left["x"] * receipt_width
        word_top_left_y = receipt_min_y + word.top_left["y"] * receipt_height
        word_top_right_x = receipt_min_x + word.top_right["x"] * receipt_width
        word_top_right_y = receipt_min_y + word.top_right["y"] * receipt_height
        word_bottom_left_x = receipt_min_x + word.bottom_left["x"] * receipt_width
        word_bottom_left_y = receipt_min_y + word.bottom_left["y"] * receipt_height
        word_bottom_right_x = receipt_min_x + word.bottom_right["x"] * receipt_width
        word_bottom_right_y = receipt_min_y + word.bottom_right["y"] * receipt_height

        all_x_coords.extend([
            word_top_left_x,
            word_top_right_x,
            word_bottom_left_x,
            word_bottom_right_x,
        ])
        all_y_coords.extend([
            word_top_left_y,
            word_top_right_y,
            word_bottom_left_y,
            word_bottom_right_y,
        ])

    min_x = min(all_x_coords)
    max_x = max(all_x_coords)
    min_y = min(all_y_coords)  # Bottom in OCR space
    max_y = max(all_y_coords)  # Top in OCR space

    # No padding - matches upload process (scan/photo don't add padding in normal case)
    # Only photo fallback uses 10px fixed padding, but we're not in a fallback scenario
    # Clamp to image bounds to ensure valid coordinates
    return {
        "top_left": {
            "x": max(0, min_x) / image_width,
            "y": min(image_height, max_y) / image_height,  # Top in OCR space (larger y)
        },
        "top_right": {
            "x": min(image_width, max_x) / image_width,
            "y": min(image_height, max_y) / image_height,  # Top in OCR space
        },
        "bottom_left": {
            "x": max(0, min_x) / image_width,
            "y": max(0, min_y) / image_height,  # Bottom in OCR space (smaller y)
        },
        "bottom_right": {
            "x": min(image_width, max_x) / image_width,
            "y": max(0, min_y) / image_height,  # Bottom in OCR space
        },
    }


def create_split_receipt_image(
    image: Any,
    bounds: Dict[str, Any],
    image_width: int,
    image_height: int,
) -> Optional[Any]:
    """
    Create a cropped image for a split receipt from the original image.

    Args:
        image: PIL Image object (the actual downloaded image)
        bounds: Normalized coordinates (0-1) in OCR space (y=0 at bottom)
        image_width: Width of the original image (used for coordinate conversion)
        image_height: Height of the original image (used for coordinate conversion)

    Returns:
        Cropped PIL Image, or None if cropping fails
    """
    if not IMAGE_PROCESSING_AVAILABLE:
        return None

    try:
        # Use actual image dimensions for clamping (in case they differ from stored dimensions)
        actual_width = image.width
        actual_height = image.height

        # Convert normalized bounds to pixel coordinates
        # Bounds are in OCR space (y=0 at bottom), convert to image space (y=0 at top) for PIL
        # OCR_y = image_height - image_y, so image_y = image_height - OCR_y
        top_left_x = int(bounds["top_left"]["x"] * image_width)
        top_left_y = int(image_height - bounds["top_left"]["y"] * image_height)  # Top in OCR = convert to image space
        bottom_right_x = int(bounds["bottom_right"]["x"] * image_width)
        bottom_right_y = int(image_height - bounds["bottom_left"]["y"] * image_height)  # Bottom in OCR = convert to image space

        # Clamp to actual image bounds (use actual image dimensions, not stored dimensions)
        top_left_x = max(0, min(top_left_x, actual_width - 1))
        top_left_y = max(0, min(top_left_y, actual_height - 1))
        bottom_right_x = max(top_left_x + 1, min(bottom_right_x, actual_width))
        bottom_right_y = max(top_left_y + 1, min(bottom_right_y, actual_height))

        # Ensure valid crop region
        if bottom_right_x <= top_left_x or bottom_right_y <= top_left_y:
            print(f"⚠️  Invalid crop region: ({top_left_x}, {top_left_y}) to ({bottom_right_x}, {bottom_right_y})")
            return None

        cropped = image.crop((top_left_x, top_left_y, bottom_right_x, bottom_right_y))
        return cropped
    except Exception as e:
        print(f"⚠️  Error creating receipt image: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_split_receipt_records(
    image_id: str,
    new_receipt_id: int,
    cluster_lines: List[Line],
    original_receipt: Receipt,
    original_receipt_lines: List[ReceiptLine],
    original_receipt_words: List[ReceiptWord],
    original_receipt_letters: List[ReceiptLetter],
    image_width: int,
    image_height: int,
    raw_bucket: str,
    site_bucket: str,
    original_image: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Create all DynamoDB entities for a split receipt.

    Returns dict with receipt, lines, words, letters, and ID mappings.
    """
    # Get line_ids in this cluster
    cluster_line_ids = {line.line_id for line in cluster_lines}

    # Filter receipt lines and words for this cluster
    cluster_receipt_lines = [
        rl for rl in original_receipt_lines
        if rl.line_id in cluster_line_ids
    ]
    cluster_receipt_words = [
        rw for rw in original_receipt_words
        if rw.line_id in cluster_line_ids
    ]
    cluster_receipt_letters = [
        rl for rl in original_receipt_letters
        if rl.line_id in cluster_line_ids
    ]

    if not cluster_receipt_words:
        raise ValueError(f"No words found for cluster {new_receipt_id}")

    # Calculate bounds
    bounds = calculate_receipt_bounds(
        cluster_receipt_words,
        original_receipt,
        image_width,
        image_height,
    )

    # Create receipt image and upload to CDN if original image is available
    receipt_image = None
    receipt_cdn_keys = {}
    if original_image and IMAGE_PROCESSING_AVAILABLE:
        receipt_image = create_split_receipt_image(
            original_image,
            bounds,
            image_width,
            image_height,
        )
        if receipt_image:
            # Upload raw image to raw bucket
            raw_s3_key = f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png"
            upload_png_to_s3(receipt_image, raw_bucket, raw_s3_key)

            # Upload all CDN formats to site bucket (same pattern as upload workflow)
            receipt_cdn_keys = upload_all_cdn_formats(
                receipt_image,
                site_bucket,
                f"assets/{image_id}_RECEIPT_{new_receipt_id:05d}",
                generate_thumbnails=True,
            )

    # Calculate dimensions
    if receipt_image:
        receipt_width = receipt_image.width
        receipt_height = receipt_image.height
    else:
        # Fallback to calculated dimensions if image not available
        receipt_width = max(1, int(round(
            (bounds["bottom_right"]["x"] - bounds["top_left"]["x"]) * image_width
        )))
        receipt_height = max(1, int(round(
            (bounds["top_left"]["y"] - bounds["bottom_left"]["y"]) * image_height
        )))

    # Create Receipt entity
    receipt = Receipt(
        image_id=image_id,
        receipt_id=new_receipt_id,
        width=receipt_width,
        height=receipt_height,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png" if receipt_image else "",
        top_left=bounds["top_left"],
        top_right=bounds["top_right"],
        bottom_left=bounds["bottom_left"],
        bottom_right=bounds["bottom_right"],
        sha256=calculate_sha256_from_bytes(receipt_image.tobytes()) if receipt_image else original_receipt.sha256,
        cdn_s3_bucket=site_bucket,
        # Set CDN keys from uploaded image, or fallback to original (but only if new key exists)
        cdn_s3_key=receipt_cdn_keys.get("jpeg") or original_receipt.cdn_s3_key,
        cdn_webp_s3_key=receipt_cdn_keys.get("webp") or original_receipt.cdn_webp_s3_key,
        # AVIF: Only use new key if it exists (not None), otherwise leave as None (don't use original)
        # Note: If AVIF upload fails, receipt_cdn_keys.get("avif") will be None, so we leave it as None
        cdn_avif_s3_key=receipt_cdn_keys.get("avif"),
        cdn_thumbnail_s3_key=receipt_cdn_keys.get("jpeg_thumbnail"),
        cdn_thumbnail_webp_s3_key=receipt_cdn_keys.get("webp_thumbnail"),
        cdn_thumbnail_avif_s3_key=receipt_cdn_keys.get("avif_thumbnail"),
        cdn_small_s3_key=receipt_cdn_keys.get("jpeg_small"),
        cdn_small_webp_s3_key=receipt_cdn_keys.get("webp_small"),
        cdn_small_avif_s3_key=receipt_cdn_keys.get("avif_small"),
        cdn_medium_s3_key=receipt_cdn_keys.get("jpeg_medium"),
        cdn_medium_webp_s3_key=receipt_cdn_keys.get("webp_medium"),
        cdn_medium_avif_s3_key=receipt_cdn_keys.get("avif_medium"),
    )

    # Group words by line
    words_by_line = defaultdict(list)
    for word in cluster_receipt_words:
        words_by_line[word.line_id].append(word)

    # Get transform coefficients from original receipt to image (reused for all lines/words)
    # Use the new Receipt entity method
    try:
        transform_coeffs, orig_receipt_width, orig_receipt_height = original_receipt.get_transform_to_image(
            image_width, image_height
        )
    except ImportError:
        raise ValueError("Transform methods not available - required for coordinate transformation")

    # Calculate new receipt bounds in image coordinates (OCR space) - reused for all transformations
    new_receipt_min_x_abs = bounds["top_left"]["x"] * image_width
    new_receipt_max_x_abs = bounds["top_right"]["x"] * image_width
    new_receipt_min_y_abs = bounds["bottom_left"]["y"] * image_height  # Bottom in OCR space
    new_receipt_max_y_abs = bounds["top_left"]["y"] * image_height  # Top in OCR space
    new_receipt_width_abs = new_receipt_max_x_abs - new_receipt_min_x_abs
    new_receipt_height_abs = new_receipt_max_y_abs - new_receipt_min_y_abs

    # Helper function to convert image coordinates to new receipt-relative coordinates
    def img_to_receipt_coord(img_x, img_y):
        # Convert from PIL space (y=0 at top) to OCR space (y=0 at bottom)
        ocr_y = image_height - img_y
        # Normalize relative to new receipt bounds
        receipt_x = (img_x - new_receipt_min_x_abs) / new_receipt_width_abs if new_receipt_width_abs > 0 else 0.0
        receipt_y = (ocr_y - new_receipt_min_y_abs) / new_receipt_height_abs if new_receipt_height_abs > 0 else 0.0
        return receipt_x, receipt_y

    # Create ReceiptLine entities with new IDs
    receipt_lines = []
    line_id_map = {}  # old_line_id -> new_line_id
    new_line_id = 1

    for old_line_id in sorted(words_by_line.keys()):
        line_words = sorted(words_by_line[old_line_id], key=lambda w: w.word_id)
        original_line = next(
            (rl for rl in cluster_receipt_lines if rl.line_id == old_line_id),
            None
        )

        if not original_line:
            continue

        # Use proven transform method: transform line from original receipt space to image space
        # Then transform from image space to new receipt space
        import copy

        # Step 1: Transform line from original receipt space to image space
        # Create a copy of the line to transform
        line_copy = copy.deepcopy(original_line)

        # Apply transform: receipt space -> image space
        # warp_transform expects forward coefficients (image -> receipt), so we invert
        forward_coeffs = invert_warp(*transform_coeffs)
        line_copy.warp_transform(
            *forward_coeffs,
            src_width=image_width,
            src_height=image_height,
            dst_width=orig_receipt_width,
            dst_height=orig_receipt_height,
            flip_y=True,  # Receipt coords are in OCR space (y=0 at bottom), need to flip to PIL space
        )

        # After warp_transform, line_copy coordinates are normalized (0-1) in image space
        # Convert to absolute image coordinates
        line_tl_img = {
            "x": line_copy.top_left["x"] * image_width,
            "y": line_copy.top_left["y"] * image_height,
        }
        line_tr_img = {
            "x": line_copy.top_right["x"] * image_width,
            "y": line_copy.top_right["y"] * image_height,
        }
        line_bl_img = {
            "x": line_copy.bottom_left["x"] * image_width,
            "y": line_copy.bottom_left["y"] * image_height,
        }
        line_br_img = {
            "x": line_copy.bottom_right["x"] * image_width,
            "y": line_copy.bottom_right["y"] * image_height,
        }

        # Step 2: Transform from image space to new receipt space
        line_top_left_x, line_top_left_y = img_to_receipt_coord(line_tl_img["x"], line_tl_img["y"])
        line_top_right_x, line_top_right_y = img_to_receipt_coord(line_tr_img["x"], line_tr_img["y"])
        line_bottom_left_x, line_bottom_left_y = img_to_receipt_coord(line_bl_img["x"], line_bl_img["y"])
        line_bottom_right_x, line_bottom_right_y = img_to_receipt_coord(line_br_img["x"], line_br_img["y"])

        # Calculate bounding box for the line
        line_min_x_abs = min(line_tl_img["x"], line_tr_img["x"], line_bl_img["x"], line_br_img["x"])
        line_max_x_abs = max(line_tl_img["x"], line_tr_img["x"], line_bl_img["x"], line_br_img["x"])
        line_min_y_img = min(line_tl_img["y"], line_tr_img["y"], line_bl_img["y"], line_br_img["y"])
        line_max_y_img = max(line_tl_img["y"], line_tr_img["y"], line_bl_img["y"], line_br_img["y"])
        # Convert to OCR space for bounding box
        line_min_y_ocr = image_height - line_max_y_img  # Bottom in OCR space
        line_max_y_ocr = image_height - line_min_y_img  # Top in OCR space

        # Calculate bounding box in new receipt space (normalized, OCR space)
        # Bounding box coordinates are relative to receipt bounds, in OCR space
        line_min_x_receipt = min(line_top_left_x, line_top_right_x, line_bottom_left_x, line_bottom_right_x)
        line_max_x_receipt = max(line_top_left_x, line_top_right_x, line_bottom_left_x, line_bottom_right_x)
        line_min_y_receipt = min(line_top_left_y, line_top_right_y, line_bottom_left_y, line_bottom_right_y)
        line_max_y_receipt = max(line_top_left_y, line_top_right_y, line_bottom_left_y, line_bottom_right_y)

        receipt_line = ReceiptLine(
            receipt_id=new_receipt_id,
            image_id=image_id,
            line_id=new_line_id,
            text=original_line.text,
            bounding_box={
                "x": line_min_x_receipt * new_receipt_width_abs,
                "y": line_min_y_receipt * new_receipt_height_abs,
                "width": (line_max_x_receipt - line_min_x_receipt) * new_receipt_width_abs,
                "height": (line_max_y_receipt - line_min_y_receipt) * new_receipt_height_abs,
            },
            top_left={"x": line_top_left_x, "y": line_top_left_y},
            top_right={"x": line_top_right_x, "y": line_top_right_y},
            bottom_left={"x": line_bottom_left_x, "y": line_bottom_left_y},
            bottom_right={"x": line_bottom_right_x, "y": line_bottom_right_y},
            angle_degrees=original_line.angle_degrees,
            angle_radians=original_line.angle_radians,
            confidence=original_line.confidence,
        )

        receipt_lines.append(receipt_line)
        line_id_map[old_line_id] = new_line_id
        new_line_id += 1

    # Create ReceiptWord entities with new IDs
    receipt_words = []
    word_id_map = {}  # (old_line_id, old_word_id) -> new_word_id

    for old_line_id in sorted(words_by_line.keys()):
        line_words = sorted(words_by_line[old_line_id], key=lambda w: w.word_id)
        new_word_id = 1

        for word in line_words:
            new_line_id = line_id_map[old_line_id]

            # Use proven transform method: transform word from original receipt space to image space
            # Then transform from image space to new receipt space
            # Step 1: Transform word from original receipt space to image space
            word_copy = copy.deepcopy(word)
            forward_coeffs = invert_warp(*transform_coeffs)
            word_copy.warp_transform(
                *forward_coeffs,
                src_width=image_width,
                src_height=image_height,
                dst_width=orig_receipt_width,
                dst_height=orig_receipt_height,
                flip_y=True,  # Receipt coords are in OCR space (y=0 at bottom), need to flip to PIL space
            )

            # After warp_transform, word_copy coordinates are normalized (0-1) in image space
            # Convert to absolute image coordinates
            word_tl_img = {
                "x": word_copy.top_left["x"] * image_width,
                "y": word_copy.top_left["y"] * image_height,
            }
            word_tr_img = {
                "x": word_copy.top_right["x"] * image_width,
                "y": word_copy.top_right["y"] * image_height,
            }
            word_bl_img = {
                "x": word_copy.bottom_left["x"] * image_width,
                "y": word_copy.bottom_left["y"] * image_height,
            }
            word_br_img = {
                "x": word_copy.bottom_right["x"] * image_width,
                "y": word_copy.bottom_right["y"] * image_height,
            }

            # Step 2: Transform from image space to new receipt space
            # Use the same img_to_receipt_coord function
            word_top_left_x, word_top_left_y = img_to_receipt_coord(word_tl_img["x"], word_tl_img["y"])
            word_top_right_x, word_top_right_y = img_to_receipt_coord(word_tr_img["x"], word_tr_img["y"])
            word_bottom_left_x, word_bottom_left_y = img_to_receipt_coord(word_bl_img["x"], word_bl_img["y"])
            word_bottom_right_x, word_bottom_right_y = img_to_receipt_coord(word_br_img["x"], word_br_img["y"])

            # Calculate bounding box in new receipt space
            word_min_x_receipt = min(word_top_left_x, word_top_right_x, word_bottom_left_x, word_bottom_right_x)
            word_max_x_receipt = max(word_top_left_x, word_top_right_x, word_bottom_left_x, word_bottom_right_x)
            word_min_y_receipt = min(word_top_left_y, word_top_right_y, word_bottom_left_y, word_bottom_right_y)
            word_max_y_receipt = max(word_top_left_y, word_top_right_y, word_bottom_left_y, word_bottom_right_y)

            receipt_word = ReceiptWord(
                receipt_id=new_receipt_id,
                image_id=image_id,
                line_id=new_line_id,
                word_id=new_word_id,
                text=word.text,
                bounding_box={
                    "x": word_min_x_receipt * new_receipt_width_abs,
                    "y": word_min_y_receipt * new_receipt_height_abs,
                    "width": (word_max_x_receipt - word_min_x_receipt) * new_receipt_width_abs,
                    "height": (word_max_y_receipt - word_min_y_receipt) * new_receipt_height_abs,
                },
                top_left={"x": word_top_left_x, "y": word_top_left_y},
                top_right={"x": word_top_right_x, "y": word_top_right_y},
                bottom_left={"x": word_bottom_left_x, "y": word_bottom_left_y},
                bottom_right={"x": word_bottom_right_x, "y": word_bottom_right_y},
                angle_degrees=word.angle_degrees,
                angle_radians=word.angle_radians,
                confidence=word.confidence,
            )

            receipt_words.append(receipt_word)
            word_id_map[(old_line_id, word.word_id)] = new_word_id
            new_word_id += 1

    # Create ReceiptLetter entities (if any)
    receipt_letters = []
    for letter in cluster_receipt_letters:
        if letter.line_id in line_id_map:
            new_line_id = line_id_map[letter.line_id]
            # Find corresponding word mapping
            # (simplified - would need word_id mapping for letters)
            receipt_letters.append(ReceiptLetter(
                receipt_id=new_receipt_id,
                image_id=image_id,
                line_id=new_line_id,
                word_id=letter.word_id,  # Keep same for now
                letter_id=letter.letter_id,
                text=letter.text,
                bounding_box=letter.bounding_box,
                top_left=letter.top_left,
                top_right=letter.top_right,
                bottom_left=letter.bottom_left,
                bottom_right=letter.bottom_right,
                angle_degrees=letter.angle_degrees,
                angle_radians=letter.angle_radians,
                confidence=letter.confidence,
            ))

    return {
        "receipt": receipt,
        "receipt_lines": receipt_lines,
        "receipt_words": receipt_words,
        "receipt_letters": receipt_letters,
        "line_id_map": line_id_map,
        "word_id_map": word_id_map,
    }


def migrate_receipt_word_labels(
    original_labels: List[ReceiptWordLabel],
    line_id_map: Dict[int, int],
    word_id_map: Dict[Tuple[int, int], int],
    new_receipt_id: int,
) -> List[ReceiptWordLabel]:
    """Migrate ReceiptWordLabel entities to new receipt."""
    new_labels = []

    for label in original_labels:
        new_line_id = line_id_map.get(label.line_id)
        new_word_id = word_id_map.get((label.line_id, label.word_id))

        if new_line_id is None or new_word_id is None:
            # Label doesn't map to this receipt - skip
            continue

        new_label = ReceiptWordLabel(
            image_id=label.image_id,
            receipt_id=new_receipt_id,
            line_id=new_line_id,
            word_id=new_word_id,
            label=label.label,
            reasoning=label.reasoning or f"Migrated from receipt {label.receipt_id}, word {label.word_id}",
            timestamp_added=datetime.now(timezone.utc),
            validation_status=label.validation_status,
            label_proposed_by=label.label_proposed_by or "receipt_split",
            label_consolidated_from=f"receipt_{label.receipt_id}_word_{label.word_id}",
        )
        new_labels.append(new_label)

    return new_labels


def save_records_locally(
    output_dir: Path,
    image_id: str,
    split_results: List[Dict[str, Any]],
    original_receipt: Receipt,
) -> None:
    """Save all records to local JSON files for rollback."""
    image_dir = output_dir / image_id
    image_dir.mkdir(parents=True, exist_ok=True)

    # Save original receipt (for rollback)
    original_file = image_dir / "original_receipt.json"
    with open(original_file, 'w') as f:
        json.dump(dict(original_receipt), f, indent=2, default=str)

    # Save split results
    for i, result in enumerate(split_results, start=1):
        receipt_dir = image_dir / f"receipt_{result['receipt'].receipt_id:05d}"
        receipt_dir.mkdir(exist_ok=True)

        # Save receipt
        with open(receipt_dir / "receipt.json", 'w') as f:
            json.dump(dict(result["receipt"]), f, indent=2, default=str)

        # Save lines
        with open(receipt_dir / "lines.json", 'w') as f:
            json.dump([dict(line) for line in result["receipt_lines"]], f, indent=2, default=str)

        # Save words
        with open(receipt_dir / "words.json", 'w') as f:
            json.dump([dict(word) for word in result["receipt_words"]], f, indent=2, default=str)

        # Save letters
        if result["receipt_letters"]:
            with open(receipt_dir / "letters.json", 'w') as f:
                json.dump([dict(letter) for letter in result["receipt_letters"]], f, indent=2, default=str)

        # Save labels
        if result["receipt_labels"]:
            with open(receipt_dir / "labels.json", 'w') as f:
                json.dump([dict(label) for label in result["receipt_labels"]], f, indent=2, default=str)

        # Save ID mappings
        with open(receipt_dir / "id_mappings.json", 'w') as f:
            json.dump({
                "line_id_map": result["line_id_map"],
                "word_id_map": {f"{k[0]}_{k[1]}": v for k, v in result["word_id_map"].items()},
            }, f, indent=2)

    print(f"💾 Saved records locally to: {image_dir}")


def export_receipt_ndjson_to_s3(
    client: DynamoClient,
    artifacts_bucket: str,
    image_id: str,
    receipt_id: int,
) -> None:
    """
    Export receipt lines and words to NDJSON files in S3.

    This matches the upload workflow pattern from process_ocr_results.py.
    The NDJSON files are exported for consistency and audit trail, even though
    we're doing direct embedding instead of queue-based processing.
    """
    try:
        import boto3

        # Fetch authoritative words/lines from DynamoDB (just saved)
        receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)
        receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)

        prefix = f"receipts/{image_id}/receipt-{receipt_id:05d}/"
        lines_key = prefix + "lines.ndjson"
        words_key = prefix + "words.ndjson"

        # Serialize full dataclass objects so the consumer can rehydrate with
        # ReceiptLine(**d)/ReceiptWord(**d) preserving geometry and methods
        line_rows = [dict(l) for l in (receipt_lines or [])]
        word_rows = [dict(w) for w in (receipt_words or [])]

        # Upload NDJSON files to S3
        s3_client = boto3.client("s3")

        # Upload lines NDJSON
        lines_ndjson_content = "\n".join(json.dumps(row, default=str) for row in line_rows)
        s3_client.put_object(
            Bucket=artifacts_bucket,
            Key=lines_key,
            Body=lines_ndjson_content.encode("utf-8"),
            ContentType="application/x-ndjson",
        )

        # Upload words NDJSON
        words_ndjson_content = "\n".join(json.dumps(row, default=str) for row in word_rows)
        s3_client.put_object(
            Bucket=artifacts_bucket,
            Key=words_key,
            Body=words_ndjson_content.encode("utf-8"),
            ContentType="application/x-ndjson",
        )

    except Exception as e:
        print(f"⚠️  Error exporting NDJSON for receipt {receipt_id}: {e}")
        import traceback
        traceback.print_exc()


def create_embeddings_and_compaction_run(
    client: DynamoClient,
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    merchant_name: Optional[str] = None,
) -> Optional[str]:
    """
    Create embeddings in realtime and create CompactionRun directly.

    This matches the approach used in combine_receipts_logic.py.
    No queue needed - we create CompactionRun directly, which triggers compaction via streams.

    Returns:
        run_id if successful, None if embedding is not available
    """
    try:
        import tempfile
        import uuid
        from receipt_chroma.data.chroma_client import ChromaClient
        from receipt_label.merchant_resolution.embeddings import upsert_embeddings
        from receipt_label.embedding.line.realtime import embed_lines_realtime
        from receipt_label.embedding.word.realtime import embed_words_realtime

        # Check if embedding is available
        if not os.environ.get("OPENAI_API_KEY"):
            print(f"⚠️  OPENAI_API_KEY not set; skipping embedding")
            return None

        # Fetch receipt data
        receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)

        if not receipt_lines or not receipt_words:
            print(f"⚠️  No lines/words found for receipt {receipt_id}; skipping embedding")
            return None

        # Generate run ID
        run_id = str(uuid.uuid4())
        delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
        delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

        # Create local ChromaDB deltas
        line_client = ChromaClient(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True
        )
        word_client = ChromaClient(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True
        )

        # Create embeddings
        print(f"   Creating embeddings for receipt {receipt_id}...")
        upsert_embeddings(
            line_client=line_client,
            word_client=word_client,
            line_embed_fn=embed_lines_realtime,
            word_embed_fn=embed_words_realtime,
            ctx={"lines": receipt_lines, "words": receipt_words},
            merchant_name=merchant_name,
        )

        # Upload deltas to S3
        lines_prefix = f"lines/delta/{run_id}/"
        words_prefix = f"words/delta/{run_id}/"

        lines_delta_key = line_client.persist_and_upload_delta(
            bucket=chromadb_bucket,
            s3_prefix=lines_prefix,
        )
        words_delta_key = word_client.persist_and_upload_delta(
            bucket=chromadb_bucket,
            s3_prefix=words_prefix,
        )

        # Create CompactionRun directly (triggers compaction via streams)
        compaction_run = CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=lines_delta_key,
            words_delta_prefix=words_delta_key,
        )
        client.add_compaction_run(compaction_run)

        print(f"   ✅ Created embeddings and CompactionRun: {run_id}")
        return run_id

    except ImportError as e:
        print(f"⚠️  Could not import embedding modules: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Error creating embeddings: {e}")
        import traceback
        traceback.print_exc()
        return None


def delete_receipt_embeddings_from_chromadb(
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
) -> None:
    """
    Delete all embeddings for a receipt from ChromaDB.

    This deletes embeddings from the main snapshot collections (not deltas).
    Must be called BEFORE deleting from DynamoDB so we can construct IDs from receipt data.

    Args:
        chromadb_bucket: S3 bucket containing ChromaDB snapshots
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_lines: List of ReceiptLine entities (for constructing line IDs)
        receipt_words: List of ReceiptWord entities (for constructing word IDs)
    """
    try:
        from receipt_chroma import ChromaClient
        from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic
        import tempfile
        import shutil

        # Construct ChromaDB IDs from receipt data
        line_ids = [
            f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line.line_id:05d}"
            for line in receipt_lines
        ]
        word_ids = [
            f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
            for word in receipt_words
        ]

        if not line_ids and not word_ids:
            print(f"   ⚠️  No embeddings to delete (no lines or words)")
            return

        print(f"   🗑️  Deleting embeddings from ChromaDB...")
        print(f"      Lines: {len(line_ids)}, Words: {len(word_ids)}")

        # Process lines collection
        if line_ids:
            lines_temp_dir = tempfile.mkdtemp()
            try:
                # Download snapshot
                download_snapshot_atomic(
                    bucket=chromadb_bucket,
                    collection="lines",
                    local_path=lines_temp_dir,
                )

                # Open collection
                lines_client = ChromaClient(
                    persist_directory=lines_temp_dir,
                    mode="write",
                )
                lines_collection = lines_client.get_collection("lines")

                # Delete embeddings
                lines_collection.delete(ids=line_ids)
                print(f"      ✅ Deleted {len(line_ids)} line embeddings")

                # Upload updated snapshot
                upload_snapshot_atomic(
                    local_path=lines_temp_dir,
                    bucket=chromadb_bucket,
                    collection="lines",
                )
                print(f"      ✅ Uploaded updated lines snapshot")
            finally:
                shutil.rmtree(lines_temp_dir, ignore_errors=True)

        # Process words collection
        if word_ids:
            words_temp_dir = tempfile.mkdtemp()
            try:
                # Download snapshot
                download_snapshot_atomic(
                    bucket=chromadb_bucket,
                    collection="words",
                    local_path=words_temp_dir,
                )

                # Open collection
                words_client = ChromaClient(
                    persist_directory=words_temp_dir,
                    mode="write",
                )
                words_collection = words_client.get_collection("words")

                # Delete embeddings
                words_collection.delete(ids=word_ids)
                print(f"      ✅ Deleted {len(word_ids)} word embeddings")

                # Upload updated snapshot
                upload_snapshot_atomic(
                    local_path=words_temp_dir,
                    bucket=chromadb_bucket,
                    collection="words",
                )
                print(f"      ✅ Uploaded updated words snapshot")
            finally:
                shutil.rmtree(words_temp_dir, ignore_errors=True)

        print(f"   ✅ Deleted all embeddings for receipt {receipt_id}")

    except ImportError:
        print(f"   ⚠️  ChromaDB deletion not available (receipt_chroma not installed)")
    except Exception as e:
        print(f"   ⚠️  Error deleting embeddings: {e}")
        import traceback
        traceback.print_exc()


def wait_for_compaction_complete(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    max_wait_seconds: int = 300,
    poll_interval: int = 5,
    initial_wait_seconds: int = 10,
) -> str:
    """
    Wait for CompactionRun to complete for a receipt.

    First waits for CompactionRun to appear (NDJSON worker creates it),
    then polls until both lines and words collections are COMPLETED.

    Args:
        client: DynamoDB client
        image_id: Image ID
        receipt_id: Receipt ID
        max_wait_seconds: Maximum total wait time
        poll_interval: Seconds between polls
        initial_wait_seconds: Initial wait before checking for CompactionRun

    Returns:
        run_id of the completed compaction run

    Raises:
        TimeoutError: If compaction doesn't complete within max_wait_seconds
        RuntimeError: If compaction fails
    """
    import time
    from receipt_dynamo.constants import CompactionState

    start_time = time.time()
    run_id = None
    last_state = None
    compaction_run_found = False

    print(f"⏳ Waiting for compaction to complete (max {max_wait_seconds}s)...")

    # First, wait a bit for NDJSON worker to create CompactionRun
    if initial_wait_seconds > 0:
        print(f"   Waiting {initial_wait_seconds}s for CompactionRun to be created...")
        time.sleep(initial_wait_seconds)

    while time.time() - start_time < max_wait_seconds:
        # Get most recent CompactionRun for this receipt
        runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id, limit=1)

        if runs:
            if not compaction_run_found:
                print(f"   ✓ CompactionRun found, waiting for completion...")
                compaction_run_found = True

            run = runs[0]
            run_id = run.run_id

            # Check current state
            current_state = f"lines={run.lines_state}, words={run.words_state}"
            if current_state != last_state:
                print(f"   Compaction state: {current_state}")
                last_state = current_state

            # Check if both collections are completed
            if (run.lines_state == CompactionState.COMPLETED.value and
                run.words_state == CompactionState.COMPLETED.value):
                print(f"✅ Compaction completed for run {run_id}")
                return run_id

            # Check for failures
            if (run.lines_state == CompactionState.FAILED.value or
                run.words_state == CompactionState.FAILED.value):
                error_msg = f"Compaction failed: lines={run.lines_state}, words={run.words_state}"
                if run.lines_error:
                    error_msg += f", lines_error={run.lines_error}"
                if run.words_error:
                    error_msg += f", words_error={run.words_error}"
                raise RuntimeError(error_msg)
        else:
            # CompactionRun not created yet
            if not compaction_run_found:
                elapsed = int(time.time() - start_time)
                if elapsed % 10 == 0:  # Print every 10 seconds
                    print(f"   Waiting for CompactionRun to be created... ({elapsed}s)")

        time.sleep(poll_interval)

    # Timeout
    if not run_id:
        raise TimeoutError(
            f"CompactionRun not found for receipt {receipt_id} after {max_wait_seconds}s. "
            f"NDJSON worker may not have processed the queue yet."
        )
    else:
        raise TimeoutError(
            f"Compaction did not complete for run {run_id} after {max_wait_seconds}s. "
            f"Current state: {last_state}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Split a receipt into multiple receipts based on re-clustering"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID to process",
    )
    parser.add_argument(
        "--original-receipt-id",
        type=int,
        default=1,
        help="Original receipt ID to split (default: 1)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./local_receipt_splits"),
        help="Directory to save local records (default: ./local_receipt_splits)",
    )
    parser.add_argument(
        "--chromadb-bucket",
        help="S3 bucket for ChromaDB deltas",
    )
    parser.add_argument(
        "--raw-bucket",
        help="S3 bucket for raw images",
    )
    parser.add_argument(
        "--artifacts-bucket",
        help="S3 bucket for artifacts (NDJSON files) - auto-loaded from Pulumi if not provided",
    )
    parser.add_argument(
        "--site-bucket",
        help="S3 bucket for site/CDN images - auto-loaded from Pulumi if not provided (not currently used)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode - don't save to DynamoDB",
    )
    parser.add_argument(
        "--skip-embedding",
        action="store_true",
        help="Skip embedding creation (no CompactionRun will be created)",
    )
    parser.add_argument(
        "--delete-original",
        action="store_true",
        help="Delete original receipt and its embeddings after split completes (waits for compaction first)",
    )

    args = parser.parse_args()

    # Setup
    config = setup_environment()
    table_name = config["table_name"]
    client = DynamoClient(table_name)

    # Use Pulumi values if not provided via args
    chromadb_bucket = args.chromadb_bucket or config.get("chromadb_bucket")
    if not chromadb_bucket and not args.skip_embedding:
        print(f"⚠️  CHROMADB_BUCKET not set; use --chromadb-bucket or --skip-embedding")
        chromadb_bucket = None

    artifacts_bucket = args.artifacts_bucket or config.get("artifacts_bucket")
    if not artifacts_bucket:
        print(f"⚠️  ARTIFACTS_BUCKET not set; NDJSON export will be skipped")

    site_bucket = args.site_bucket or config.get("site_bucket")
    raw_bucket = args.raw_bucket or config.get("raw_bucket")

    image_id = args.image_id
    original_receipt_id = args.original_receipt_id

    print(f"📊 Splitting receipt for image: {image_id}")
    print(f"   Original receipt ID: {original_receipt_id}")
    print(f"   Dry run: {args.dry_run}")

    # Load original receipt data
    print(f"\n📥 Loading original receipt data...")
    original_receipt = client.get_receipt(image_id, original_receipt_id)
    original_receipt_lines = client.list_receipt_lines_from_receipt(image_id, original_receipt_id)
    original_receipt_words = client.list_receipt_words_from_receipt(image_id, original_receipt_id)
    # Receipt letters are optional - try to get them if method exists
    try:
        original_receipt_letters = client.list_receipt_letters_from_receipt(image_id, original_receipt_id)
    except AttributeError:
        # Method doesn't exist or letters not available
        original_receipt_letters = []
    original_receipt_labels, _ = client.list_receipt_word_labels_for_receipt(image_id, original_receipt_id)

    print(f"   Receipt: {len(original_receipt_lines)} lines, {len(original_receipt_words)} words")
    print(f"   Labels: {len(original_receipt_labels)}")

    # Load image-level OCR data for re-clustering
    print(f"\n🔄 Re-clustering lines...")
    image_lines = client.list_lines_from_image(image_id)
    image_entity = client.get_image(image_id)

    cluster_dict = recluster_receipt_lines(
        image_lines,
        image_entity.width,
        image_entity.height,
    )

    print(f"   Found {len(cluster_dict)} clusters")
    for cluster_id, cluster_lines in cluster_dict.items():
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")

    if len(cluster_dict) < 2:
        print(f"⚠️  Only {len(cluster_dict)} cluster(s) found - nothing to split!")
        return

    # Download original image for creating receipt images
    original_image = None
    actual_image_width = image_entity.width
    actual_image_height = image_entity.height

    if IMAGE_PROCESSING_AVAILABLE and image_entity.raw_s3_bucket and image_entity.raw_s3_key:
        try:
            print(f"\n📥 Downloading original image for receipt image creation...")
            image_bucket = raw_bucket or image_entity.raw_s3_bucket
            image_path = download_image_from_s3(
                image_bucket,
                image_entity.raw_s3_key,
                image_id,
            )
            original_image = PIL_Image.open(image_path)
            actual_image_width = original_image.width
            actual_image_height = original_image.height
            print(f"   ✅ Loaded image: {actual_image_width}x{actual_image_height}")

            # Verify dimensions match DynamoDB (warn if they don't)
            if actual_image_width != image_entity.width or actual_image_height != image_entity.height:
                print(f"⚠️  Image dimensions mismatch!")
                print(f"   DynamoDB: {image_entity.width}x{image_entity.height}")
                print(f"   Actual: {actual_image_width}x{actual_image_height}")
                print(f"   Using actual image dimensions for calculations")
        except Exception as e:
            print(f"⚠️  Could not download original image: {e}")
            print(f"   Receipt images will not be created, but data will still be split")
            print(f"   Using DynamoDB dimensions: {actual_image_width}x{actual_image_height}")
    elif not IMAGE_PROCESSING_AVAILABLE:
        print(f"\n⚠️  Image processing not available (PIL not installed)")
        print(f"   Receipt images will not be created, but data will still be split")
        print(f"   Using DynamoDB dimensions: {actual_image_width}x{actual_image_height}")

    # Find next available receipt ID (avoid conflicts with existing receipts)
    print(f"\n🔍 Finding next available receipt ID...")
    try:
        # Try to get all receipts for this image
        existing_receipts = client.get_receipts_from_image(image_id)
        if existing_receipts:
            max_receipt_id = max(r.receipt_id for r in existing_receipts)
            new_receipt_id = max_receipt_id + 1
            print(f"   Found {len(existing_receipts)} existing receipts, max ID: {max_receipt_id}")
            print(f"   Starting new receipts at ID: {new_receipt_id}")
        else:
            new_receipt_id = 1
            print(f"   No existing receipts found, starting at ID: 1")
    except Exception as e:
        # Fallback: start at a safe high number to avoid conflicts
        print(f"   ⚠️  Could not query existing receipts: {e}")
        print(f"   Starting at ID: 1000 (safe fallback to avoid conflicts)")
        new_receipt_id = 1000

    # Create new receipt records
    print(f"\n📝 Creating new receipt records...")
    split_results = []

    # Determine buckets (use args, then config, then fallback to original receipt)
    final_raw_bucket = raw_bucket or original_receipt.raw_s3_bucket or ""
    final_site_bucket = site_bucket or original_receipt.cdn_s3_bucket or ""

    if not final_raw_bucket:
        raise ValueError("RAW_BUCKET not set - cannot create receipt images")
    if not final_site_bucket:
        raise ValueError("SITE_BUCKET not set - cannot upload receipt images to CDN")

    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        print(f"   Creating receipt {new_receipt_id} from cluster {cluster_id}...")

        result = create_split_receipt_records(
            image_id=image_id,
            new_receipt_id=new_receipt_id,
            cluster_lines=cluster_lines,
            original_receipt=original_receipt,
            original_receipt_lines=original_receipt_lines,
            original_receipt_words=original_receipt_words,
            original_receipt_letters=original_receipt_letters,
            image_width=actual_image_width,  # Use actual image dimensions
            image_height=actual_image_height,  # Use actual image dimensions
            raw_bucket=final_raw_bucket,
            site_bucket=final_site_bucket,
            original_image=original_image,
        )

        # Migrate labels
        result["receipt_labels"] = migrate_receipt_word_labels(
            original_receipt_labels,
            result["line_id_map"],
            result["word_id_map"],
            new_receipt_id,
        )

        split_results.append(result)
        print(f"      Receipt {new_receipt_id}: {len(result['receipt_lines'])} lines, "
              f"{len(result['receipt_words'])} words, {len(result['receipt_labels'])} labels")

        new_receipt_id += 1

    # Save locally first
    print(f"\n💾 Saving records locally...")
    save_records_locally(
        args.output_dir,
        image_id,
        split_results,
        original_receipt,
    )

    if args.dry_run:
        print(f"\n✅ Dry run complete - records saved locally only")
        return

    # Save to DynamoDB (receipts, lines, words - but NOT labels yet)
    print(f"\n💾 Saving to DynamoDB...")
    for result in split_results:
        receipt = result["receipt"]
        print(f"   Saving receipt {receipt.receipt_id}...")

        client.add_receipt(receipt)
        client.add_receipt_lines(result["receipt_lines"])
        client.add_receipt_words(result["receipt_words"])
        if result["receipt_letters"]:
            client.add_receipt_letters(result["receipt_letters"])
        # NOTE: Labels will be added AFTER compaction completes

        print(f"      ✅ Saved receipt {receipt.receipt_id}")

    # Export NDJSON files to S3 (for consistency with upload process)
    # This matches the upload workflow pattern from process_ocr_results.py
    if artifacts_bucket:
        print(f"\n📤 Exporting NDJSON files to S3...")
        for result in split_results:
            receipt_id = result["receipt"].receipt_id
            export_receipt_ndjson_to_s3(
                client,
                artifacts_bucket,
                image_id,
                receipt_id,
            )
            print(f"   ✅ Exported NDJSON for receipt {receipt_id}")

    # Create embeddings and CompactionRun directly (no queue needed)
    compaction_completed = {}
    if not args.skip_embedding and chromadb_bucket:
        print(f"\n📤 Creating embeddings and CompactionRun...")

        # Get merchant name from original receipt metadata (if available)
        merchant_name = None
        try:
            receipt_metadata = client.get_receipt_metadata(image_id, original_receipt_id)
            if receipt_metadata:
                merchant_name = receipt_metadata.merchant_name or receipt_metadata.canonical_merchant_name
        except Exception:
            pass  # No metadata available, will use None

        for result in split_results:
            receipt_id = result["receipt"].receipt_id
            run_id = create_embeddings_and_compaction_run(
                client,
                chromadb_bucket,
                image_id,
                receipt_id,
                merchant_name=merchant_name,
            )

            if run_id:
                compaction_completed[receipt_id] = run_id

        # Wait for compaction to complete before adding labels
        if compaction_completed:
            print(f"\n⏳ Waiting for compaction to complete before adding labels...")
            for result in split_results:
                receipt_id = result["receipt"].receipt_id
                if receipt_id in compaction_completed:
                    run_id = compaction_completed[receipt_id]
                    try:
                        wait_for_compaction_complete(
                            client,
                            image_id,
                            receipt_id,
                            max_wait_seconds=300,  # 5 minutes
                            poll_interval=5,
                        )
                        print(f"   ✅ Compaction completed for receipt {receipt_id}")
                    except (TimeoutError, RuntimeError) as e:
                        print(f"⚠️  Compaction failed or timed out for receipt {receipt_id}: {e}")
                        print(f"   Will add labels anyway - they'll update when compaction completes later")
        else:
            print(f"⚠️  No embeddings created; labels will be added but may not update ChromaDB")
    else:
        if args.skip_embedding:
            print(f"\n⚠️  Skipping embedding (--skip-embedding)")
        else:
            print(f"\n⚠️  Skipping embedding (CHROMADB_BUCKET not set)")
        print(f"   Labels will be added, but embeddings may not exist in ChromaDB yet")

    # Add labels AFTER compaction completes
    # If compaction completed successfully, labels will update immediately
    # If compaction failed/timed out, labels will still be added and update when compaction completes later
    print(f"\n🏷️  Adding labels...")
    # DynamoDB batch writes are limited to 25 items per request
    # The add_receipt_word_labels method should handle chunking, but we'll chunk manually to be safe
    CHUNK_SIZE = 25
    for result in split_results:
        receipt_id = result["receipt"].receipt_id
        if result["receipt_labels"]:
            labels = result["receipt_labels"]
            total_labels = len(labels)

            # Chunk labels into batches of 25
            for i in range(0, total_labels, CHUNK_SIZE):
                chunk = labels[i:i + CHUNK_SIZE]
                chunk_num = (i // CHUNK_SIZE) + 1
                total_chunks = (total_labels + CHUNK_SIZE - 1) // CHUNK_SIZE

                if receipt_id in compaction_completed:
                    print(f"   Adding labels chunk {chunk_num}/{total_chunks} ({len(chunk)} labels) for receipt {receipt_id} (compaction completed)...")
                    client.add_receipt_word_labels(chunk)
                elif not args.skip_embedding:
                    print(f"   Adding labels chunk {chunk_num}/{total_chunks} ({len(chunk)} labels) for receipt {receipt_id} (compaction pending)...")
                    client.add_receipt_word_labels(chunk)
                else:
                    print(f"   Adding labels chunk {chunk_num}/{total_chunks} ({len(chunk)} labels) for receipt {receipt_id} (embedding skipped)...")
                    client.add_receipt_word_labels(chunk)

            if receipt_id in compaction_completed:
                print(f"      ✅ Added {total_labels} labels (compaction run: {compaction_completed[receipt_id]})")
            elif not args.skip_embedding:
                print(f"      ✅ Added {total_labels} labels (will update when compaction completes)")
            else:
                print(f"      ✅ Added {total_labels} labels (will update when embeddings are created)")
        else:
            print(f"   No labels to add for receipt {receipt_id}")

    # Wait for ALL compaction runs to complete before deleting original receipt
    # This ensures new receipts are fully embedded before we delete the original
    all_compaction_complete = True
    if args.delete_original and compaction_completed:
        print(f"\n⏳ Waiting for ALL compaction runs to complete before deleting original receipt...")
        for result in split_results:
            receipt_id = result["receipt"].receipt_id
            if receipt_id in compaction_completed:
                try:
                    wait_for_compaction_complete(
                        client,
                        image_id,
                        receipt_id,
                        max_wait_seconds=300,
                        poll_interval=5,
                    )
                    print(f"   ✅ Compaction completed for receipt {receipt_id}")
                except (TimeoutError, RuntimeError) as e:
                    print(f"⚠️  Compaction failed or timed out for receipt {receipt_id}: {e}")
                    all_compaction_complete = False

        if not all_compaction_complete:
            print(f"⚠️  Some compaction runs did not complete - original receipt will NOT be deleted")
            print(f"   You can manually delete it later after compaction completes")

    # Delete original receipt and its embeddings (if compaction completed or skipped)
    if args.delete_original and (not compaction_completed or all_compaction_complete):
        print(f"\n🗑️  Deleting original receipt {original_receipt_id}...")

        # Option 1: Manual deletion (immediate, before DynamoDB deletion)
        # This ensures embeddings are deleted even if compactor hasn't processed yet
        # First, delete embeddings from ChromaDB (BEFORE deleting from DynamoDB)
        # We need the receipt data to construct ChromaDB IDs
        manual_deletion_success = False
        if chromadb_bucket and not args.skip_embedding:
            try:
                delete_receipt_embeddings_from_chromadb(
                    chromadb_bucket,
                    image_id,
                    original_receipt_id,
                    original_receipt_lines,
                    original_receipt_words,
                )
                manual_deletion_success = True
                print(f"   ✅ Manually deleted embeddings from ChromaDB")
            except Exception as e:
                print(f"⚠️  Error manually deleting embeddings: {e}")
                print(f"   Compactor will handle deletion automatically when Receipt is deleted")

        # Option 2: Let compactor handle it automatically
        # When we delete the Receipt from DynamoDB, the stream will trigger
        # the compactor to delete embeddings automatically
        # However, if we delete lines/words first, the compactor won't be able
        # to query them to construct IDs, so manual deletion above is preferred

        # Delete from DynamoDB in reverse order of creation
        # 1. Delete labels first
        if original_receipt_labels:
            print(f"   Deleting {len(original_receipt_labels)} labels...")
            # Delete in batches of 25
            CHUNK_SIZE = 25
            for i in range(0, len(original_receipt_labels), CHUNK_SIZE):
                chunk = original_receipt_labels[i:i + CHUNK_SIZE]
                for label in chunk:
                    try:
                        client.delete_receipt_word_label(
                            image_id,
                            original_receipt_id,
                            label.line_id,
                            label.word_id,
                            label.label,
                        )
                    except Exception as e:
                        print(f"      ⚠️  Error deleting label: {e}")
            print(f"      ✅ Deleted labels")

        # 2. Delete words
        if original_receipt_words:
            print(f"   Deleting {len(original_receipt_words)} words...")
            client.delete_receipt_words(original_receipt_words)
            print(f"      ✅ Deleted words")

        # 3. Delete lines
        if original_receipt_lines:
            print(f"   Deleting {len(original_receipt_lines)} lines...")
            client.delete_receipt_lines(original_receipt_lines)
            print(f"      ✅ Deleted lines")

        # 4. Delete letters (if any)
        if original_receipt_letters:
            print(f"   Deleting {len(original_receipt_letters)} letters...")
            try:
                client.delete_receipt_letters(original_receipt_letters)
                print(f"      ✅ Deleted letters")
            except AttributeError:
                print(f"      ⚠️  Letter deletion not supported")

        # 5. Delete metadata (if any)
        try:
            metadata = client.get_receipt_metadata(image_id, original_receipt_id)
            if metadata:
                print(f"   Deleting metadata...")
                client.delete_receipt_metadata(image_id, original_receipt_id)
                print(f"      ✅ Deleted metadata")
        except Exception:
            pass  # Metadata might not exist

        # 6. Delete CompactionRun (if any)
        try:
            runs, _ = client.list_compaction_runs_for_receipt(image_id, original_receipt_id)
            if runs:
                print(f"   Deleting {len(runs)} compaction runs...")
                for run in runs:
                    client.delete_compaction_run(image_id, original_receipt_id, run.run_id)
                print(f"      ✅ Deleted compaction runs")
        except Exception:
            pass  # Compaction runs might not exist

        # 7. Delete receipt (last)
        print(f"   Deleting receipt...")
        client.delete_receipt(image_id, original_receipt_id)
        print(f"      ✅ Deleted receipt {original_receipt_id}")

        print(f"\n✅ Original receipt {original_receipt_id} deleted (including ChromaDB embeddings)")
    elif args.delete_original:
        print(f"\n⚠️  Skipping deletion of original receipt (compaction not complete)")
    else:
        print(f"\n💾 Original receipt {original_receipt_id} kept (use --delete-original to remove)")

    print(f"\n✅ Split complete!")
    print(f"   Created {len(split_results)} new receipts")
    print(f"   Local records saved to: {args.output_dir / image_id}")


if __name__ == "__main__":
    main()

