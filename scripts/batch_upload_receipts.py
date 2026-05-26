#!/usr/bin/env python3
"""Batch upload receipt images to the dev (or prod) upload API.

For each image:
  1. POST /upload-receipt → presigned URL + image_id
  2. PUT image bytes to the presigned URL
  3. Log the mapping (filename → image_id)

Usage:
    python scripts/batch_upload_receipts.py /path/to/images/
    python scripts/batch_upload_receipts.py /path/to/images/ --env prod
    python scripts/batch_upload_receipts.py /path/to/images/ --dry-run
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

UPLOAD_DOMAINS = {
    "dev": "https://dev-upload.tylernorlund.com",
    "prod": "https://upload.tylernorlund.com",
}

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".heic": "image/heic",
}


def upload_image(api_base: str, image_path: Path) -> dict:
    content_type = CONTENT_TYPES.get(image_path.suffix.lower(), "image/png")

    resp = requests.post(
        f"{api_base}/upload-receipt",
        json={"filename": image_path.name, "content_type": content_type},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    upload_url = data["upload_url"]
    with open(image_path, "rb") as f:
        put_resp = requests.put(
            upload_url,
            data=f,
            headers={"Content-Type": content_type},
            timeout=120,
        )
        put_resp.raise_for_status()

    return {
        "filename": image_path.name,
        "image_id": data["image_id"],
        "job_id": data["job_id"],
        "s3_key": data["s3_key"],
    }


def main():
    parser = argparse.ArgumentParser(description="Batch upload receipt images")
    parser.add_argument("image_dir", type=Path, help="Directory of images to upload")
    parser.add_argument("--env", default="dev", choices=["dev", "prod"])
    parser.add_argument("--dry-run", action="store_true", help="List files without uploading")
    parser.add_argument("--output", type=Path, help="Write results JSON to file")
    args = parser.parse_args()

    if not args.image_dir.is_dir():
        print(f"Error: {args.image_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    images = sorted(
        p for p in args.image_dir.iterdir()
        if p.suffix.lower() in CONTENT_TYPES
    )

    if not images:
        print(f"No image files found in {args.image_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(images)} images in {args.image_dir}")

    if args.dry_run:
        for img in images:
            print(f"  {img.name} ({img.stat().st_size / 1024:.0f} KB)")
        print(f"\nDry run — {len(images)} images would be uploaded to {args.env}")
        return

    api_base = UPLOAD_DOMAINS[args.env]
    print(f"Uploading to {api_base}")
    print()

    results = []
    failures = []

    for i, img in enumerate(images, 1):
        try:
            result = upload_image(api_base, img)
            results.append(result)
            print(f"[{i}/{len(images)}] {img.name} → {result['image_id']}")
        except Exception as e:
            failures.append({"filename": img.name, "error": str(e)})
            print(f"[{i}/{len(images)}] {img.name} FAILED: {e}", file=sys.stderr)
        if i < len(images):
            time.sleep(0.5)

    print(f"\nDone: {len(results)} uploaded, {len(failures)} failed")

    output = {"env": args.env, "results": results, "failures": failures}

    if args.output:
        args.output.write_text(json.dumps(output, indent=2))
        print(f"Results written to {args.output}")
    else:
        output_path = args.image_dir / f"upload_results_{args.env}.json"
        output_path.write_text(json.dumps(output, indent=2))
        print(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
