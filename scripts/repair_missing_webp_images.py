#!/usr/bin/env python3
"""
Repair missing WebP images for LabelEvaluator visualization receipts.

This script:
1. Downloads the existing JPEG from S3
2. Generates WebP version
3. Uploads to both dev and prod CDN buckets
"""

import argparse
import logging
import os
import sys
from io import BytesIO

import boto3
from PIL import Image as PIL_Image

# Add parent directory to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)

# Import directly to avoid package init issues
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))
from receipt_upload.utils import upload_webp_to_s3

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# The 14 receipts with missing WebP files
MISSING_WEBP_RECEIPTS = [
    ("082f1bfa-ca36-4f11-ba76-dd4c0b8d83cc", 1),
    ("082f1bfa-ca36-4f11-ba76-dd4c0b8d83cc", 2),
    ("291cfd80-5b29-428e-b650-5e4505c4cbd7", 1),
    ("750e0675-f318-4d1d-994c-c330ff6cb3f3", 1),
    ("750e0675-f318-4d1d-994c-c330ff6cb3f3", 2),
    ("77b07cb3-baaf-459f-a268-57146f6a0020", 3),
    ("864e5b51-3ab1-49d9-9275-4779086efb84", 1),
    ("864e5b51-3ab1-49d9-9275-4779086efb84", 2),
    ("9ab3dffd-de81-48fa-bbde-5bf1664b6fdb", 1),
    ("a2d33e1e-ec1c-4d56-880f-9aa60baa41e7", 1),
    ("b800a56a-ee4b-4315-a241-48ccf5e6237c", 1),
    ("b800a56a-ee4b-4315-a241-48ccf5e6237c", 2),
    ("e9ea77d0-3faa-427f-9b59-83e1180826b7", 1),
    ("fb588a3b-49ab-4ee7-accd-f1953b7378c3", 2),
]


def get_pulumi_outputs(stack_name: str, work_dir: str) -> dict:
    """Get Pulumi stack outputs."""
    from pulumi import automation as auto

    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    outputs = stack.outputs()
    return {k: v.value for k, v in outputs.items()}


def download_jpeg_from_s3(s3_client, bucket: str, key: str) -> PIL_Image.Image:
    """Download JPEG from S3 and return as PIL Image."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    image_data = response["Body"].read()
    return PIL_Image.open(BytesIO(image_data))


def repair_receipt_webp(
    s3_client,
    image_id: str,
    receipt_id: int,
    dev_bucket: str,
    prod_bucket: str,
    dry_run: bool = False,
) -> dict:
    """Repair missing WebP for a single receipt."""
    # Build the S3 keys using the flat pattern
    base_key = f"assets/{image_id}_RECEIPT_{receipt_id:05d}"
    jpeg_key = f"{base_key}.jpg"
    webp_key = f"{base_key}.webp"

    result = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "jpeg_key": jpeg_key,
        "webp_key": webp_key,
        "dev_success": False,
        "prod_success": False,
        "error": None,
    }

    try:
        # Download JPEG from dev bucket (should exist in both, but dev is source of truth)
        logger.info(f"  Downloading JPEG from s3://{dev_bucket}/{jpeg_key}")
        image = download_jpeg_from_s3(s3_client, dev_bucket, jpeg_key)
        logger.info(f"  Image size: {image.width}x{image.height}")

        if dry_run:
            logger.info(f"  [DRY RUN] Would upload WebP to both buckets")
            result["dev_success"] = True
            result["prod_success"] = True
            return result

        # Upload WebP to dev bucket
        logger.info(f"  Uploading WebP to s3://{dev_bucket}/{webp_key}")
        upload_webp_to_s3(image, dev_bucket, webp_key, quality=85)
        result["dev_success"] = True

        # Upload WebP to prod bucket
        logger.info(f"  Uploading WebP to s3://{prod_bucket}/{webp_key}")
        upload_webp_to_s3(image, prod_bucket, webp_key, quality=85)
        result["prod_success"] = True

        logger.info(f"  ✅ Successfully repaired WebP for {image_id}_{receipt_id}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"  ❌ Failed to repair {image_id}_{receipt_id}: {e}")

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Repair missing WebP images for LabelEvaluator receipts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    work_dir = os.path.join(parent_dir, "infra")
    s3_client = boto3.client("s3")

    logger.info("Getting Pulumi stack outputs...")
    dev_outputs = get_pulumi_outputs("tnorlund/portfolio/dev", work_dir)
    prod_outputs = get_pulumi_outputs("tnorlund/portfolio/prod", work_dir)

    dev_bucket = dev_outputs.get("cdn_bucket_name")
    prod_bucket = prod_outputs.get("cdn_bucket_name")

    logger.info(f"DEV CDN bucket: {dev_bucket}")
    logger.info(f"PROD CDN bucket: {prod_bucket}")

    if args.dry_run:
        logger.info("\n*** DRY RUN MODE - No changes will be made ***\n")

    # Process each missing receipt
    results = []
    logger.info(f"\nRepairing {len(MISSING_WEBP_RECEIPTS)} receipts with missing WebP files...\n")

    for image_id, receipt_id in MISSING_WEBP_RECEIPTS:
        logger.info(f"Processing {image_id}_{receipt_id}...")
        result = repair_receipt_webp(
            s3_client,
            image_id,
            receipt_id,
            dev_bucket,
            prod_bucket,
            dry_run=args.dry_run,
        )
        results.append(result)

    # Print summary
    dev_success = sum(1 for r in results if r["dev_success"])
    prod_success = sum(1 for r in results if r["prod_success"])
    failures = sum(1 for r in results if r["error"])

    logger.info("\n" + "=" * 60)
    logger.info("REPAIR SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total receipts processed: {len(results)}")
    logger.info(f"DEV uploads successful: {dev_success}")
    logger.info(f"PROD uploads successful: {prod_success}")
    logger.info(f"Failures: {failures}")

    if failures > 0:
        logger.info("\nFailed receipts:")
        for r in results:
            if r["error"]:
                logger.error(f"  {r['image_id']}_{r['receipt_id']}: {r['error']}")

    if not args.dry_run and dev_success == len(results) and prod_success == len(results):
        logger.info("\n✅ All WebP images successfully repaired!")
    elif args.dry_run:
        logger.info("\n[DRY RUN] No changes were made. Run without --dry-run to apply fixes.")


if __name__ == "__main__":
    main()
