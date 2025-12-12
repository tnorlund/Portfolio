import argparse
import json
import os
from pathlib import Path
from typing import Dict, Tuple

import boto3
from PIL import Image, ImageDraw


def _download_s3(bucket: str, key: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client("s3")
    s3.download_file(bucket, key, str(dest))
    return dest


def _load_records(records_path: Path) -> Dict:
    with records_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _ocr_to_pil_y(y_ocr: float, height: int) -> float:
    return height - y_ocr


def _draw_polygon(draw: ImageDraw.ImageDraw, points: Tuple[Tuple[float, float], ...], color: str) -> None:
    draw.line(points + (points[0],), fill=color, width=3)


def _draw_receipt_corners(draw: ImageDraw.ImageDraw, corners: Dict[str, Dict[str, float]], width: int, height: int, color: str) -> None:
    tl = (corners["top_left"]["x"] * width, _ocr_to_pil_y(corners["top_left"]["y"] * height, height))
    tr = (corners["top_right"]["x"] * width, _ocr_to_pil_y(corners["top_right"]["y"] * height, height))
    br = (corners["bottom_right"]["x"] * width, _ocr_to_pil_y(corners["bottom_right"]["y"] * height, height))
    bl = (corners["bottom_left"]["x"] * width, _ocr_to_pil_y(corners["bottom_left"]["y"] * height, height))
    _draw_polygon(draw, (tl, tr, br, bl), color)


def _draw_bounds(draw: ImageDraw.ImageDraw, bounds: Dict[str, Dict[str, float]], height: int, color: str) -> None:
    tl = (bounds["top_left"]["x"], _ocr_to_pil_y(bounds["top_left"]["y"], height))
    tr = (bounds["top_right"]["x"], _ocr_to_pil_y(bounds["top_right"]["y"], height))
    br = (bounds["bottom_right"]["x"], _ocr_to_pil_y(bounds["bottom_right"]["y"], height))
    bl = (bounds["bottom_left"]["x"], _ocr_to_pil_y(bounds["bottom_left"]["y"], height))
    _draw_polygon(draw, (tl, tr, br, bl), color)


def visualize(records_path: Path, output_dir: Path, orig_bucket: str | None, orig_key: str | None) -> None:
    data = _load_records(records_path)
    receipt = data["receipt"]
    image_id = data.get("image_id") or receipt.get("image_id")

    # Download warped (new) receipt image
    raw_bucket = receipt["raw_s3_bucket"]
    raw_key = receipt["raw_s3_key"]
    warped_path = _download_s3(raw_bucket, raw_key, output_dir / "warped.png")

    # Download original image
    orig_path = output_dir / "original.png"
    if orig_bucket and orig_key:
        _download_s3(orig_bucket, orig_key, orig_path)
    else:
        # Try common patterns using the same bucket
        tried = []
        for ext in ("png", "jpg", "jpeg"):
            candidate = f"raw/{image_id}.{ext}"
            tried.append(candidate)
            try:
                _download_s3(raw_bucket, candidate, orig_path)
                break
            except Exception:
                continue
        else:
            raise RuntimeError(
                f"Could not locate original image. Tried keys: {tried}. Provide --orig-bucket/--orig-key."
            )

    # Load images
    warped_img = Image.open(warped_path).convert("RGB")
    orig_img = Image.open(orig_path).convert("RGB")

    # Draw bounds on original image (bounds are in original OCR coords)
    bounds = data.get("bounds")
    if bounds:
        d = ImageDraw.Draw(orig_img)
        _draw_bounds(d, bounds, orig_img.height, color="red")

    # Draw receipt corners on warped image (normalized OCR coords)
    d2 = ImageDraw.Draw(warped_img)
    corners = {
        "top_left": receipt["top_left"],
        "top_right": receipt["top_right"],
        "bottom_right": receipt["bottom_right"],
        "bottom_left": receipt["bottom_left"],
    }
    _draw_receipt_corners(d2, corners, warped_img.width, warped_img.height, color="blue")

    # Save outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    orig_out = output_dir / "original_with_bounds.png"
    warped_out = output_dir / "warped_with_corners.png"
    orig_img.save(orig_out)
    warped_img.save(warped_out)
    print(f"Saved original overlay -> {orig_out}")
    print(f"Saved warped overlay -> {warped_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize combined receipt overlays.")
    parser.add_argument("--records-bucket", required=False, help="S3 bucket for records JSON")
    parser.add_argument("--records-key", required=False, help="S3 key for records JSON")
    parser.add_argument("--records-path", required=False, help="Local path to records JSON (if not using S3)")
    parser.add_argument("--orig-bucket", required=False, help="S3 bucket for original image (optional)")
    parser.add_argument("--orig-key", required=False, help="S3 key for original image (optional)")
    parser.add_argument("--output-dir", default="./tmp/visualize", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.records_path:
        records_path = Path(args.records_path)
    else:
        if not args.records_bucket or not args.records_key:
            raise SystemExit("Provide --records-path or both --records-bucket and --records-key")
        records_path = output_dir / "records.json"
        _download_s3(args.records_bucket, args.records_key, records_path)

    visualize(records_path, output_dir, args.orig_bucket, args.orig_key)


if __name__ == "__main__":
    main()
