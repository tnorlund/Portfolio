#!/usr/bin/env python3
"""
Back-map warped receipt payloads (lines/words) to the original image to validate
corners and geometry against receipt_upload conventions.

For each payload_full.json and corresponding split.json, this:
- Recomputes the perspective from the oriented quad to the warped rect
- Maps warped line/word polygons back into the original image
- Draws overlays on the original image for visual confirmation

Usage example:
  python scripts/validate_backmap_overlays.py \
    --image-id 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
    --split-json manual_splits/752c/752cf8e2-cb69-4643-8f28-0ac9e3d2df25_receipt1_split.json \
    --payload-left warped_splits/752c/receipt_2_payload_full.json \
    --payload-right warped_splits/752c/receipt_3_payload_full.json \
    --out-dir warped_splits/752c
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

import boto3
from PIL import Image as PIL_Image
from PIL import ImageDraw, ImageFont

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))
from receipt_dynamo import DynamoClient  # type: ignore
from receipt_upload.geometry.transformations import (  # type: ignore
    find_perspective_coeffs,
    invert_warp,
)
from scripts.split_receipt import setup_environment  # type: ignore


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    s3 = boto3.client("s3")
    resp = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(resp["Body"]).convert("RGB")


def load_original_image(
    image_entity, raw_bucket: str, prefer_cdn=True
) -> PIL_Image.Image:
    img = None
    if prefer_cdn and image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
        try:
            img = get_image_from_s3(
                image_entity.cdn_s3_bucket, image_entity.cdn_s3_key
            )
            print(f"Loaded CDN {image_entity.cdn_s3_key}")
        except Exception:
            img = None
    if img is None and raw_bucket:
        try:
            rk = image_entity.raw_s3_key or f"raw/{image_entity.image_id}.png"
            img = get_image_from_s3(raw_bucket, rk)
            print(f"Loaded RAW {rk}")
        except Exception:
            img = None
    if (
        img is None
        and not prefer_cdn
        and image_entity.cdn_s3_bucket
        and image_entity.cdn_s3_key
    ):
        try:
            img = get_image_from_s3(
                image_entity.cdn_s3_bucket, image_entity.cdn_s3_key
            )
            print("Loaded CDN fallback")
        except Exception:
            img = None
    if img is None:
        img = PIL_Image.new(
            "RGB", (image_entity.width, image_entity.height), "white"
        )
        print("Using blank image")
    return img


def perspective_coeffs_dst_to_src(
    obox: dict, w_out: int, h_out: int
) -> List[float]:
    q = obox["pixel_quad"]
    src = [
        (q["top_left"]["x"], q["top_left"]["y"]),
        (q["top_right"]["x"], q["top_right"]["y"]),
        (q["bottom_right"]["x"], q["bottom_right"]["y"]),
        (q["bottom_left"]["x"], q["bottom_left"]["y"]),
    ]
    top_len = (
        (src[0][0] - src[1][0]) ** 2 + (src[0][1] - src[1][1]) ** 2
    ) ** 0.5
    bottom_len = (
        (src[3][0] - src[2][0]) ** 2 + (src[3][1] - src[2][1]) ** 2
    ) ** 0.5
    left_len = (
        (src[0][0] - src[3][0]) ** 2 + (src[0][1] - src[3][1]) ** 2
    ) ** 0.5
    right_len = (
        (src[1][0] - src[2][0]) ** 2 + (src[1][1] - src[2][1]) ** 2
    ) ** 0.5
    w_calc = max(1, int(round(max(top_len, bottom_len))))
    h_calc = max(1, int(round(max(left_len, right_len))))
    w_out = max(w_out, w_calc)
    h_out = max(h_out, h_calc)
    dst = [(0, 0), (w_out - 1, 0), (w_out - 1, h_out - 1), (0, h_out - 1)]
    coeffs = find_perspective_coeffs(src_points=src, dst_points=dst)
    return coeffs, w_out, h_out


def apply_coeffs_pt(
    pt: Tuple[float, float], coeffs: List[float]
) -> Tuple[float, float]:
    x, y = pt
    a, b, c, d, e, f, g, h = coeffs
    denom = g * x + h * y + 1.0
    if abs(denom) < 1e-12:
        return (0.0, 0.0)
    return ((a * x + b * y + c) / denom, (d * x + e * y + f) / denom)


def map_pts(
    points_norm: List[dict], w: int, h: int, coeffs: List[float]
) -> List[Tuple[float, float]]:
    pts = []
    for p in points_norm:
        x = p["x"] * w
        y = (1 - p["y"]) * h
        pts.append(apply_coeffs_pt((x, y), coeffs))
    return pts


def overlay_payloads(
    original: PIL_Image.Image,
    payload_path: Path,
    split_path: Path,
    side: str,
    out_image_path: Path,
    out_receipt_path: Path,
    receipt_orig: dict,
):
    payload = json.loads(payload_path.read_text())
    split = json.loads(split_path.read_text())
    obox = split["left_obox"] if side == "left" else split["right_obox"]
    w_out = payload["receipt"]["width"]
    h_out = payload["receipt"]["height"]
    coeffs_dst_to_src, w_out, h_out = perspective_coeffs_dst_to_src(
        obox, w_out, h_out
    )

    # Build receipt<->image transforms from original receipt corners
    W_img, H_img = original.size
    r_tl = receipt_orig["top_left"]
    r_tr = receipt_orig["top_right"]
    r_br = receipt_orig["bottom_right"]
    r_bl = receipt_orig["bottom_left"]
    src_receipt = [
        (r_tl["x"] * W_img, (1 - r_tl["y"]) * H_img),
        (r_tr["x"] * W_img, (1 - r_tr["y"]) * H_img),
        (r_br["x"] * W_img, (1 - r_br["y"]) * H_img),
        (r_bl["x"] * W_img, (1 - r_bl["y"]) * H_img),
    ]
    dst_receipt = [(0, 0), (1, 0), (1, 1), (0, 1)]
    coeffs_img_to_receipt = find_perspective_coeffs(
        src_points=src_receipt, dst_points=dst_receipt
    )  # maps image px -> receipt norm
    coeffs_receipt_to_img = invert_warp(*coeffs_img_to_receipt)

    # image overlay
    img = original.copy()
    draw = ImageDraw.Draw(img)
    # receipt-space overlay using original receipt aspect/background
    receipt_w = receipt_orig.get("width", 1000) or 1000
    receipt_h = receipt_orig.get("height", 1000) or 1000
    receipt_w = int(receipt_w)
    receipt_h = int(receipt_h)

    # build image->receipt transform for background
    r_tl = receipt_orig["top_left"]
    r_tr = receipt_orig["top_right"]
    r_br = receipt_orig["bottom_right"]
    r_bl = receipt_orig["bottom_left"]
    W_img, H_img = original.size
    src_receipt = [
        (r_tl["x"] * W_img, (1 - r_tl["y"]) * H_img),
        (r_tr["x"] * W_img, (1 - r_tr["y"]) * H_img),
        (r_br["x"] * W_img, (1 - r_br["y"]) * H_img),
        (r_bl["x"] * W_img, (1 - r_bl["y"]) * H_img),
    ]
    dst_receipt_px = [
        (0, 0),
        (receipt_w - 1, 0),
        (receipt_w - 1, receipt_h - 1),
        (0, receipt_h - 1),
    ]
    coeffs_img_to_receipt_px = find_perspective_coeffs(
        src_points=src_receipt, dst_points=dst_receipt_px
    )
    receipt_img = original.transform(
        (receipt_w, receipt_h),
        PIL_Image.PERSPECTIVE,
        coeffs_img_to_receipt_px,
        resample=PIL_Image.BICUBIC,
    )
    receipt_draw = ImageDraw.Draw(receipt_img)
    try:
        font = ImageFont.truetype("Arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    def draw_entities(entities, color):
        for idx, ent in enumerate(entities, start=1):
            pts_warp_norm = [
                ent["top_left"],
                ent["top_right"],
                ent["bottom_right"],
                ent["bottom_left"],
            ]
            # warped -> image
            pts_img = map_pts(pts_warp_norm, w_out, h_out, coeffs_dst_to_src)
            # image -> receipt space (normalized)
            pts_receipt_norm = [
                apply_coeffs_pt(p, coeffs_img_to_receipt) for p in pts_img
            ]
            # receipt -> image (for drawing final, two-step)
            pts_final = [
                apply_coeffs_pt((p[0], p[1]), coeffs_receipt_to_img)
                for p in pts_receipt_norm
            ]
            draw.polygon(pts_final, outline=color, width=2)
            cx = sum(p[0] for p in pts_final) / 4
            cy = sum(p[1] for p in pts_final) / 4
            draw.text((cx, cy), f"{idx}", fill=color, font=font)

            # image -> receipt space (pixel) direct for receipt overlay
            pts_receipt_px = [
                apply_coeffs_pt(p, coeffs_img_to_receipt_px) for p in pts_img
            ]
            receipt_draw.polygon(pts_receipt_px, outline=color, width=2)
            cxr = sum(p[0] for p in pts_receipt_px) / 4
            cyr = sum(p[1] for p in pts_receipt_px) / 4
            receipt_draw.text((cxr, cyr), f"{idx}", fill=color, font=font)

    draw_entities(payload.get("lines", []), "#FF0000")
    draw_entities(payload.get("words", []), "#00AA00")

    # draw receipt bounding box (the oriented quad) in both spaces
    obox = split["left_obox"] if side == "left" else split["right_obox"]
    q = obox["pixel_quad"]
    quad_img = [
        (q["top_left"]["x"], q["top_left"]["y"]),
        (q["top_right"]["x"], q["top_right"]["y"]),
        (q["bottom_right"]["x"], q["bottom_right"]["y"]),
        (q["bottom_left"]["x"], q["bottom_left"]["y"]),
    ]
    draw.polygon(quad_img, outline="#0000FF", width=3)

    quad_rec_px = [
        apply_coeffs_pt(p, coeffs_img_to_receipt_px) for p in quad_img
    ]
    receipt_draw.polygon(quad_rec_px, outline="#0000FF", width=3)

    out_image_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_image_path)
    receipt_img.save(out_receipt_path)
    print(f"Saved validation overlay (image): {out_image_path}")
    print(f"Saved validation overlay (receipt space): {out_receipt_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Back-map warped receipt payloads to original image for validation."
    )
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--split-json", required=True, type=Path)
    ap.add_argument("--payload-left", required=True, type=Path)
    ap.add_argument("--payload-right", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()

    env = setup_environment()
    table = env["table_name"]
    raw_bucket = env.get("raw_bucket") or env.get("raw_bucket_name")
    client = DynamoClient(table)
    image_entity = client.get_image(args.image_id)
    receipt_orig = client.get_receipt(args.image_id, 1).__dict__
    original = load_original_image(image_entity, raw_bucket, prefer_cdn=True)

    overlay_payloads(
        original,
        args.payload_left,
        args.split_json,
        "left",
        args.out_dir / "receipt_left_validation_on_original.png",
        args.out_dir / "receipt_left_validation_on_receipt_space.png",
        receipt_orig,
    )
    overlay_payloads(
        original,
        args.payload_right,
        args.split_json,
        "right",
        args.out_dir / "receipt_right_validation_on_original.png",
        args.out_dir / "receipt_right_validation_on_receipt_space.png",
        receipt_orig,
    )


if __name__ == "__main__":
    main()


