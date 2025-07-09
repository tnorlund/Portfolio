#!/usr/bin/env python3
"""
Script to process base images (not receipt extracts) and upload them in CDN formats
to the site bucket for the ImageStack component.
"""
import argparse
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


def get_base_images_from_raw_bucket(
    bucket_name: str, prefix: str = "raw/"
) -> List[Dict[str, str]]:
    """Get all base images (not receipt extracts) from the raw bucket."""
    s3 = boto3.client("s3")

    base_images = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        if "Contents" not in page:
            continue

        for obj in page["Contents"]:
            key = obj["Key"]
            # Match base images: {image_id}.png (NOT containing _RECEIPT_)
            if key.endswith(".png") and "_RECEIPT_" not in key:
                # Extract image_id
                image_id = key.replace("raw/", "").replace(".png", "")
                # Skip if it's not a valid UUID-like pattern
                if re.match(r"^[a-f0-9\-]+$", image_id):
                    base_images.append(
                        {
                            "key": key,
                            "image_id": image_id,
                            "base_name": image_id,
                        }
                    )

    return base_images


def process_base_image(
    raw_bucket: str,
    site_bucket: str,
    raw_key: str,
    image_id: str,
    base_name: str,
    dry_run: bool = False,
) -> Dict[str, Optional[str]]:
    """Process a single base image and upload CDN formats."""
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
        image_path = download_image_from_s3(raw_bucket, raw_key, image_id)
        # Open the image with PIL
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

        # Clean up temp file
        image_path.unlink()

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
        description="Process base images (not receipts) and upload CDN formats to site bucket"
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

    # Get list of base images
    print("\nScanning for base images (non-receipt)...")
    base_images = get_base_images_from_raw_bucket(args.raw_bucket)

    # Filter by image_id if specified
    if args.image_id:
        base_images = [
            img
            for img in base_images
            if img["image_id"].startswith(args.image_id)
        ]

    print(f"Found {len(base_images)} base images")

    # Apply limit if specified
    if args.limit:
        base_images = base_images[: args.limit]
        print(f"Limited to {len(base_images)} images")

    # Process each image
    processed = 0
    skipped = 0
    failed = 0

    for img in base_images:
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
        result = process_base_image(
            args.raw_bucket,
            args.site_bucket,
            img["key"],
            img["image_id"],
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
    print(f"  Total base images found: {len(base_images)}")
    print(f"  Processed: {processed}")
    print(f"  Skipped (already exist): {skipped}")
    print(f"  Failed: {failed}")

    if not args.dry_run and processed > 0:
        print(f"\nNext steps:")
        print(f"1. Run the CloudFront invalidation script:")
        print(f"   ./scripts/invalidate-cloudfront-images.sh")
        print(f"2. Wait 10-15 minutes for invalidation to complete")
        print(
            f"3. Test that images load correctly in the ImageStack component"
        )


if __name__ == "__main__":
    main()
