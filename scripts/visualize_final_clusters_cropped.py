#!/usr/bin/env python3
"""
Visualize the final clustered receipts for an image, showing each cluster as a cropped receipt.

This uses the same affine transformation process as the receipt upload process,
ensuring the cropped receipts are perspective-corrected (not axis-aligned).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Line,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
)
from scripts.split_receipt import recluster_receipt_lines, setup_environment

try:
    from PIL import Image as PIL_Image
    from PIL import ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL not available, install pillow")
    sys.exit(1)

import copy

import boto3

from receipt_upload.cluster import reorder_box_points

# Import geometry functions from receipt_upload (same as scan.py)
from receipt_upload.geometry import box_points, invert_affine, min_area_rect

# Import transform utilities
try:
    from receipt_upload.geometry.transformations import invert_warp

    TRANSFORM_AVAILABLE = True
except ImportError:
    TRANSFORM_AVAILABLE = False


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    """Download image from S3."""
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(response["Body"])


def get_cluster_color(cluster_id: int, total_clusters: int) -> str:
    """Get a bright, high-contrast color for each cluster."""
    colors = [
        "#FF0000",  # Bright Red
        "#00FF00",  # Bright Green
        "#0000FF",  # Bright Blue
        "#FF00FF",  # Magenta
        "#00FFFF",  # Cyan
        "#FFFF00",  # Yellow
        "#FF8000",  # Orange
        "#8000FF",  # Purple
    ]
    return colors[cluster_id % len(colors)]


def transform_receipt_line_to_image_line(
    receipt_line: ReceiptLine,
    original_receipt,
    image_width: int,
    image_height: int,
) -> Line:
    """
    Transform a ReceiptLine from receipt-relative space to image space,
    creating a Line entity with coordinates in image space.

    Args:
        receipt_line: ReceiptLine entity (coordinates in receipt-relative space, normalized 0-1)
        original_receipt: Receipt entity (to get transform coefficients)
        image_width: Image width in pixels
        image_height: Image height in pixels

    Returns:
        Line entity with coordinates in image space (normalized 0-1, OCR space)
    """
    if not TRANSFORM_AVAILABLE:
        raise ImportError("Transform utilities not available")

    # Step 1: Get transform from receipt space to image space
    try:
        from receipt_agent.utils.receipt_coordinates import (
            get_receipt_to_image_transform,
        )

        transform_coeffs, orig_receipt_width, orig_receipt_height = (
            get_receipt_to_image_transform(
                original_receipt, image_width, image_height
            )
        )
    except ImportError:
        # Fallback: try receipt_agent package
        try:
            from receipt_agent.receipt_agent.utils.receipt_coordinates import (
                get_receipt_to_image_transform,
            )

            transform_coeffs, orig_receipt_width, orig_receipt_height = (
                get_receipt_to_image_transform(
                    original_receipt, image_width, image_height
                )
            )
        except ImportError:
            raise ImportError(
                "Could not import get_receipt_to_image_transform - required for coordinate transformation"
            )

    # Step 2: Transform receipt line corners to image space
    entity_copy = copy.deepcopy(receipt_line)
    forward_coeffs = invert_warp(*transform_coeffs)
    entity_copy.warp_transform(
        *forward_coeffs,
        src_width=image_width,
        src_height=image_height,
        dst_width=orig_receipt_width,
        dst_height=orig_receipt_height,
        flip_y=True,
    )

    # Step 3: Get corners from transformed entity
    # After warp_transform with flip_y=True, inverse_perspective_transform does a final Y-flip
    # (see entity_mixins.py line 1325-1328), so coordinates are already in OCR space (y=0 at bottom)
    # We can use them directly as OCR space coordinates for the Line entity
    corners_img = {}
    for corner_name in [
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    ]:
        corner = getattr(entity_copy, corner_name)
        # After warp_transform with flip_y=True, coordinates are normalized 0-1 in OCR space (y=0 at bottom)
        # Use them directly - no conversion needed
        corners_img[corner_name] = {
            "x": corner["x"],  # Already normalized 0-1
            "y": corner[
                "y"
            ],  # Already normalized 0-1 in OCR space (y=0 at bottom)
        }

    # Step 4: Create Line entity with image-space coordinates
    import math

    tl = corners_img["top_left"]
    tr = corners_img["top_right"]
    dx = tr["x"] - tl["x"]
    dy = tr["y"] - tl["y"]  # Note: in OCR space, y increases upward
    angle_radians = math.atan2(dy, dx)
    angle_degrees = math.degrees(angle_radians)

    # Create Line entity
    line = Line(
        line_id=receipt_line.line_id,
        image_id=receipt_line.image_id,
        text=receipt_line.text,
        bounding_box={
            "x": min(corners_img[c]["x"] for c in corners_img) * image_width,
            "y": min(corners_img[c]["y"] for c in corners_img) * image_height,
            "width": (
                max(corners_img[c]["x"] for c in corners_img)
                - min(corners_img[c]["x"] for c in corners_img)
            )
            * image_width,
            "height": (
                max(corners_img[c]["y"] for c in corners_img)
                - min(corners_img[c]["y"] for c in corners_img)
            )
            * image_height,
        },
        top_left=corners_img["top_left"],
        top_right=corners_img["top_right"],
        bottom_left=corners_img["bottom_left"],
        bottom_right=corners_img["bottom_right"],
        angle_degrees=angle_degrees,
        angle_radians=angle_radians,
        confidence=receipt_line.confidence,
    )

    return line


def get_line_corners_image_coords(line: Line, img_width: int, img_height: int):
    """Get line corners in image coordinate space (y=0 at top)."""
    # Line corners are in OCR space (normalized 0-1, y=0 at bottom)
    # Convert to image space (pixels, y=0 at top)
    tl = (
        line.top_left["x"] * img_width,
        (1.0 - line.top_left["y"]) * img_height,  # Flip Y
    )
    tr = (
        line.top_right["x"] * img_width,
        (1.0 - line.top_right["y"]) * img_height,  # Flip Y
    )
    br = (
        line.bottom_right["x"] * img_width,
        (1.0 - line.bottom_right["y"]) * img_height,  # Flip Y
    )
    bl = (
        line.bottom_left["x"] * img_width,
        (1.0 - line.bottom_left["y"]) * img_height,  # Flip Y
    )
    return [tl, tr, br, bl]


def calculate_receipt_bounds_from_lines(
    cluster_lines: list[Line], img_width: int, img_height: int
) -> dict:
    """
    Calculate receipt bounds using the same process as receipt_upload.

    This uses min_area_rect to find the minimum area rectangle, then calculates
    an affine transform to warp the image (same as scan.py).
    """
    # 1) Collect cluster points in absolute image coordinates
    points_abs = []
    for line in cluster_lines:
        # Line corners are in OCR space (normalized 0-1, y=0 at bottom)
        # Convert to image space (pixels, y=0 at top)
        for corner in [
            line.top_left,
            line.top_right,
            line.bottom_left,
            line.bottom_right,
        ]:
            x_abs = corner["x"] * img_width
            y_abs = (1 - corner["y"]) * img_height  # flip Y: OCR -> PIL
            points_abs.append((x_abs, y_abs))

    if not points_abs:
        return None

    # 2) Use min_area_rect to find the bounding box
    (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
    w = int(round(rw))
    h = int(round(rh))
    if w < 1 or h < 1:
        return None

    # The Receipts are always portrait, so we need to rotate the bounding
    # box if it's landscape.
    if w > h:
        # Rotate the bounding box by -90° so the final warp is 'portrait'
        angle_deg -= 90.0
        # Swap the width & height so the final image is portrait
        w, h = h, w
        # Also swap rw, rh so our box_points() call below is correct
        rw, rh = rh, rw

    # Recompute the four corners for the (possibly) adjusted angle & size
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    box_4_ordered = reorder_box_points(box_4)

    # For convenience, name the corners we need for the transform
    src_tl = box_4_ordered[0]
    src_tr = box_4_ordered[1]
    src_bl = box_4_ordered[3]

    # 3) Build the Pillow transform (dst->src) matrix
    if w > 1:
        a_i = (src_tr[0] - src_tl[0]) / (w - 1)
        d_i = (src_tr[1] - src_tl[1]) / (w - 1)
    else:
        a_i = d_i = 0.0

    if h > 1:
        b_i = (src_bl[0] - src_tl[0]) / (h - 1)
        e_i = (src_bl[1] - src_tl[1]) / (h - 1)
    else:
        b_i = e_i = 0.0

    c_i = src_tl[0]
    f_i = src_tl[1]

    return {
        "width": w,
        "height": h,
        "affine_transform": (a_i, b_i, c_i, d_i, e_i, f_i),
        "box_4_ordered": box_4_ordered,
    }


def visualize_final_clusters_cropped(
    image_id: str,
    output_dir: Path,
    raw_bucket: str,
    receipt_id: Optional[int] = None,
    x_eps: Optional[float] = None,
    angle_tolerance: Optional[float] = None,
    vertical_gap_threshold: Optional[float] = None,
    reassign_x_proximity_threshold: Optional[float] = None,
    reassign_y_proximity_threshold: Optional[float] = None,
    vertical_proximity_threshold: Optional[float] = None,
    vertical_x_proximity_threshold: Optional[float] = None,
    merge_x_proximity_threshold: Optional[float] = None,
    merge_min_score: Optional[float] = None,
    join_iou_threshold: Optional[float] = None,
):
    """
    Create visualization of final clustered receipts, showing each cluster as a cropped receipt.

    Args:
        image_id: Image ID to visualize
        output_dir: Output directory for visualizations
        raw_bucket: S3 bucket name for raw images
        receipt_id: Optional receipt ID. If provided, uses image-level OCR for clustering
                    (to get correct clusters) but uses receipt-level OCR entities for
                    visualization (more accurate coordinates and text). This hybrid approach
                    gives the best of both worlds: correct clustering + accurate visualization.
    """
    env = setup_environment()
    table_name = env.get("table_name")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME not set")

    client = DynamoClient(table_name)

    # Load data
    image_entity = client.get_image(image_id)

    # Strategy: Use image-level OCR for clustering (gets correct clusters),
    # then use cluster bounds to find ReceiptLines, ReceiptWords, and ReceiptLetters in original receipt space
    original_receipt = None
    receipt_lines = []  # All ReceiptLines from original receipt
    receipt_words = []  # All ReceiptWords from original receipt
    receipt_letters = []  # All ReceiptLetters from original receipt

    if receipt_id is not None:
        print(
            f"📊 Using image-level OCR for clustering, receipt-level OCR entities for visualization"
        )
        original_receipt = client.get_receipt(image_id, receipt_id)
        receipt_lines = client.list_receipt_lines_from_receipt(
            image_id, receipt_id
        )
        receipt_words = client.list_receipt_words_from_receipt(
            image_id, receipt_id
        )
        # Note: ReceiptLetters are typically accessed via ReceiptWords
        # For now, we'll skip loading them separately
        receipt_letters = []

        if receipt_lines or receipt_words or receipt_letters:
            print(
                f"   Loaded {len(receipt_lines)} ReceiptLines, "
                f"{len(receipt_words)} ReceiptWords, "
                f"{len(receipt_letters)} ReceiptLetters from original receipt"
            )
        else:
            print(
                f"⚠️  No ReceiptOCR entities found for receipt_id={receipt_id}"
            )

    # Always use image-level OCR for clustering (gets correct 2 clusters)
    print(f"📊 Clustering with image-level OCR...")
    image_lines = client.list_lines_from_image(image_id)
    print(f"   Found {len(image_lines)} image-level lines")

    # Get final clusters (this branch's recluster_receipt_lines only accepts x_eps)
    eps_val = x_eps if x_eps is not None else 0.08
    print(f"   Clustering x_eps: {eps_val}")
    cluster_dict = recluster_receipt_lines(
        image_lines, image_entity.width, image_entity.height, x_eps=eps_val
    )

    # Download original image
    original_image = None
    s3_key = (
        image_entity.raw_s3_key
        if image_entity.raw_s3_key
        else f"raw/{image_id}.png"
    )
    try:
        original_image = get_image_from_s3(raw_bucket, s3_key)
        print(f"✅ Loaded image from raw S3: {s3_key}")
    except Exception as e:
        print(f"⚠️  Could not load image from raw S3 key '{s3_key}': {e}")
        # Try CDN image as fallback
        if image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
            try:
                print(f"   Trying CDN image: {image_entity.cdn_s3_key}")
                original_image = get_image_from_s3(
                    image_entity.cdn_s3_bucket, image_entity.cdn_s3_key
                )
                print(f"✅ Loaded image from CDN")
            except Exception as e2:
                print(f"⚠️  Could not load image from CDN: {e2}")
        if original_image is None:
            print(f"   Creating blank image for visualization...")
            original_image = PIL_Image.new(
                "RGB", (image_entity.width, image_entity.height), "white"
            )

    img_width, img_height = original_image.size

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Process each cluster
    print(f"\n📊 Final clusters for {image_id}:")
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict))
        print(
            f"   Cluster {cluster_id}: {len(cluster_lines)} lines (color: {color})"
        )

        # Calculate receipt bounds using same process as receipt_upload
        bounds = calculate_receipt_bounds_from_lines(
            cluster_lines, img_width, img_height
        )
        if not bounds:
            print(f"      ⚠️  No bounds - skipping")
            continue

        w = bounds["width"]
        h = bounds["height"]
        affine_transform = bounds["affine_transform"]

        # Warp the image using the affine transform (same as scan.py)
        warped_image = original_image.transform(
            (w, h),
            PIL_Image.AFFINE,  # type: ignore[attr-defined]
            affine_transform,
            resample=PIL_Image.BICUBIC,  # type: ignore[attr-defined]
        )

        # Create visualization on warped image
        img = warped_image.copy()
        draw = ImageDraw.Draw(img)

        # Draw each line in this cluster
        # NOTE: Always use image-level OCR coordinates for visualization
        # Receipt-level OCR coordinates are relative to the original receipt, not the split cluster
        # The image-level OCR coordinates are already in image space and work correctly with the cluster's affine transform
        drawn_count = 0
        skipped_count = 0
        for line in cluster_lines:
            # Always use image-level line coordinates for drawing
            # (Receipt-level OCR is only used for text accuracy in the export, not for coordinates)
            corners_img = get_line_corners_image_coords(
                line, img_width, img_height
            )

            # Transform corners to warped receipt space using the inverse affine transform
            # The affine transform maps (receipt_x, receipt_y) -> (image_x, image_y)
            # We need the inverse: (image_x, image_y) -> (receipt_x, receipt_y)
            a_i, b_i, c_i, d_i, e_i, f_i = affine_transform
            a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(
                a_i, b_i, c_i, d_i, e_i, f_i
            )

            # Store inverse affine transform for ReceiptWords transformation
            cluster_inverse_affine = (a_f, b_f, c_f, d_f, e_f, f_f)

            # Apply inverse transform to each corner
            corners_warped = []
            for img_x, img_y in corners_img:
                # Inverse affine transform: receipt = A^-1 * (image - c)
                receipt_x = a_f * img_x + b_f * img_y + c_f
                receipt_y = d_f * img_x + e_f * img_y + f_f
                corners_warped.append((receipt_x, receipt_y))

            # Only draw if corners are valid (not all zeros/NaN)
            if corners_warped and all(
                isinstance(x, (int, float))
                and isinstance(y, (int, float))
                and not (x != x or y != y)  # Check for NaN
                for x, y in corners_warped
            ):
                draw.polygon(corners_warped, outline=color, width=3)
                drawn_count += 1
            else:
                print(
                    f"      ⚠️  Line {line.line_id}: Invalid corners, skipping"
                )
                skipped_count += 1

        if skipped_count > 0:
            print(
                f"      ⚠️  Skipped {skipped_count} lines, drew {drawn_count} lines"
            )

        # Now find ReceiptLines, ReceiptWords, and ReceiptLetters that fall within this cluster's bounds
        # Step 1: Transform cluster bounds from image space to original receipt coordinate space
        cluster_receipt_lines = []
        cluster_receipt_words = []
        cluster_receipt_letters = []
        if receipt_id is not None and original_receipt:
            # Get cluster bounds in image space (box_4_ordered is in PIL space, absolute pixels)
            box_4_ordered = bounds[
                "box_4_ordered"
            ]  # [(x, y), ...] in PIL space

            # Transform these corners to original receipt coordinate space
            # We need to use the inverse of the receipt's transform_to_image
            try:
                # Try the correct import path
                try:
                    from receipt_agent.utils.receipt_coordinates import (
                        get_receipt_to_image_transform,
                    )
                except ImportError:
                    from receipt_agent.receipt_agent.utils.receipt_coordinates import (
                        get_receipt_to_image_transform,
                    )
                import copy

                from receipt_upload.geometry.transformations import invert_warp

                # Get transform from receipt space to image space
                transform_coeffs, orig_receipt_width, orig_receipt_height = (
                    get_receipt_to_image_transform(
                        original_receipt, img_width, img_height
                    )
                )

                # Invert to get image -> receipt transform
                image_to_receipt_coeffs = invert_warp(*transform_coeffs)
                a, b, c, d, e, f, g, h = image_to_receipt_coeffs

                # Helper function to apply perspective transform manually
                def apply_perspective_transform(x, y, coeffs):
                    """Apply perspective transform: (x, y) -> (x_new, y_new)"""
                    a, b, c, d, e, f, g, h = coeffs
                    denom = 1 + g * x + h * y
                    if abs(denom) < 1e-10:
                        return None, None
                    x_new = (a * x + b * y + c) / denom
                    y_new = (d * x + e * y + f) / denom
                    return x_new, y_new

                # Transform cluster bounds corners from image space to receipt space
                # box_4_ordered is in PIL space (absolute pixels)
                cluster_bounds_in_receipt_space = []
                for img_x, img_y in box_4_ordered:
                    # Convert to OCR space (y=0 at bottom) in absolute pixels
                    x_ocr_abs = img_x
                    y_ocr_abs = img_height - img_y  # Flip Y: PIL -> OCR

                    # Apply perspective transform to get receipt space (absolute pixels)
                    receipt_x_abs, receipt_y_abs = apply_perspective_transform(
                        x_ocr_abs, y_ocr_abs, image_to_receipt_coeffs
                    )

                    if receipt_x_abs is None or receipt_y_abs is None:
                        continue

                    # Normalize to receipt space (0-1)
                    receipt_x_norm = receipt_x_abs / orig_receipt_width
                    receipt_y_norm = receipt_y_abs / orig_receipt_height

                    # Receipt space is in OCR space (y=0 at bottom), so no flip needed
                    cluster_bounds_in_receipt_space.append(
                        (receipt_x_norm, receipt_y_norm)
                    )

                # Step 2: Find ReceiptLines, ReceiptWords, and ReceiptLetters that fall within cluster bounds
                # Simple bounding box check (using min/max of cluster bounds)
                if cluster_bounds_in_receipt_space:
                    min_x = min(c[0] for c in cluster_bounds_in_receipt_space)
                    max_x = max(c[0] for c in cluster_bounds_in_receipt_space)
                    min_y = min(c[1] for c in cluster_bounds_in_receipt_space)
                    max_y = max(c[1] for c in cluster_bounds_in_receipt_space)

                    # Find ReceiptLines
                    cluster_receipt_lines = []
                    if receipt_lines:
                        for line in receipt_lines:
                            centroid = line.calculate_centroid()
                            line_x, line_y = centroid[0], centroid[1]
                            if (
                                min_x <= line_x <= max_x
                                and min_y <= line_y <= max_y
                            ):
                                cluster_receipt_lines.append(line)

                    # Find ReceiptWords
                    cluster_receipt_words = []
                    if receipt_words:
                        for word in receipt_words:
                            centroid = word.calculate_centroid()
                            word_x, word_y = centroid[0], centroid[1]
                            if (
                                min_x <= word_x <= max_x
                                and min_y <= word_y <= max_y
                            ):
                                cluster_receipt_words.append(word)

                    # Find ReceiptLetters
                    cluster_receipt_letters = []
                    if receipt_letters:
                        for letter in receipt_letters:
                            centroid = letter.calculate_centroid()
                            letter_x, letter_y = centroid[0], centroid[1]
                            if (
                                min_x <= letter_x <= max_x
                                and min_y <= letter_y <= max_y
                            ):
                                cluster_receipt_letters.append(letter)

                    print(
                        f"      Found {len(cluster_receipt_lines)} ReceiptLines, "
                        f"{len(cluster_receipt_words)} ReceiptWords, "
                        f"{len(cluster_receipt_letters)} ReceiptLetters within cluster bounds"
                    )

                    # Step 3: Transform ReceiptOCR entities using two-step approach:
                    # Original receipt space -> Image space -> Warped receipt space
                    # We know the clusters are correct in image space, so we use the cluster bounds
                    # in receipt space to find the correct ReceiptLines, ReceiptWords, and ReceiptLetters
                    # Then transform them using the proven two-step approach

                    # Get inverse affine transform (image space -> warped receipt space)
                    # This was already computed above for the lines (a_f, b_f, c_f, d_f, e_f, f_f)

                    # Debug: Check for specific words
                    search_terms = ["2.88", "10.98", "5.97", "2.28"]
                    debug_words_list = [
                        w
                        for w in cluster_receipt_words
                        if any(term in (w.text or "") for term in search_terms)
                    ]
                    if debug_words_list:
                        print(
                            f"      🔍 Found {len(debug_words_list)} debug words in cluster: {[w.text for w in debug_words_list]}"
                        )

                    # Try direct transform from original receipt space to warped receipt space
                    # This should preserve the angle correctly
                    # Use absolute pixel coordinates to avoid issues with normalized 0-1 range
                    direct_transform_available = False
                    direct_transform_coeffs = None

                    if len(cluster_bounds_in_receipt_space) == 4:
                        try:
                            from receipt_upload.geometry.transformations import (
                                find_perspective_coeffs,
                                invert_warp,
                            )

                            # Source: cluster bounds in original receipt space (normalized 0-1, OCR space, y=0 at bottom)
                            # Destination: cluster bounds in warped receipt space (normalized 0-1, PIL space, y=0 at top)
                            # Warped receipt space is a rectangle: TL=(0,0), TR=(w,0), BR=(w,h), BL=(0,h) in pixels
                            # But we'll work in normalized coordinates: TL=(0,0), TR=(1,0), BR=(1,1), BL=(0,1)
                            # Note: In PIL space, y=0 is at top, so we need to flip Y from OCR space
                            # Use absolute pixel coordinates to avoid issues with normalized 0-1 range
                            # Source: cluster bounds in original receipt space (absolute pixels, OCR space, y=0 at bottom)
                            # Convert from normalized to absolute pixels
                            src_points_receipt_abs_ocr = [
                                (
                                    x * orig_receipt_width,
                                    y * orig_receipt_height,
                                )
                                for x, y in cluster_bounds_in_receipt_space
                            ]

                            # Convert from OCR space to PIL space (flip Y)
                            src_points_receipt_abs_pil = [
                                (x, orig_receipt_height - y)
                                for x, y in src_points_receipt_abs_ocr
                            ]

                            # Destination: warped receipt space rectangle in PIL space (absolute pixels)
                            # Order: [TL, TR, BR, BL] to match reorder_box_points output
                            dst_points_warped_abs_pil = [
                                (
                                    0.0,
                                    0.0,
                                ),  # TL (PIL space, absolute pixels, y=0 at top)
                                (
                                    float(w),
                                    0.0,
                                ),  # TR (PIL space, absolute pixels, y=0 at top)
                                (
                                    float(w),
                                    float(h),
                                ),  # BR (PIL space, absolute pixels, y=h at bottom)
                                (
                                    0.0,
                                    float(h),
                                ),  # BL (PIL space, absolute pixels, y=h at bottom)
                            ]

                            # find_perspective_coeffs returns coefficients that map dst -> src (inverse transform)
                            # We need the forward transform (src -> dst), so we swap src and dst
                            # Then invert to get the forward transform
                            inverse_coeffs = find_perspective_coeffs(
                                src_points=dst_points_warped_abs_pil,  # Warped receipt space (PIL, absolute pixels)
                                dst_points=src_points_receipt_abs_pil,  # Original receipt space (PIL, absolute pixels)
                            )
                            # Invert to get forward transform (original receipt space -> warped receipt space)
                            direct_transform_coeffs = invert_warp(
                                *inverse_coeffs
                            )
                            direct_transform_available = True
                            print(
                                f"      ✅ Direct transform computed successfully (using absolute pixels)"
                            )
                        except Exception as e:
                            print(
                                f"      ⚠️  Could not compute direct transform: {e}, using two-step approach"
                            )
                            import traceback

                            traceback.print_exc()
                            direct_transform_available = False

                    # Transform and draw ReceiptWords
                    for word in cluster_receipt_words:
                        word_corners_warped = []

                        if direct_transform_available:
                            # Direct transform: original receipt space -> warped receipt space
                            try:
                                # Get word corners in original receipt space (normalized 0-1, OCR space, y=0 at bottom)
                                word_corners_receipt_ocr_norm = []
                                for corner_name in [
                                    "top_left",
                                    "top_right",
                                    "bottom_right",
                                    "bottom_left",
                                ]:
                                    corner = getattr(word, corner_name)
                                    word_corners_receipt_ocr_norm.append(
                                        (corner["x"], corner["y"])
                                    )

                                # Convert from normalized to absolute pixels, then from OCR space to PIL space
                                word_corners_receipt_abs_pil = [
                                    (
                                        x * orig_receipt_width,
                                        orig_receipt_height
                                        - (y * orig_receipt_height),
                                    )
                                    for x, y in word_corners_receipt_ocr_norm
                                ]

                                # Apply direct perspective transform (using absolute pixels)
                                a, b, c, d, e, f, g, h = (
                                    direct_transform_coeffs
                                )
                                for (
                                    x_receipt_abs_pil,
                                    y_receipt_abs_pil,
                                ) in word_corners_receipt_abs_pil:
                                    # Apply perspective transform: (x_receipt_abs_pil, y_receipt_abs_pil) -> (x_warped_abs, y_warped_abs)
                                    denom = (
                                        1
                                        + g * x_receipt_abs_pil
                                        + h * y_receipt_abs_pil
                                    )
                                    if abs(denom) < 1e-10:
                                        raise ValueError(
                                            "Degenerate transform"
                                        )
                                    x_warped_abs = (
                                        a * x_receipt_abs_pil
                                        + b * y_receipt_abs_pil
                                        + c
                                    ) / denom
                                    y_warped_abs = (
                                        d * x_receipt_abs_pil
                                        + e * y_receipt_abs_pil
                                        + f
                                    ) / denom

                                    # Result is already in absolute pixels (PIL space, y=0 at top)
                                    word_corners_warped.append(
                                        (x_warped_abs, y_warped_abs)
                                    )

                            except Exception as e:
                                print(
                                    f"      ⚠️  Direct transform failed for word '{word.text}': {e}, falling back to two-step"
                                )
                                import traceback

                                traceback.print_exc()
                                # Don't set direct_transform_available = False here, just skip this word
                                word_corners_warped = []

                        if (
                            not direct_transform_available
                            or not word_corners_warped
                        ):
                            # Fallback: Two-step transform (original receipt space -> image space -> warped receipt space)
                            # Step 1: Transform from receipt space to image space
                            word_copy = copy.deepcopy(word)
                            forward_coeffs = invert_warp(*transform_coeffs)
                            word_copy.warp_transform(
                                *forward_coeffs,
                                src_width=img_width,
                                src_height=img_height,
                                dst_width=orig_receipt_width,
                                dst_height=orig_receipt_height,
                                flip_y=True,
                            )

                            # Step 2: Get word corners in image space (PIL space, absolute pixels)
                            # Use same corner order as lines: [TL, TR, BR, BL] to avoid bowtie (X shape)
                            word_corners_img = []
                            for corner_name in [
                                "top_left",
                                "top_right",
                                "bottom_right",
                                "bottom_left",
                            ]:
                                corner = getattr(word_copy, corner_name)
                                # After warp_transform with flip_y=True, coordinates are normalized (0-1) in image space
                                # According to split_receipt.py line 835-852, these are in OCR space (normalized 0-1, y=0 at bottom)
                                # Convert to absolute image coordinates (OCR space, absolute pixels)
                                img_x_ocr = corner["x"] * img_width
                                img_y_ocr = (
                                    corner["y"] * img_height
                                )  # Still in OCR space (y=0 at bottom)

                                # Convert OCR space to PIL space (y=0 at top) for the affine transform
                                img_x = img_x_ocr
                                img_y = (
                                    img_height - img_y_ocr
                                )  # Flip Y: OCR -> PIL
                                word_corners_img.append((img_x, img_y))

                            # Step 3: Transform to warped receipt space using cluster's affine transform
                            for img_x, img_y in word_corners_img:
                                receipt_x = a_f * img_x + b_f * img_y + c_f
                                receipt_y = d_f * img_x + e_f * img_y + f_f
                                word_corners_warped.append(
                                    (receipt_x, receipt_y)
                                )

                        # Debug for specific words
                        is_debug_word = any(
                            term in (word.text or "") for term in search_terms
                        )
                        if is_debug_word:
                            min_warped_x = min(
                                x for x, y in word_corners_warped
                            )
                            max_warped_x = max(
                                x for x, y in word_corners_warped
                            )
                            min_warped_y = min(
                                y for x, y in word_corners_warped
                            )
                            max_warped_y = max(
                                y for x, y in word_corners_warped
                            )
                            print(
                                f"      🔍 Debug word '{word.text}': "
                                f"bounds=({min_warped_x:.1f}-{max_warped_x:.1f}, {min_warped_y:.1f}-{max_warped_y:.1f}), "
                                f"warped_size=({w:.1f}, {h:.1f})"
                            )

                        # Draw word bounding box
                        if word_corners_warped and all(
                            isinstance(x, (int, float))
                            and isinstance(y, (int, float))
                            and not (x != x or y != y)  # Check for NaN
                            for x, y in word_corners_warped
                        ):
                            draw.polygon(
                                word_corners_warped, outline=color, width=2
                            )
                            drawn_count += 1

            except Exception as e:
                print(f"      ⚠️  Error finding ReceiptWords: {e}")
                import traceback

                traceback.print_exc()

        # Add legend
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/Helvetica.ttc", 24
            )
            small_font = ImageFont.truetype(
                "/System/Library/Fonts/Helvetica.ttc", 18
            )
        except:
            font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        legend_y = 10
        draw.rectangle(
            [10, legend_y, 300, legend_y + 30],
            fill=(255, 255, 255, 200),
            outline="black",
            width=2,
        )
        draw.text(
            (20, legend_y + 5),
            f"Cluster {cluster_id}: {len(cluster_lines)} lines",
            fill=color,
            font=small_font,
        )

        # Save visualization
        output_path = output_dir / f"receipt_{cluster_id}_visualization.png"
        img.save(output_path)
        print(f"      💾 Saved: {output_path}")

        # Save clean warped image (without bounding boxes) for re-OCR
        clean_output_path = output_dir / f"receipt_{cluster_id}_clean.png"
        warped_image.save(clean_output_path)
        print(f"      💾 Saved clean image for OCR: {clean_output_path}")

        # Export OCR results for comparison
        ocr_export = {
            "cluster_id": cluster_id,
            "image_id": image_id,
            "image_width": img_width,
            "image_height": img_height,
            "warped_width": w,
            "warped_height": h,
            "affine_transform": affine_transform,
            "box_4_ordered": bounds["box_4_ordered"],
            "lines": [],
            "words": [],
            "letters": [],
        }

        # Get inverse affine transform for coordinate transformations
        a_i, b_i, c_i, d_i, e_i, f_i = affine_transform
        a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(
            a_i, b_i, c_i, d_i, e_i, f_i
        )

        # Export each line with its coordinates in both image space and warped receipt space
        # Always use image-level OCR for export (coordinates are correct)
        for line in cluster_lines:
            # Use image-level
            export_line = line
            export_text = line.text
            export_ocr_coords = {
                "top_left": line.top_left,
                "top_right": line.top_right,
                "bottom_left": line.bottom_left,
                "bottom_right": line.bottom_right,
            }

            # Get line corners in image coordinate space (PIL space, y=0 at top)
            corners_img = get_line_corners_image_coords(
                export_line, img_width, img_height
            )

            # Transform to warped receipt space
            corners_warped = []
            for img_x, img_y in corners_img:
                receipt_x = a_f * img_x + b_f * img_y + c_f
                receipt_y = d_f * img_x + e_f * img_y + f_f
                corners_warped.append({"x": receipt_x, "y": receipt_y})

            # Export line data
            line_data = {
                "line_id": line.line_id,
                "text": export_text,  # Use receipt-level text if available
                "ocr_coords": export_ocr_coords,  # Use receipt-level coords if available
                "image_coords_pil": [{"x": x, "y": y} for x, y in corners_img],
                "warped_receipt_coords": corners_warped,
                "source": "image_ocr",  # Always use image-level OCR for coordinates
            }
            ocr_export["lines"].append(line_data)

        # Export ReceiptWords with their coordinates
        for word in cluster_receipt_words:
            # Get word corners in original receipt space (normalized 0-1, OCR space)
            word_corners_receipt = {
                "top_left": word.top_left,
                "top_right": word.top_right,
                "bottom_left": word.bottom_left,
                "bottom_right": word.bottom_right,
            }

            # Transform word from original receipt space to image space
            word_copy = copy.deepcopy(word)
            forward_coeffs = invert_warp(*transform_coeffs)
            word_copy.warp_transform(
                *forward_coeffs,
                src_width=img_width,
                src_height=img_height,
                dst_width=orig_receipt_width,
                dst_height=orig_receipt_height,
                flip_y=True,
            )

            # Get word corners in image space (PIL space, absolute pixels)
            word_corners_img = []
            for corner_name in [
                "top_left",
                "top_right",
                "bottom_right",
                "bottom_left",
            ]:
                corner = getattr(word_copy, corner_name)
                img_x_ocr = corner["x"] * img_width
                img_y_ocr = corner["y"] * img_height
                img_x = img_x_ocr
                img_y = img_height - img_y_ocr  # Flip Y: OCR -> PIL
                word_corners_img.append({"x": img_x, "y": img_y})

            # Transform to warped receipt space
            word_corners_warped = []
            for corner_img in word_corners_img:
                receipt_x = a_f * corner_img["x"] + b_f * corner_img["y"] + c_f
                receipt_y = d_f * corner_img["x"] + e_f * corner_img["y"] + f_f
                word_corners_warped.append({"x": receipt_x, "y": receipt_y})

            # Export word data
            word_data = {
                "line_id": word.line_id,
                "word_id": word.word_id,
                "text": word.text,
                "bounding_box": word.bounding_box,
                "corners": word_corners_receipt,
                "image_coords_pil": word_corners_img,
                "warped_receipt_coords": word_corners_warped,
                "source": "receipt_ocr",
            }
            ocr_export["words"].append(word_data)

        # Export ReceiptLetters with their coordinates
        for letter in cluster_receipt_letters:
            # Get letter corners in original receipt space (normalized 0-1, OCR space)
            letter_corners_receipt = {
                "top_left": letter.top_left,
                "top_right": letter.top_right,
                "bottom_left": letter.bottom_left,
                "bottom_right": letter.bottom_right,
            }

            # Transform letter from original receipt space to image space
            # ReceiptLetters are typically accessed via ReceiptWords, so we need to find the parent word
            # For now, use the letter's coordinates directly
            letter_copy = copy.deepcopy(letter)
            forward_coeffs = invert_warp(*transform_coeffs)
            letter_copy.warp_transform(
                *forward_coeffs,
                src_width=img_width,
                src_height=img_height,
                dst_width=orig_receipt_width,
                dst_height=orig_receipt_height,
                flip_y=True,
            )

            # Get letter corners in image space (PIL space, absolute pixels)
            letter_corners_img = []
            for corner_name in [
                "top_left",
                "top_right",
                "bottom_right",
                "bottom_left",
            ]:
                corner = getattr(letter_copy, corner_name)
                img_x_ocr = corner["x"] * img_width
                img_y_ocr = corner["y"] * img_height
                img_x = img_x_ocr
                img_y = img_height - img_y_ocr  # Flip Y: OCR -> PIL
                letter_corners_img.append({"x": img_x, "y": img_y})

            # Transform to warped receipt space
            letter_corners_warped = []
            for corner_img in letter_corners_img:
                receipt_x = a_f * corner_img["x"] + b_f * corner_img["y"] + c_f
                receipt_y = d_f * corner_img["x"] + e_f * corner_img["y"] + f_f
                letter_corners_warped.append({"x": receipt_x, "y": receipt_y})

            # Export letter data
            letter_data = {
                "line_id": letter.line_id,
                "word_id": letter.word_id,
                "letter_id": letter.letter_id,
                "text": letter.text,
                "bounding_box": letter.bounding_box,
                "corners": letter_corners_receipt,
                "image_coords_pil": letter_corners_img,
                "warped_receipt_coords": letter_corners_warped,
                "source": "receipt_ocr",
            }
            ocr_export["letters"].append(letter_data)

        # Save OCR export
        ocr_export_path = output_dir / f"receipt_{cluster_id}_ocr_export.json"
        with open(ocr_export_path, "w") as f:
            json.dump(ocr_export, f, indent=2)
        print(f"      💾 Saved OCR export: {ocr_export_path}")
        print(
            f"         Lines: {len(ocr_export['lines'])}, Words: {len(ocr_export['words'])}, Letters: {len(ocr_export['letters'])}"
        )

    print(f"\n✅ Complete! Visualizations saved to: {output_dir}")
    print(f"   Total clusters: {len(cluster_dict)}")
    print(
        f"   Total lines: {sum(len(lines) for lines in cluster_dict.values())}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Visualize final clustered receipts (cropped)"
    )
    parser.add_argument(
        "--image-id", required=True, help="Image ID to visualize"
    )
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="Output directory"
    )
    parser.add_argument(
        "--raw-bucket",
        help="Raw S3 bucket name (optional, will try to load from environment)",
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        help="Optional receipt ID. If provided, uses receipt-level OCR (more accurate) instead of image-level OCR",
    )
    parser.add_argument(
        "--x-eps",
        type=float,
        help="Epsilon parameter for X-axis DBSCAN clustering (default: 0.08, larger values create fewer/larger clusters)",
    )
    parser.add_argument(
        "--angle-tolerance",
        type=float,
        help="Angle difference tolerance in degrees for splitting clusters (default: 3.0)",
    )
    parser.add_argument(
        "--vertical-gap-threshold",
        type=float,
        help="Split cluster if vertical gap > this fraction of image height (default: 0.15 = 15%%)",
    )
    parser.add_argument(
        "--merge-x-proximity-threshold",
        type=float,
        help="Don't merge clusters if > this fraction apart horizontally (default: 0.4 = 40%%)",
    )
    parser.add_argument(
        "--merge-min-score",
        type=float,
        help="Minimum coherence score to merge clusters (default: 0.5)",
    )
    parser.add_argument(
        "--reassign-x-proximity-threshold",
        type=float,
        help="X-proximity threshold for reassignment phase (default: 0.1 = 10%%)",
    )
    parser.add_argument(
        "--reassign-y-proximity-threshold",
        type=float,
        help="Y-proximity threshold for reassignment phase (default: 0.15 = 15%%)",
    )
    parser.add_argument(
        "--vertical-proximity-threshold",
        type=float,
        help="Vertical proximity threshold for vertical stacking (default: 0.05 = 5%%)",
    )
    parser.add_argument(
        "--vertical-x-proximity-threshold",
        type=float,
        help="X-proximity threshold for vertical stacking (default: 0.1 = 10%%)",
    )
    parser.add_argument(
        "--join-iou-threshold",
        type=float,
        help="IoU threshold for joining overlapping clusters (default: 0.1)",
    )

    args = parser.parse_args()

    # Setup environment
    env = setup_environment()
    raw_bucket = args.raw_bucket or env.get("raw_bucket", "")
    if not raw_bucket:
        print("⚠️  Raw bucket not specified and not found in environment")
        sys.exit(1)

    visualize_final_clusters_cropped(
        args.image_id,
        args.output_dir,
        raw_bucket,
        receipt_id=args.receipt_id,
        x_eps=args.x_eps,
        angle_tolerance=args.angle_tolerance,
        vertical_gap_threshold=args.vertical_gap_threshold,
        reassign_x_proximity_threshold=args.reassign_x_proximity_threshold,
        reassign_y_proximity_threshold=args.reassign_y_proximity_threshold,
        vertical_proximity_threshold=args.vertical_proximity_threshold,
        vertical_x_proximity_threshold=args.vertical_x_proximity_threshold,
        merge_x_proximity_threshold=args.merge_x_proximity_threshold,
        merge_min_score=args.merge_min_score,
        join_iou_threshold=args.join_iou_threshold,
    )


if __name__ == "__main__":
    main()




