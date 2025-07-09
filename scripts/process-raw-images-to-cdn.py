#!/usr/bin/env python3
"""
Script to process raw receipt images and upload them in CDN formats (JPEG, WebP, AVIF)
to the site bucket for proper serving through CloudFront.
"""
import argparse
import io
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import boto3
from PIL import Image

# Add the receipt_upload package to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from receipt_upload.utils import (
        download_image_from_s3,
        upload_all_cdn_formats,
    )
except ImportError:
    print("Error: Could not import receipt_upload package.")
    print(
        "Make sure you're running this from the project root and have installed dependencies."
    )
    sys.exit(1)


def get_receipt_images_from_raw_bucket(
    bucket_name: str, prefix: str = "raw/"
) -> List[Dict[str, str]]:
    """Get all receipt images from the raw bucket."""
    s3 = boto3.client("s3")

    receipt_images = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        if "Contents" not in page:
            continue

        for obj in page["Contents"]:
            key = obj["Key"]
            # Match receipt images: {image_id}_RECEIPT_{number}.png
            if re.match(r"^raw/[a-f0-9\-]+_RECEIPT_\d+\.png$", key):
                # Extract image_id and receipt_id
                parts = (
                    key.replace("raw/", "")
                    .replace(".png", "")
                    .split("_RECEIPT_")
                )
                if len(parts) == 2:
                    receipt_images.append(
                        {
                            "key": key,
                            "image_id": parts[0],
                            "receipt_number": parts[1],
                            "base_name": f"{parts[0]}_RECEIPT_{parts[1]}",
                        }
                    )

    return receipt_images


def process_receipt_image(
    raw_bucket: str,
    site_bucket: str,
    raw_key: str,
    base_name: str,
    dry_run: bool = False,
) -> Dict[str, Optional[str]]:
    """Process a single receipt image and upload CDN formats."""
    print(f"\nProcessing: {raw_key}")

    if dry_run:
        print(f"  [DRY RUN] Would download from s3://{raw_bucket}/{raw_key}")
        print(
            f"  [DRY RUN] Would upload to s3://{site_bucket}/assets/{base_name}.[jpg|webp|avif]"
        )
        return {
            "jpeg": f"assets/{base_name}.jpg",
            "webp": f"assets/{base_name}.webp",
            "avif": f"assets/{base_name}.avif",
        }

    try:
        # Download the image
        print(f"  Downloading from s3://{raw_bucket}/{raw_key}")
        # Extract image_id from the key
        image_id = raw_key.split("/")[-1].split("_RECEIPT_")[0]
        image_path = download_image_from_s3(raw_bucket, raw_key, image_id)
        # Open the image with PIL
        from PIL import Image

        image = Image.open(image_path)

        # Upload all CDN formats
        print(
            f"  Uploading CDN formats to s3://{site_bucket}/assets/{base_name}"
        )
        cdn_keys = upload_all_cdn_formats(
            image,
            site_bucket,
            f"assets/{base_name}",
            webp_quality=85,
            avif_quality=85,
        )

        print(
            f"  ✓ Uploaded: {', '.join([k for k in cdn_keys.values() if k])}"
        )
        return cdn_keys

    except Exception as e:
        print(f"  ✗ Error processing {raw_key}: {str(e)}")
        return {}


def check_existing_cdn_images(
    site_bucket: str, base_name: str
) -> Dict[str, bool]:
    """Check which CDN formats already exist for a given image."""
    s3 = boto3.client("s3")
    formats = {
        "jpeg": f"assets/{base_name}.jpg",
        "webp": f"assets/{base_name}.webp",
        "avif": f"assets/{base_name}.avif",
    }

    exists = {}
    for format_name, key in formats.items():
        try:
            s3.head_object(Bucket=site_bucket, Key=key)
            exists[format_name] = True
        except:
            exists[format_name] = False

    return exists


def main():
    parser = argparse.ArgumentParser(
        description="Process raw receipt images and upload CDN formats to site bucket"
    )
    parser.add_argument(
        "--raw-bucket",
        default="raw-image-bucket-0facc78",
        help="S3 bucket containing raw images (default: raw-image-bucket-0facc78)",
    )
    parser.add_argument(
        "--site-bucket",
        default="sitebucket-778abc9",
        help="S3 bucket for CDN images (default: sitebucket-778abc9)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually processing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reprocessing even if CDN images already exist",
    )
    parser.add_argument(
        "--limit", type=int, help="Limit number of images to process"
    )
    parser.add_argument(
        "--image-id", help="Process only images matching this image ID prefix"
    )

    args = parser.parse_args()

    print(f"Raw Bucket: {args.raw_bucket}")
    print(f"Site Bucket: {args.site_bucket}")
    print(f"Dry Run: {args.dry_run}")
    print(f"Force Reprocess: {args.force}")

    # Get list of receipt images
    print("\nScanning for receipt images...")
    receipt_images = get_receipt_images_from_raw_bucket(args.raw_bucket)

    # Filter by image_id if specified
    if args.image_id:
        receipt_images = [
            img
            for img in receipt_images
            if img["image_id"].startswith(args.image_id)
        ]

    print(f"Found {len(receipt_images)} receipt images")

    # Apply limit if specified
    if args.limit:
        receipt_images = receipt_images[: args.limit]
        print(f"Limited to {len(receipt_images)} images")

    # Process each image
    processed = 0
    skipped = 0
    failed = 0

    for img in receipt_images:
        # Check if CDN images already exist
        if not args.force:
            existing = check_existing_cdn_images(
                args.site_bucket, img["base_name"]
            )
            if all(existing.values()):
                print(
                    f"\nSkipping {img['key']} - all CDN formats already exist"
                )
                skipped += 1
                continue

        # Process the image
        result = process_receipt_image(
            args.raw_bucket,
            args.site_bucket,
            img["key"],
            img["base_name"],
            args.dry_run,
        )

        if result:
            processed += 1
        else:
            failed += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Total images found: {len(receipt_images)}")
    print(f"  Processed: {processed}")
    print(f"  Skipped (already exist): {skipped}")
    print(f"  Failed: {failed}")

    if not args.dry_run and processed > 0:
        print(f"\nNext steps:")
        print(f"1. Run the CloudFront invalidation script:")
        print(f"   ./scripts/invalidate-cloudfront-images.sh")
        print(f"2. Wait 10-15 minutes for invalidation to complete")
        print(f"3. Test that images load correctly")


if __name__ == "__main__":
    main()
