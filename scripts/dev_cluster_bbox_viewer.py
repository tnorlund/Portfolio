#!/usr/bin/env python3
"""
Developer helper to visualize line clusters with bounding boxes (non-destructive).

Steps:
1) Pull lines for an image from Dynamo.
2) Run the legacy-style recluster with full params (angle, vertical gap, etc.).
3) Draw per-cluster bounding boxes on the receipt image and save overlays.

You can point at raw bucket or let it fall back to CDN; if neither works,
it will draw on a blank white canvas.
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
from receipt_dynamo.entities import Line
from scripts.split_receipt import (
    dbscan_lines_x_axis,
    join_overlapping_clusters,
    merge_clusters_with_agent_logic,
    reassign_lines_by_vertical_proximity,
    reassign_lines_by_x_proximity,
    setup_environment,
    should_apply_smart_merging,
    split_clusters_by_angle_consistency,
)

try:
    from PIL import Image as PIL_Image
    from PIL import ImageDraw, ImageFont
except ImportError:
    print("⚠️  pillow is required. Install with `pip install pillow`.")
    sys.exit(1)

import boto3

from receipt_upload.cluster import reorder_box_points
from receipt_upload.geometry import box_points, invert_affine, min_area_rect


# --- Clustering (legacy parameterization) -----------------------------------------------------
def legacy_recluster_receipt_lines(
    image_lines: List[Line],
    image_width: int,
    image_height: int,
    x_eps: float = 0.08,
    angle_tolerance: float = 3.0,
    vertical_gap_threshold: float = 0.15,
    reassign_x_proximity_threshold: float = 0.1,
    reassign_y_proximity_threshold: float = 0.15,
    vertical_proximity_threshold: float = 0.05,
    vertical_x_proximity_threshold: float = 0.1,
    merge_min_score: float = 0.5,
    merge_x_proximity_threshold: float = 0.4,
    join_iou_threshold: float = 0.1,
) -> Dict[int, List[Line]]:
    cluster_dict = dbscan_lines_x_axis(image_lines, eps=x_eps)
    cluster_dict = split_clusters_by_angle_consistency(
        cluster_dict,
        angle_tolerance=angle_tolerance,
        min_samples=2,
        vertical_gap_threshold=vertical_gap_threshold,
    )
    cluster_dict = reassign_lines_by_x_proximity(
        cluster_dict,
        x_proximity_threshold=reassign_x_proximity_threshold,
        y_proximity_threshold=reassign_y_proximity_threshold,
    )
    cluster_dict = reassign_lines_by_vertical_proximity(
        cluster_dict,
        vertical_proximity_threshold=vertical_proximity_threshold,
        x_proximity_threshold=vertical_x_proximity_threshold,
    )
    if should_apply_smart_merging(cluster_dict, len(image_lines)):
        cluster_dict = merge_clusters_with_agent_logic(
            cluster_dict,
            min_score=merge_min_score,
            x_proximity_threshold=merge_x_proximity_threshold,
        )
    if len(cluster_dict) > 2:
        cluster_dict = join_overlapping_clusters(
            cluster_dict,
            image_width,
            image_height,
            iou_threshold=join_iou_threshold,
            x_proximity_threshold=0.3,
        )
    return cluster_dict


# --- Geometry helpers ------------------------------------------------------------------------
def get_line_corners_image_coords(line: Line, img_width: int, img_height: int):
    return [
        (
            line.top_left["x"] * img_width,
            (1.0 - line.top_left["y"]) * img_height,
        ),
        (
            line.top_right["x"] * img_width,
            (1.0 - line.top_right["y"]) * img_height,
        ),
        (
            line.bottom_right["x"] * img_width,
            (1.0 - line.bottom_right["y"]) * img_height,
        ),
        (
            line.bottom_left["x"] * img_width,
            (1.0 - line.bottom_left["y"]) * img_height,
        ),
    ]


def calculate_receipt_bounds_from_lines(
    cluster_lines: List[Line],
    img_width: int,
    img_height: int,
) -> Optional[dict]:
    points_abs = []
    for line in cluster_lines:
        for corner in [
            line.top_left,
            line.top_right,
            line.bottom_left,
            line.bottom_right,
        ]:
            x_abs = corner["x"] * img_width
            y_abs = (1 - corner["y"]) * img_height
            points_abs.append((x_abs, y_abs))
    if not points_abs:
        return None

    (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
    w = int(round(rw))
    h = int(round(rh))
    if w < 1 or h < 1:
        return None
    if w > h:
        angle_deg -= 90.0
        w, h = h, w
        rw, rh = rh, rw
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    box_4_ordered = reorder_box_points(box_4)
    src_tl, src_tr, src_bl = (
        box_4_ordered[0],
        box_4_ordered[1],
        box_4_ordered[3],
    )
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


# --- Image loading --------------------------------------------------------------------------
def get_image_from_s3(bucket: str, key: str) -> Optional[PIL_Image.Image]:
    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return PIL_Image.open(response["Body"])
    except Exception as exc:
        print(f"⚠️  Could not load s3://{bucket}/{key}: {exc}")
        return None


def load_image(image_entity, raw_bucket: Optional[str]) -> PIL_Image.Image:
    img = None
    if raw_bucket:
        raw_key = image_entity.raw_s3_key or f"raw/{image_entity.image_id}.png"
        img = get_image_from_s3(raw_bucket, raw_key)
        if img:
            print(f"✅ Loaded image from raw bucket: {raw_key}")
    if img is None and image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
        img = get_image_from_s3(
            image_entity.cdn_s3_bucket, image_entity.cdn_s3_key
        )
        if img:
            print("✅ Loaded image from CDN")
    if img is None:
        img = PIL_Image.new(
            "RGB", (image_entity.width, image_entity.height), "white"
        )
        print("⚠️  Using blank white image fallback")
    return img


# --- Drawing --------------------------------------------------------------------------------
def cluster_color(idx: int) -> str:
    palette = [
        "#FF0000",
        "#00A2FF",
        "#00C853",
        "#FF8F00",
        "#9C27B0",
        "#00CED1",
        "#E91E63",
        "#795548",
    ]
    return palette[idx % len(palette)]


def draw_cluster_overlay(
    base_image: PIL_Image.Image,
    cluster_dict: Dict[int, List[Line]],
    output_dir: Path,
    prefix: str,
):
    font = None
    try:
        font = ImageFont.truetype("Arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    # combined overlay
    combined = base_image.copy()
    combined_draw = ImageDraw.Draw(combined)
    img_w, img_h = base_image.size

    for cluster_id, lines in sorted(cluster_dict.items()):
        color = cluster_color(cluster_id)
        bounds = calculate_receipt_bounds_from_lines(lines, img_w, img_h)
        if not bounds:
            continue
        # draw bounding box
        combined_draw.polygon(bounds["box_4_ordered"], outline=color, width=4)
        # label
        cx = sum(p[0] for p in bounds["box_4_ordered"]) / 4
        cy = sum(p[1] for p in bounds["box_4_ordered"]) / 4
        combined_draw.text(
            (cx + 4, cy + 4), f"cluster {cluster_id}", fill=color, font=font
        )

        # per-cluster file
        img = base_image.copy()
        draw = ImageDraw.Draw(img)
        draw.polygon(bounds["box_4_ordered"], outline=color, width=4)
        draw.text(
            (cx + 4, cy + 4), f"cluster {cluster_id}", fill=color, font=font
        )
        # optional: also draw individual line polygons
        for line in lines:
            corners_img = get_line_corners_image_coords(line, img_w, img_h)
            draw.polygon(corners_img, outline=color, width=2)
        per_path = output_dir / f"{prefix}_cluster_{cluster_id}.png"
        img.save(per_path)
        print(f"  saved {per_path}")

    combined_path = output_dir / f"{prefix}_clusters_overlay.png"
    combined.save(combined_path)
    print(f"  saved {combined_path}")


# --- Main -----------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Visualize clusters with bounding boxes (legacy clustering)."
    )
    ap.add_argument("--image-id", required=True, help="Receipt image id")
    ap.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory to write overlays",
    )
    ap.add_argument(
        "--raw-bucket", required=False, help="Raw bucket to fetch source image"
    )
    ap.add_argument("--x-eps", type=float, default=0.08, help="DBSCAN x_eps")
    args = ap.parse_args()

    env = setup_environment()
    table_name = env.get("table_name")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME not set")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    client = DynamoClient(table_name)
    image_entity = client.get_image(args.image_id)
    image_lines = client.list_lines_from_image(args.image_id)
    print(f"Found {len(image_lines)} lines for image {args.image_id}")

    cluster_dict = legacy_recluster_receipt_lines(
        image_lines,
        image_entity.width,
        image_entity.height,
        x_eps=args.x_eps,
    )
    print(f"Clusters: { {k: len(v) for k, v in cluster_dict.items()} }")

    base_image = load_image(image_entity, args.raw_bucket)
    draw_cluster_overlay(
        base_image, cluster_dict, output_dir, prefix=args.image_id
    )

    summary_path = output_dir / f"{args.image_id}_clusters.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "image_id": args.image_id,
                "x_eps": args.x_eps,
                "clusters": {str(k): len(v) for k, v in cluster_dict.items()},
            },
            f,
            indent=2,
        )
    print(f"  saved {summary_path}")


if __name__ == "__main__":
    main()






