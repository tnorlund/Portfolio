#!/usr/bin/env python3
"""
Reprocess PHOTO image receipts with improved corner detection.

This script:
1. Deletes existing receipts for a PHOTO image (triggers Chroma cleanup via stream)
2. Re-runs clustering and receipt corner detection with the new algorithm
3. Creates new receipt entities and warped images
4. Queues REFINEMENT OCR jobs to SQS for Mac OCR processing

The original Lines/Words/Letters are preserved since they're correct.

Usage:
    python scripts/reprocess_photo_receipts.py \
        --stack dev \
        --image-id 2c453d65-edae-4aeb-819a-612b27d99894 \
        [--dry-run]

    # Reprocess multiple images:
    python scripts/reprocess_photo_receipts.py \
        --stack dev \
        --image-ids 2c453d65-edae-4aeb-819a-612b27d99894 8362b52a-60a1-4534-857f-028dd531976e
"""

import argparse
import json
import logging
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image as PIL_Image
from PIL.Image import Resampling, Transform

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Image, Line, OCRJob, Receipt, Word

from receipt_upload.cluster import dbscan_lines
from receipt_upload.geometry import (
    compute_rotated_bounding_box_corners,
    convex_hull,
    find_perspective_coeffs,
)
from receipt_upload.utils import (
    calculate_sha256_from_bytes,
    download_image_from_s3,
    send_message_to_sqs,
    upload_all_cdn_formats,
    upload_png_to_s3,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def delete_receipt_and_children(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    dry_run: bool = False,
) -> bool:
    """Delete a receipt and all its child records.

    This triggers the DynamoDB stream which will clean up Chroma embeddings.
    """
    logger.info(f"Deleting receipt {receipt_id} for image {image_id}...")

    try:
        receipt = client.get_receipt(image_id, receipt_id)
    except Exception as e:
        logger.warning(f"Receipt not found: {e}")
        return False

    # Fetch child records
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id) or []
    words = client.list_receipt_words_from_receipt(image_id, receipt_id) or []
    try:
        labels, _ = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )
    except Exception as e:
        logger.debug(f"No labels found for receipt: {e}")
        labels = []
    try:
        letters = client.list_receipt_letters_from_image_and_receipt(
            image_id, receipt_id
        )
    except Exception as e:
        logger.debug(f"No letters found for receipt: {e}")
        letters = []

    logger.info(
        f"  Found: {len(lines)} lines, {len(words)} words, "
        f"{len(labels)} labels, {len(letters)} letters"
    )

    if dry_run:
        logger.info("  [DRY RUN] Would delete these records")
        return True

    # Delete in order: labels -> words -> lines -> letters -> metadata -> receipt
    if labels:
        try:
            client.delete_receipt_word_labels(labels)
            logger.info("  ✓ Deleted labels")
        except Exception as e:
            logger.warning(f"  Failed to delete labels: {e}")
            for lbl in labels:
                try:
                    client.delete_receipt_word_label(lbl)
                except Exception as label_err:
                    logger.debug(f"Failed to delete individual label: {label_err}")

    if words:
        client.delete_receipt_words(words)
        logger.info("  ✓ Deleted words")

    if lines:
        client.delete_receipt_lines(lines)
        logger.info("  ✓ Deleted lines")

    if letters:
        try:
            client.delete_receipt_letters(letters)
            logger.info("  ✓ Deleted letters")
        except Exception as e:
            logger.warning(f"  Failed to delete letters: {e}")

    # Delete metadata (best effort)
    try:
        md = client.get_receipt_metadata(image_id, receipt_id)
        if md:
            client.delete_receipt_metadata(image_id, receipt_id)
            logger.info("  ✓ Deleted metadata")
    except Exception as md_err:
        logger.debug(f"Failed to delete metadata: {md_err}")

    # Delete compaction runs (best effort)
    try:
        runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
        for r in runs:
            client.delete_compaction_run(r)
        if runs:
            logger.info(f"  ✓ Deleted {len(runs)} compaction runs")
    except Exception as e:
        logger.debug(f"Failed to delete compaction runs: {e}")

    # Delete REFINEMENT OCR jobs for this receipt
    try:
        jobs = client.list_ocr_jobs(image_id)
        refinement_jobs = [
            j for j in jobs
            if j.job_type == OCRJobType.REFINEMENT.value
            and j.receipt_id == receipt_id
        ]
        for job in refinement_jobs:
            client.delete_ocr_job(job)
        if refinement_jobs:
            logger.info(f"  ✓ Deleted {len(refinement_jobs)} REFINEMENT OCR jobs")
    except Exception as e:
        logger.warning(f"  Failed to delete OCR jobs: {e}")

    # Finally, delete the receipt (this triggers stream -> Chroma cleanup)
    try:
        client.delete_receipt(receipt)
        logger.info(f"  ✓ Deleted receipt {receipt_id}")
        return True
    except Exception as e:
        logger.error(f"  ✗ Error deleting receipt: {e}")
        return False


def reprocess_photo_receipts(
    client: DynamoClient,
    image_id: str,
    raw_bucket: str,
    site_bucket: str,
    ocr_job_queue_url: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Reprocess a PHOTO image with improved corner detection.

    Returns:
        Dict with processing results
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"REPROCESSING IMAGE: {image_id}")
    logger.info(f"{'='*60}")

    # Step 1: Get image details
    try:
        image = client.get_image(image_id)
    except Exception as e:
        logger.error(f"Image not found: {e}")
        return {"success": False, "error": str(e)}

    img_type = image.image_type.value if hasattr(image.image_type, 'value') else image.image_type
    if img_type != "PHOTO":
        logger.error(f"Image is not a PHOTO (type: {img_type})")
        return {"success": False, "error": f"Not a PHOTO image: {img_type}"}

    # Step 2: Get existing receipts and delete them
    receipts = client.get_receipts_from_image(image_id)
    logger.info(f"Found {len(receipts)} existing receipts to delete")

    for receipt in receipts:
        delete_receipt_and_children(
            client, image_id, receipt.receipt_id, dry_run
        )

    if dry_run:
        logger.info("\n[DRY RUN] Stopping before reprocessing")
        return {"success": True, "dry_run": True, "deleted_receipts": len(receipts)}

    # Step 3: Get original Lines and Words from DynamoDB
    lines = client.list_lines_from_image(image_id)
    if not lines:
        logger.error("No lines found for image")
        return {"success": False, "error": "No lines found"}

    all_words = []
    for line in lines:
        words = client.list_words_from_line(image_id, line.line_id)
        all_words.extend(words)

    logger.info(f"Retrieved {len(lines)} lines and {len(all_words)} words")

    # Step 4: Download the original image
    logger.info("Downloading original image from S3...")
    image_path = download_image_from_s3(
        s3_bucket=image.raw_s3_bucket,
        s3_key=image.raw_s3_key,
        image_id=image_id,
    )
    pil_image = PIL_Image.open(image_path)
    logger.info(f"Image size: {pil_image.width}x{pil_image.height}")

    # Step 5: Cluster lines using DBSCAN
    avg_diagonal_length = sum(
        [line.calculate_diagonal_length() for line in lines]
    ) / len(lines)
    clusters = dbscan_lines(lines, eps=avg_diagonal_length * 2, min_samples=10)
    clusters = {k: v for k, v in clusters.items() if k != -1}

    if not clusters:
        logger.error("No valid clusters found")
        return {"success": False, "error": "No clusters found"}

    logger.info(f"Found {len(clusters)} receipt clusters")

    # Step 6: Process each cluster
    successful_receipts = 0
    for cluster_id, cluster_lines in clusters.items():
        try:
            if len(cluster_lines) < 3:
                logger.debug(f"Skipping cluster {cluster_id}: insufficient lines")
                continue

            line_ids = [line.line_id for line in cluster_lines]
            cluster_words = [w for w in all_words if w.line_id in line_ids]

            if len(cluster_words) < 4:
                logger.debug(f"Skipping cluster {cluster_id}: insufficient words")
                continue

            # Get word corners for hull
            all_word_corners = []
            for word in cluster_words:
                corners = word.calculate_corners(
                    width=pil_image.width,
                    height=pil_image.height,
                    flip_y=True,
                )
                all_word_corners.extend([(int(x), int(y)) for x, y in corners])

            if len(all_word_corners) < 4:
                continue

            hull = convex_hull(all_word_corners)
            if len(hull) < 4:
                continue

            # Find top and bottom lines using centroid Y
            def get_line_centroid_y(line):
                return (
                    line.top_left["y"] + line.top_right["y"] +
                    line.bottom_left["y"] + line.bottom_right["y"]
                ) / 4

            sorted_lines = sorted(
                cluster_lines, key=get_line_centroid_y, reverse=True
            )
            top_line = sorted_lines[0]
            bottom_line = sorted_lines[-1]

            # Check for upside-down receipt
            angles = [line.angle_degrees for line in cluster_lines]
            avg_angle = sum(angles) / len(angles) if angles else 0.0
            if abs(avg_angle) > 90:
                top_line, bottom_line = bottom_line, top_line

            # Get corners from top/bottom lines
            top_line_corners = top_line.calculate_corners(
                width=pil_image.width, height=pil_image.height, flip_y=True
            )
            bottom_line_corners = bottom_line.calculate_corners(
                width=pil_image.width, height=pil_image.height, flip_y=True
            )

            # Compute receipt corners using NEW algorithm
            receipt_box_corners = compute_rotated_bounding_box_corners(
                hull, top_line_corners, bottom_line_corners
            )

            # Validate corners
            min_x = min(corner[0] for corner in receipt_box_corners)
            max_x = max(corner[0] for corner in receipt_box_corners)
            min_y = min(corner[1] for corner in receipt_box_corners)
            max_y = max(corner[1] for corner in receipt_box_corners)

            if (max_x - min_x) < 10 or (max_y - min_y) < 10:
                logger.warning(f"Skipping cluster {cluster_id}: degenerate rectangle")
                continue

            # Check for duplicate corners
            has_duplicates = False
            for i in range(4):
                for j in range(i + 1, 4):
                    if math.dist(receipt_box_corners[i], receipt_box_corners[j]) < 1.0:
                        has_duplicates = True
                        break
            if has_duplicates:
                logger.warning(f"Skipping cluster {cluster_id}: duplicate corners")
                continue

            # Compute warped image dimensions
            top_w = math.dist(receipt_box_corners[0], receipt_box_corners[1])
            bottom_w = math.dist(receipt_box_corners[3], receipt_box_corners[2])
            source_width = (top_w + bottom_w) / 2.0

            left_h = math.dist(receipt_box_corners[0], receipt_box_corners[3])
            right_h = math.dist(receipt_box_corners[1], receipt_box_corners[2])
            source_height = (left_h + right_h) / 2.0

            if source_width < 10 or source_height < 10:
                logger.warning(f"Skipping cluster {cluster_id}: too small")
                continue

            warped_width = round(source_width)
            warped_height = round(source_height)
            dst_corners = [
                (0.0, 0.0),
                (float(warped_width - 1), 0.0),
                (float(warped_width - 1), float(warped_height - 1)),
                (0.0, float(warped_height - 1)),
            ]

            # Compute perspective transform
            try:
                transform_coeffs = find_perspective_coeffs(
                    src_points=receipt_box_corners, dst_points=dst_corners
                )
            except ValueError as e:
                logger.warning(f"Perspective transform failed for cluster {cluster_id}: {e}")
                continue

            # Create warped image
            warped_img = pil_image.transform(
                (warped_width, warped_height),
                Transform.PERSPECTIVE,
                transform_coeffs,
                resample=Resampling.BICUBIC,
            )

            logger.info(
                f"Cluster {cluster_id}: corners computed, warped size {warped_width}x{warped_height}"
            )

            # Upload warped image to S3
            upload_png_to_s3(
                warped_img,
                raw_bucket,
                f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
            )

            # Upload CDN formats
            receipt_cdn_keys = upload_all_cdn_formats(
                warped_img,
                site_bucket,
                f"assets/{image_id}_RECEIPT_{cluster_id:05d}",
                generate_thumbnails=True,
            )

            # Convert corners to normalized coordinates
            top_left = {
                "x": receipt_box_corners[0][0] / pil_image.width,
                "y": receipt_box_corners[0][1] / pil_image.height,
            }
            top_right = {
                "x": receipt_box_corners[1][0] / pil_image.width,
                "y": receipt_box_corners[1][1] / pil_image.height,
            }
            bottom_right = {
                "x": receipt_box_corners[2][0] / pil_image.width,
                "y": receipt_box_corners[2][1] / pil_image.height,
            }
            bottom_left = {
                "x": receipt_box_corners[3][0] / pil_image.width,
                "y": receipt_box_corners[3][1] / pil_image.height,
            }

            # Create Receipt entity
            receipt = Receipt(
                image_id=image_id,
                receipt_id=cluster_id,
                width=warped_img.width,
                height=warped_img.height,
                timestamp_added=datetime.now(timezone.utc),
                raw_s3_bucket=raw_bucket,
                raw_s3_key=f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
                top_left=top_left,
                top_right=top_right,
                bottom_left=bottom_left,
                bottom_right=bottom_right,
                sha256=calculate_sha256_from_bytes(warped_img.tobytes()),
                cdn_s3_bucket=site_bucket,
                cdn_s3_key=receipt_cdn_keys["jpeg"],
                cdn_webp_s3_key=receipt_cdn_keys["webp"],
                cdn_avif_s3_key=receipt_cdn_keys["avif"],
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
            client.add_receipt(receipt)
            logger.info(f"  ✓ Created receipt {cluster_id}")

            # Create and queue REFINEMENT OCR job
            new_ocr_job = OCRJob(
                image_id=image_id,
                job_id=str(uuid.uuid4()),
                s3_bucket=raw_bucket,
                s3_key=f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                status=OCRStatus.PENDING,
                job_type=OCRJobType.REFINEMENT,
                receipt_id=cluster_id,
            )
            client.add_ocr_job(new_ocr_job)

            # Queue to SQS for Mac OCR processing
            send_message_to_sqs(
                ocr_job_queue_url,
                json.dumps({
                    "job_id": new_ocr_job.job_id,
                    "image_id": new_ocr_job.image_id,
                }),
            )
            logger.info(f"  ✓ Queued REFINEMENT job for receipt {cluster_id}")

            successful_receipts += 1

        except Exception as e:
            logger.exception(f"Error processing cluster {cluster_id}: {e}")
            continue

    # Cleanup
    if image_path.exists():
        image_path.unlink()

    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETED: {successful_receipts} receipts created and queued")
    logger.info(f"{'='*60}")

    return {
        "success": True,
        "image_id": image_id,
        "receipts_created": successful_receipts,
        "receipts_deleted": len(receipts),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reprocess PHOTO image receipts with improved corner detection"
    )
    parser.add_argument(
        "--stack",
        choices=["dev", "prod"],
        required=True,
        help="Pulumi stack to use",
    )
    parser.add_argument(
        "--image-id",
        type=str,
        help="Single image ID to reprocess",
    )
    parser.add_argument(
        "--image-ids",
        type=str,
        nargs="+",
        help="Multiple image IDs to reprocess",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without making changes",
    )
    args = parser.parse_args()

    # Validate arguments
    if not args.image_id and not args.image_ids:
        parser.error("Either --image-id or --image-ids is required")

    image_ids = []
    if args.image_id:
        image_ids.append(args.image_id)
    if args.image_ids:
        image_ids.extend(args.image_ids)

    # Get configuration from Pulumi
    logger.info(f"Getting {args.stack.upper()} configuration from Pulumi...")
    env = load_env(env=args.stack)

    table_name = env.get("dynamodb_table_name")
    raw_bucket = env.get("raw_bucket_name")
    site_bucket = env.get("cdn_bucket_name")  # CDN bucket for warped receipt images
    ocr_job_queue_url = env.get("ocr_job_queue_url")

    if not all([table_name, raw_bucket, site_bucket, ocr_job_queue_url]):
        logger.error("Missing required configuration from Pulumi")
        logger.error(f"  table_name: {table_name}")
        logger.error(f"  raw_bucket: {raw_bucket}")
        logger.error(f"  site_bucket: {site_bucket}")
        logger.error(f"  ocr_job_queue_url: {ocr_job_queue_url}")
        sys.exit(1)

    logger.info("Configuration:")
    logger.info(f"  DynamoDB table: {table_name}")
    logger.info(f"  Raw S3 bucket: {raw_bucket}")
    logger.info(f"  Site S3 bucket: {site_bucket}")
    logger.info(f"  OCR Job Queue: {ocr_job_queue_url}")

    if args.dry_run:
        logger.info("\n[DRY RUN MODE] No changes will be made\n")

    client = DynamoClient(table_name)

    # Process each image
    results = []
    for image_id in image_ids:
        result = reprocess_photo_receipts(
            client=client,
            image_id=image_id,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            ocr_job_queue_url=ocr_job_queue_url,
            dry_run=args.dry_run,
        )
        results.append(result)

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for result in results:
        if result.get("success"):
            print(f"✓ {result.get('image_id', 'unknown')}: "
                  f"{result.get('receipts_created', 0)} receipts created")
        else:
            print(f"✗ {result.get('image_id', 'unknown')}: {result.get('error', 'unknown error')}")

    if args.dry_run:
        print("\n[DRY RUN] No changes were made")
    else:
        print("\nREFINEMENT jobs have been queued.")
        print("Run your Mac OCR worker to process the cropped receipt images.")
        print("After processing, embeddings will be created automatically.")


if __name__ == "__main__":
    main()
