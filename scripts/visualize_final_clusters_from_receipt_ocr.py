#!/usr/bin/env python3
"""
Visualize final clustered receipts starting from receipt-level OCR data.

This script:
1. Loads receipt-level OCR data (ReceiptLine entities)
2. Transforms receipt coordinates to image coordinate space
3. Performs clustering using the same logic as visualize_final_clusters_cropped.py
4. Visualizes using affine-transformed receipt images

This ensures we use the most accurate OCR data (receipt-level) while maintaining
the correct clustering logic.
"""

import argparse
import sys
import copy
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Line, ReceiptLine
from scripts.split_receipt import recluster_receipt_lines, setup_environment
from receipt_upload.geometry.transformations import invert_warp

try:
    from PIL import Image as PIL_Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL not available, install pillow")
    sys.exit(1)

import boto3

# Import geometry functions from receipt_upload (same as scan.py)
from receipt_upload.geometry import box_points, invert_affine, min_area_rect
from receipt_upload.cluster import reorder_box_points


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
    # Step 1: Get transform from receipt space to image space
    transform_coeffs, orig_receipt_width, orig_receipt_height = original_receipt.get_transform_to_image(
        image_width, image_height
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
    # After warp_transform with flip_y=True, coordinates are in normalized image space (0-1)
    # According to inverse_perspective_transform, if flip_y=True, it does:
    #   corner["y"] = 1.0 - corner["y"] at the end
    # So the output is in OCR space (y=0 at bottom)
    # But looking at transform_receipt_ocr_to_new_receipt_space, it treats the output as PIL space
    # and converts: ocr_y = image_height - img_y where img_y = corner["y"] * image_height
    # This suggests warp_transform outputs in PIL space (y=0 at top) in absolute pixels
    # But wait, it normalizes at the end: corner["y"] = y_old_px / src_height
    # And if flip_y=True, it flips: corner["y"] = 1.0 - corner["y"]
    # So the output is normalized 0-1 in OCR space (y=0 at bottom)

    # Let's match what transform_receipt_ocr_to_new_receipt_space does:
    # It converts to absolute pixels first, then handles Y-flip
    corners_img = {}
    for corner_name in ["top_left", "top_right", "bottom_left", "bottom_right"]:
        corner = getattr(entity_copy, corner_name)
        # Convert to absolute pixels (treating as if in PIL space)
        img_x = corner["x"] * image_width
        img_y = corner["y"] * image_height

        # Convert from PIL space to OCR space (like transform_receipt_ocr_to_new_receipt_space does)
        ocr_y = image_height - img_y

        # Convert back to normalized OCR space
        corners_img[corner_name] = {
            "x": img_x / image_width,  # Already normalized
            "y": ocr_y / image_height,  # Now in OCR space (y=0 at bottom)
        }

    # Step 4: Create Line entity with image-space coordinates
    # Calculate angle from corners
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
            "width": (max(corners_img[c]["x"] for c in corners_img) - min(corners_img[c]["x"] for c in corners_img)) * image_width,
            "height": (max(corners_img[c]["y"] for c in corners_img) - min(corners_img[c]["y"] for c in corners_img)) * image_height,
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
    cluster_lines: list[Line],
    img_width: int,
    img_height: int,
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


def visualize_final_clusters_from_receipt_ocr(
    image_id: str,
    output_dir: Path,
    raw_bucket: str,
    original_receipt_id: int = 1,
):
    """Create visualization starting from receipt-level OCR data."""
    client = DynamoClient("ReceiptsTable-dc5be22")

    # Load data
    image_entity = client.get_image(image_id)
    original_receipt = client.get_receipt(image_id, original_receipt_id)
    original_receipt_lines = client.list_receipt_lines_from_receipt(image_id, original_receipt_id)

    print(f"📥 Loaded data:")
    print(f"   Image: {image_entity.width} x {image_entity.height}")
    print(f"   Receipt-level lines: {len(original_receipt_lines)}")

    # Step 1: Load image-level lines for clustering (to get ALL lines)
    # We need all lines for correct clustering, even if some don't have receipt OCR
    image_lines_for_clustering = client.list_lines_from_image(image_id)
    print(f"   Image-level lines for clustering: {len(image_lines_for_clustering)}")

    # Step 2: Get final clusters using the same logic as visualize_final_clusters_cropped
    # This uses image-level lines to ensure we have all lines for correct clustering
    print(f"\n🔄 Re-clustering lines...")
    cluster_dict = recluster_receipt_lines(
        image_lines_for_clustering,
        image_entity.width,
        image_entity.height,
    )

    # Step 3: Transform receipt-level lines to image-space lines for visualization
    # Create a mapping of line_id -> transformed Line entity
    print(f"\n🔄 Transforming receipt OCR to image coordinates for visualization...")
    receipt_line_to_image_line = {}
    for receipt_line in original_receipt_lines:
        try:
            image_line = transform_receipt_line_to_image_line(
                receipt_line,
                original_receipt,
                image_entity.width,
                image_entity.height,
            )
            receipt_line_to_image_line[receipt_line.line_id] = image_line
        except Exception as e:
            print(f"   ⚠️  Failed to transform line {receipt_line.line_id}: {e}")
            continue

    print(f"   ✅ Transformed {len(receipt_line_to_image_line)} receipt lines to image coordinates")

    print(f"   Found {len(cluster_dict)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")

    # Download original image
    original_image = None
    s3_key = image_entity.raw_s3_key if image_entity.raw_s3_key else f"raw/{image_id}.png"
    try:
        original_image = get_image_from_s3(raw_bucket, s3_key)
        print(f"\n✅ Loaded image from raw S3: {s3_key}")
    except Exception as e:
        print(f"⚠️  Could not load image from raw S3 key '{s3_key}': {e}")
        # Try CDN image as fallback
        if image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
            try:
                print(f"   Trying CDN image: {image_entity.cdn_s3_key}")
                original_image = get_image_from_s3(image_entity.cdn_s3_bucket, image_entity.cdn_s3_key)
                print(f"✅ Loaded image from CDN")
            except Exception as e2:
                print(f"⚠️  Could not load image from CDN: {e2}")
        if original_image is None:
            print(f"   Creating blank image for visualization...")
            original_image = PIL_Image.new("RGB", (image_entity.width, image_entity.height), "white")

    img_width, img_height = original_image.size

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Process each cluster
    print(f"\n📊 Final clusters for {image_id}:")
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict))
        print(f"   Cluster {cluster_id}: {len(cluster_lines)} lines (color: {color})")

        # Calculate receipt bounds using same process as receipt_upload
        bounds = calculate_receipt_bounds_from_lines(cluster_lines, img_width, img_height)
        if not bounds:
            print(f"      ⚠️  No bounds - skipping")
            continue

        w = bounds["width"]
        h = bounds["height"]
        affine_transform = bounds["affine_transform"]

        # Warp the image using the affine transform (same as scan.py)
        warped_image = original_image.transform(
            (w, h),
            PIL_Image.AFFINE,
            affine_transform,
            resample=PIL_Image.BICUBIC,
        )

        # Create visualization on warped image
        img = warped_image.copy()
        draw = ImageDraw.Draw(img)

        # Draw each line in this cluster
        # Use receipt-level OCR data when available (more accurate), fallback to image-level
        for line in cluster_lines:
            # Prefer receipt-level OCR if available
            if line.line_id in receipt_line_to_image_line:
                line = receipt_line_to_image_line[line.line_id]
            # Get line corners in image coordinate space
            corners_img = get_line_corners_image_coords(line, img_width, img_height)

            # Transform corners to warped receipt space using the inverse affine transform
            # The affine transform maps (receipt_x, receipt_y) -> (image_x, image_y)
            # We need the inverse: (image_x, image_y) -> (receipt_x, receipt_y)
            a_i, b_i, c_i, d_i, e_i, f_i = affine_transform
            a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)

            # Apply inverse transform to each corner
            corners_warped = []
            for img_x, img_y in corners_img:
                # Inverse affine transform: receipt = A^-1 * (image - c)
                receipt_x = a_f * img_x + b_f * img_y + c_f
                receipt_y = d_f * img_x + e_f * img_y + f_f
                corners_warped.append((receipt_x, receipt_y))

            draw.polygon(corners_warped, outline=color, width=3)

        # Add legend
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
            small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        except:
            font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        legend_y = 10
        draw.rectangle([10, legend_y, 300, legend_y + 30], fill=(255, 255, 255, 200), outline="black", width=2)
        draw.text((20, legend_y + 5), f"Cluster {cluster_id}: {len(cluster_lines)} lines", fill=color, font=small_font)

        # Save
        output_path = output_dir / f"receipt_{cluster_id}_visualization.png"
        img.save(output_path)
        print(f"      💾 Saved: {output_path}")

    print(f"\n✅ Complete! Visualizations saved to: {output_dir}")
    print(f"   Total clusters: {len(cluster_dict)}")
    print(f"   Total lines: {sum(len(lines) for lines in cluster_dict.values())}")


def main():
    parser = argparse.ArgumentParser(description="Visualize final clustered receipts from receipt OCR")
    parser.add_argument("--image-id", required=True, help="Image ID to visualize")
    parser.add_argument("--original-receipt-id", type=int, default=1, help="Original receipt ID (default: 1)")
    parser.add_argument("--output-dir", required=True, type=Path, help="Output directory")
    parser.add_argument("--raw-bucket", help="Raw S3 bucket name (optional, will try to load from environment)")

    args = parser.parse_args()

    # Setup environment
    env = setup_environment()
    raw_bucket = args.raw_bucket or env.get("raw_bucket", "")
    if not raw_bucket:
        print("⚠️  Raw bucket not specified and not found in environment")
        sys.exit(1)

    visualize_final_clusters_from_receipt_ocr(
        args.image_id,
        args.output_dir,
        raw_bucket,
        args.original_receipt_id,
    )


if __name__ == "__main__":
    main()

