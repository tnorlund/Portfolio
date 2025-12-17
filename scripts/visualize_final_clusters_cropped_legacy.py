#!/usr/bin/env python3
"""
Legacy visualization of clustered receipts (cropped), using the older
recluster_receipt_lines parameterization (angle_tolerance, vertical_gap, etc.).

This is a non-destructive copy so we don't overwrite current scripts.
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

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL not available, install pillow")
    sys.exit(1)

import copy

import boto3

from receipt_upload.cluster import reorder_box_points
from receipt_upload.geometry import box_points, invert_affine, min_area_rect
from receipt_upload.geometry.transformations import invert_warp


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


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(response["Body"])


def get_cluster_color(cluster_id: int, total_clusters: int) -> str:
    colors = [
        "#FF0000",
        "#00FF00",
        "#0000FF",
        "#FF00FF",
        "#00FFFF",
        "#FFFF00",
        "#FF8000",
        "#8000FF",
    ]
    return colors[cluster_id % len(colors)]


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
    cluster_lines: list[Line], img_width: int, img_height: int
) -> dict:
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


def visualize_final_clusters_cropped(
    image_id: str,
    output_dir: Path,
    raw_bucket: str,
    x_eps: Optional[float] = None,
):
    env = setup_environment()
    table_name = env.get("table_name")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME not set")
    client = DynamoClient(table_name)
    image_entity = client.get_image(image_id)
    image_lines = client.list_lines_from_image(image_id)
    eps_val = x_eps if x_eps is not None else 0.08
    print(f"Clustering x_eps={eps_val}")
    cluster_dict = legacy_recluster_receipt_lines(
        image_lines, image_entity.width, image_entity.height, x_eps=eps_val
    )

    # load image
    original_image = None
    s3_key = image_entity.raw_s3_key or f"raw/{image_id}.png"
    try:
        original_image = get_image_from_s3(raw_bucket, s3_key)
        print(f"✅ Loaded image: {s3_key}")
    except Exception as e:
        print(f"⚠️  Could not load {s3_key}: {e}")
        if image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
            try:
                original_image = get_image_from_s3(
                    image_entity.cdn_s3_bucket, image_entity.cdn_s3_key
                )
                print("✅ Loaded image from CDN")
            except Exception as e2:
                print(f"⚠️  Could not load CDN image: {e2}")
    if original_image is None:
        original_image = PIL_Image.new(
            "RGB", (image_entity.width, image_entity.height), "white"
        )
    img_width, img_height = original_image.size
    output_dir.mkdir(parents=True, exist_ok=True)

    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict))
        print(f"Cluster {cluster_id}: {len(cluster_lines)} lines")
        bounds = calculate_receipt_bounds_from_lines(
            cluster_lines, img_width, img_height
        )
        if not bounds:
            continue
        w, h = bounds["width"], bounds["height"]
        affine_transform = bounds["affine_transform"]
        warped_image = original_image.transform(
            (w, h),
            PIL_Image.AFFINE,
            affine_transform,
            resample=PIL_Image.BICUBIC,
        )
        img = warped_image.copy()
        draw = ImageDraw.Draw(img)
        # invert affine for warping corners
        a_i, b_i, c_i, d_i, e_i, f_i = affine_transform
        a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(
            a_i, b_i, c_i, d_i, e_i, f_i
        )
        for line in cluster_lines:
            corners_img = get_line_corners_image_coords(
                line, img_width, img_height
            )
            corners_warped = []
            for img_x, img_y in corners_img:
                receipt_x = a_f * img_x + b_f * img_y + c_f
                receipt_y = d_f * img_x + e_f * img_y + f_f
                corners_warped.append((receipt_x, receipt_y))
            draw.polygon(corners_warped, outline=color, width=3)
        out_vis = output_dir / f"receipt_{cluster_id}_visualization.png"
        img.save(out_vis)
        clean_out = output_dir / f"receipt_{cluster_id}_clean.png"
        warped_image.save(clean_out)

        # export OCR
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
        for line in cluster_lines:
            corners_img = get_line_corners_image_coords(
                line, img_width, img_height
            )
            corners_warped = []
            for img_x, img_y in corners_img:
                receipt_x = a_f * img_x + b_f * img_y + c_f
                receipt_y = d_f * img_x + e_f * img_y + f_f
                corners_warped.append({"x": receipt_x, "y": receipt_y})
            ocr_export["lines"].append(
                {
                    "line_id": line.line_id,
                    "text": line.text,
                    "ocr_coords": {
                        "top_left": line.top_left,
                        "top_right": line.top_right,
                        "bottom_left": line.bottom_left,
                        "bottom_right": line.bottom_right,
                    },
                    "image_coords_pil": [
                        {"x": x, "y": y} for x, y in corners_img
                    ],
                    "warped_receipt_coords": corners_warped,
                    "source": "image_ocr",
                }
            )
        ocr_path = output_dir / f"receipt_{cluster_id}_ocr_export.json"
        with open(ocr_path, "w", encoding="utf-8") as f:
            json.dump(ocr_export, f, indent=2)
        print(f"  Saved: {out_vis}, {clean_out}, {ocr_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Legacy clustered receipts visualization (non-destructive copy)."
    )
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--raw-bucket", required=True)
    ap.add_argument("--x-eps", type=float, default=0.08)
    args = ap.parse_args()
    visualize_final_clusters_cropped(
        args.image_id,
        args.output_dir,
        args.raw_bucket,
        x_eps=args.x_eps,
    )


if __name__ == "__main__":
    main()






