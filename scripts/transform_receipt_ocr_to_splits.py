#!/usr/bin/env python3
"""
Transform original receipt OCR (lines/words/labels) into new split receipts,
and optionally render overlays on the cropped receipts.

Inputs required:
- Original OCR JSON (with lines/words/labels) in normalized receipt space (y=0 bottom).
- The merged/original image (for dimensions).
- The crop boxes (TL/TR/BL/BR) for the new split receipts, in normalized image space (y=0 bottom)
  OR min_x/min_y/width/height in pixels. We derive both from corners.
- Optional clean cropped receipt images to render overlays.

What it does:
- For each target receipt, transform all original lines/words into that receipt's normalized space:
    1) receipt->image: x_img = x_norm * W, y_img = H*(1 - y_norm)
    2) image->crop: x_rec = (x_img - min_x) / crop_w, y_rec = 1 - ((y_img - min_y)/crop_h)
  (corners and centroids)
- Map labels: nearest new word (by centroid) within a tolerance (default 0.05).
- Writes per-receipt JSON with transformed lines/words/labels.
- Optionally saves overlay PNGs if a clean receipt image is provided.

Example:
  python scripts/transform_receipt_ocr_to_splits.py \
    --original-ocr ./split_out_13da/receipt_1_ocr_export.json \
    --image ./local_receipt_data/13da1048-3888-429f-b2aa-b3e15341da5e/13da1048-3888-429f-b2aa-b3e15341da5e_merged.png \
    --target-receipt 2 ./split_out_13da/receipt_2_clean.png \
    --target-receipt 3 ./split_out_13da/receipt_1_clean.png \
    --target-corners 2 "TL=0.12,0.88;TR=0.95,0.87;BL=0.11,0.08;BR=0.94,0.07" \
    --target-corners 3 "TL=0.12,0.88;TR=0.95,0.87;BL=0.11,0.08;BR=0.94,0.07" \
    --out-dir ./transformed_ocr \
    --label-match-tol 0.05

Notes:
- Corners are normalized to the full image and use OCR space y=0 bottom.
- If you already know the crop box in pixels, you can pass --target-crop instead of corners.
- If you skip clean images, overlays are not produced.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def parse_corners_str(s: str) -> Dict[str, Dict[str, float]]:
    """
    Parse corners string like: "TL=0.1,0.9;TR=0.9,0.9;BL=0.1,0.1;BR=0.9,0.1"
    Returns dict with top_left/top_right/bottom_left/bottom_right.
    """
    parts = s.split(";")
    out = {}
    key_map = {
        "TL": "top_left",
        "TR": "top_right",
        "BL": "bottom_left",
        "BR": "bottom_right",
    }
    for p in parts:
        if "=" not in p:
            continue
        k, coords = p.split("=")
        k = k.strip().upper()
        if k not in key_map:
            continue
        xy = coords.split(",")
        if len(xy) != 2:
            continue
        x, y = float(xy[0].strip()), float(xy[1].strip())
        out[key_map[k]] = {"x": x, "y": y}
    if len(out) != 4:
        raise ValueError("Invalid corners string; need TL/TR/BL/BR")
    return out


def corners_to_crop_box(
    corners: Dict[str, Dict[str, float]], image_w: int, image_h: int
) -> Tuple[float, float, float, float]:
    """
    Convert normalized corners (OCR space y=0 bottom) to crop min_x, min_y, w, h in pixels.
    Uses bounding box of the four points.
    """
    pts = []
    for name in ["top_left", "top_right", "bottom_left", "bottom_right"]:
        c = corners[name]
        x = c["x"] * image_w
        y = (1.0 - c["y"]) * image_h  # flip to image space
        pts.append((x, y))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return min_x, min_y, max_x - min_x, max_y - min_y


def ocr_to_image(x: float, y: float, w: int, h: int) -> Tuple[float, float]:
    return x * w, h - (y * h)


def image_to_receipt(
    px: float, py: float, min_x: float, min_y: float, w: float, h: float
) -> Tuple[float, float]:
    return (px - min_x) / w, 1.0 - ((py - min_y) / h)


def transform_entity(
    entity: Dict,
    min_x: float,
    min_y: float,
    crop_w: float,
    crop_h: float,
    image_w: int,
    image_h: int,
    affine: Optional[List[float]] = None,
) -> Dict:
    # pick coord source: prefer ocr_coords, then corners, then direct corners on entity
    if "ocr_coords" in entity:
        coord_src = entity["ocr_coords"]
    elif "corners" in entity:
        coord_src = entity["corners"]
    else:
        coord_src = entity
    out_coords = {}
    for corner in ["top_left", "top_right", "bottom_right", "bottom_left"]:
        c = coord_src[corner]
        # receipt-space (normalized) -> image pixels
        if affine:
            a, b, c0, d, e, f = affine
            px = a * c["x"] + b * c["y"] + c0
            py = d * c["x"] + e * c["y"] + f
        else:
            px, py = ocr_to_image(c["x"], c["y"], image_w, image_h)
        # image pixels -> crop-normalized (y=0 bottom)
        rx, ry = image_to_receipt(px, py, min_x, min_y, crop_w, crop_h)
        out_coords[corner] = {"x": rx, "y": ry}
    entity_out = dict(entity)
    entity_out["transformed_coords"] = out_coords
    return entity_out


def centroid(coords: Dict[str, Dict[str, float]]) -> Tuple[float, float]:
    xs = [coords[k]["x"] for k in coords]
    ys = [coords[k]["y"] for k in coords]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def map_labels(
    labels: List[Dict], words_transformed: List[Dict], tol: float
) -> List[Dict]:
    """
    Map labels to nearest transformed word centroid within tol (normalized).
    Assumes labels reference old word IDs and that words_transformed carry original word_id.
    """
    mapped = []
    # Precompute centroids
    centroids = [
        (w.get("word_id"), centroid(w["transformed_coords"]))
        for w in words_transformed
    ]
    for lbl in labels:
        target_cid = None
        best_d = None
        # Use label's original word coords if present, else skip
        if "ocr_coords" in lbl:
            c = lbl["ocr_coords"]
        elif "coords" in lbl:
            c = lbl["coords"]
        else:
            continue
        cx, cy = centroid(
            {
                k: c[k]
                for k in [
                    "top_left",
                    "top_right",
                    "bottom_right",
                    "bottom_left",
                ]
            }
        )
        # This centroid is still original space; caller should transform labels too if needed.
        # For simplicity, we use the transformed centroid of label if provided; otherwise skip.
        for wid, (wx, wy) in centroids:
            dx = cx - wx
            dy = cy - wy
            d = math.hypot(dx, dy)
            if d <= tol and (best_d is None or d < best_d):
                best_d = d
                target_cid = wid
        if target_cid is not None:
            mapped.append({**lbl, "mapped_word_id": target_cid})
    return mapped


def render_overlay(
    img_path: Path, entities: List[Dict], out_path: Path
) -> None:
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 165, 0),
        (128, 0, 128),
        (255, 192, 203),
        (0, 255, 255),
        (255, 255, 0),
    ]
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except Exception:
        font = ImageFont.load_default()

    w, h = img.width, img.height
    for idx, ent in enumerate(entities):
        color = colors[idx % len(colors)]
        coords = ent["transformed_coords"]
        pts = [
            (coords["top_left"]["x"] * w, (1 - coords["top_left"]["y"]) * h),
            (coords["top_right"]["x"] * w, (1 - coords["top_right"]["y"]) * h),
            (
                coords["bottom_right"]["x"] * w,
                (1 - coords["bottom_right"]["y"]) * h,
            ),
            (
                coords["bottom_left"]["x"] * w,
                (1 - coords["bottom_left"]["y"]) * h,
            ),
        ]
        draw.polygon(pts, outline=color, width=2)
        tl = pts[0]
        label = f"{ent.get('line_id') or ent.get('word_id') or '?'}"
        draw.text((tl[0] + 2, tl[1] - 14), label, fill=color, font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"✅ Saved overlay: {out_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Transform original receipt OCR into split receipts and map labels."
    )
    ap.add_argument(
        "--original-ocr",
        required=True,
        type=Path,
        help="Original receipt OCR JSON (lines/words/labels).",
    )
    ap.add_argument(
        "--image", required=True, type=Path, help="Merged/full image (PNG)."
    )
    ap.add_argument(
        "--target-receipt",
        action="append",
        nargs=2,
        metavar=("ID", "CLEAN_IMG"),
        help="Target receipt id and clean image path.",
    )
    ap.add_argument(
        "--target-corners",
        action="append",
        nargs=2,
        metavar=("ID", "CORNERS"),
        help='Corners for target receipt: "TL=x,y;TR=x,y;BL=x,y;BR=x,y" (normalized, y=0 bottom).',
    )
    ap.add_argument(
        "--target-crop",
        action="append",
        nargs=5,
        metavar=("ID", "MIN_X", "MIN_Y", "W", "H"),
        help="Crop box in pixels if corners not provided.",
    )
    ap.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Directory to write transformed JSON and overlays.",
    )
    ap.add_argument(
        "--label-match-tol",
        type=float,
        default=0.05,
        help="Tolerance (normalized) for mapping labels to words.",
    )
    ap.add_argument(
        "--use-affine-from-source",
        action="store_true",
        help="If set, use affine_transform and box_4_ordered from source OCR JSON for crop/warp.",
    )
    args = ap.parse_args()

    img = Image.open(args.image).convert("RGB")
    image_w, image_h = img.width, img.height

    with open(args.original_ocr, encoding="utf-8") as f:
        ocr = json.load(f)
    lines_orig = ocr.get("lines", [])
    words_orig = ocr.get("words", [])
    labels_orig = ocr.get("labels", [])

    # Build target specs
    target_specs = {}
    if args.use_affine_from_source:
        affine = ocr.get("affine_transform")
        box = ocr.get("box_4_ordered") or []
        if not affine or len(affine) != 6 or len(box) != 4:
            raise ValueError(
                "affine_transform or box_4_ordered missing/invalid in source OCR JSON"
            )
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cw = max_x - min_x
        ch = max_y - min_y
        # Apply same affine/crop to all targets; assumes one target (e.g., single receipt export)
        if args.target_receipt:
            for rid_str, _ in args.target_receipt:
                rid = int(rid_str)
                target_specs[rid] = {
                    "min_x": min_x,
                    "min_y": min_y,
                    "crop_w": cw,
                    "crop_h": ch,
                    "affine": affine,
                }
    else:
        if args.target_corners:
            for rid_str, corners_str in args.target_corners:
                rid = int(rid_str)
                corners = parse_corners_str(corners_str)
                min_x, min_y, cw, ch = corners_to_crop_box(
                    corners, image_w, image_h
                )
                target_specs[rid] = {
                    "min_x": min_x,
                    "min_y": min_y,
                    "crop_w": cw,
                    "crop_h": ch,
                    "corners": corners,
                }
        if args.target_crop:
            for rec in args.target_crop:
                rid = int(rec[0])
                min_x, min_y, cw, ch = map(float, rec[1:])
                target_specs[rid] = {
                    "min_x": min_x,
                    "min_y": min_y,
                    "crop_w": cw,
                    "crop_h": ch,
                }

    # map id -> clean image path
    clean_images = {}
    if args.target_receipt:
        for rid_str, img_path in args.target_receipt:
            clean_images[int(rid_str)] = Path(img_path)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    for rid, spec in target_specs.items():
        min_x = spec["min_x"]
        min_y = spec["min_y"]
        cw = spec["crop_w"]
        ch = spec["crop_h"]

        affine = spec.get("affine")
        # Transform lines/words
        lines_tx = [
            transform_entity(
                l, min_x, min_y, cw, ch, image_w, image_h, affine=affine
            )
            for l in lines_orig
        ]
        words_tx = [
            transform_entity(
                w, min_x, min_y, cw, ch, image_w, image_h, affine=affine
            )
            for w in words_orig
        ]

        # Map labels to nearest transformed word
        labels_mapped = map_labels(
            labels_orig, words_tx, tol=args.label_match_tol
        )

        out_json = {
            "receipt_id": rid,
            "image_width": cw,
            "image_height": ch,
            "lines": lines_tx,
            "words": words_tx,
            "labels_mapped": labels_mapped,
        }
        out_path = args.out_dir / f"receipt_{rid}_transformed.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_json, f, indent=2)
        print(f"✅ Wrote {out_path}")

        # Optional overlay on clean image
        if rid in clean_images:
            overlay_path = args.out_dir / f"receipt_{rid}_overlay.png"
            render_overlay(clean_images[rid], lines_tx, overlay_path)


if __name__ == "__main__":
    main()




