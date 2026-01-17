#!/usr/bin/env python3
"""
Repair receipts missing CDN files.

This script:
1. Queries DynamoDB via GSI2 for all receipts (efficient index query, not scan)
2. Filters locally for receipts where cdn_s3_key is NULL
3. Downloads the raw PNG image from S3
4. Generates all CDN formats (JPEG, WebP, AVIF) in multiple sizes
5. Updates DynamoDB with the new CDN keys
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
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.receipt import Receipt
from receipt_upload.utils import upload_all_cdn_formats

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_pulumi_outputs(stack_name: str, work_dir: str) -> dict:
    """Get Pulumi stack outputs."""
    from pulumi import automation as auto

    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    outputs = stack.outputs()
    return {k: v.value for k, v in outputs.items()}


def get_receipts_missing_cdn(
    dynamo_client: DynamoClient, avif_only: bool = False
) -> list[Receipt]:
    """
    Get all receipts with missing CDN files using efficient GSI2 query.

    Uses list_receipts() which queries GSI2 (much more efficient than scan),
    then filters locally for receipts where cdn_s3_key is None (or cdn_avif_s3_key
    is None if avif_only is True).
    """
    missing_receipts = []
    last_key = None
    total_scanned = 0

    filter_desc = "missing AVIF" if avif_only else "missing CDN"

    while True:
        receipts, last_key = dynamo_client.list_receipts(
            limit=1000,  # Fetch in batches
            last_evaluated_key=last_key,
        )
        total_scanned += len(receipts)

        # Filter for missing keys
        for receipt in receipts:
            if avif_only:
                # Has CDN but missing AVIF
                if receipt.cdn_s3_key and receipt.cdn_avif_s3_key is None:
                    missing_receipts.append(receipt)
            else:
                # Missing CDN entirely
                if receipt.cdn_s3_key is None:
                    missing_receipts.append(receipt)

        logger.info(
            f"  Scanned {total_scanned} receipts, "
            f"found {len(missing_receipts)} {filter_desc}..."
        )

        if last_key is None:
            break

    return missing_receipts


def download_raw_image(s3_client, bucket: str, key: str) -> PIL_Image.Image:
    """Download raw image from S3 and return as PIL Image."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    image_data = response["Body"].read()
    return PIL_Image.open(BytesIO(image_data))


def repair_receipt_cdn(
    s3_client,
    dynamo_client: DynamoClient,
    receipt: Receipt,
    cdn_bucket: str,
    dry_run: bool = False,
    avif_only: bool = False,
) -> dict:
    """
    Repair missing CDN files for a single receipt.

    Downloads the raw image, generates CDN formats, and updates DynamoDB.
    If avif_only is True, only generates AVIF files (skips JPEG/WebP).
    """
    from receipt_upload.utils import upload_avif_to_s3, generate_image_sizes

    result = {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "success": False,
        "error": None,
    }

    try:
        # Download raw image
        logger.info(
            f"  Downloading raw image from "
            f"s3://{receipt.raw_s3_bucket}/{receipt.raw_s3_key}"
        )
        image = download_raw_image(s3_client, receipt.raw_s3_bucket, receipt.raw_s3_key)
        logger.info(f"  Image size: {image.width}x{image.height}")

        # Build base key for CDN files
        base_key = f"assets/{receipt.image_id}_RECEIPT_{receipt.receipt_id:05d}"

        if dry_run:
            mode = "AVIF only" if avif_only else "all CDN"
            logger.info(f"  [DRY RUN] Would upload {mode} files with base_key: {base_key}")
            result["success"] = True
            return result

        if avif_only:
            # Only upload AVIF files
            logger.info(f"  Uploading AVIF files to s3://{cdn_bucket}/{base_key}*.avif")

            # Generate different sizes
            size_configs = {"thumbnail": 300, "small": 600, "medium": 1200}
            sizes = generate_image_sizes(image, size_configs)
            sizes["full"] = image

            avif_keys = {}
            for size_name, sized_image in sizes.items():
                if size_name == "full":
                    size_key = base_key
                else:
                    size_key = f"{base_key}_{size_name}"

                avif_key = f"{size_key}.avif"
                try:
                    upload_avif_to_s3(sized_image, cdn_bucket, avif_key, quality=85)
                    avif_keys[size_name] = avif_key
                except Exception as e:
                    logger.warning(f"  AVIF upload failed for {size_name}: {e}")
                    avif_keys[size_name] = None

            # Update only AVIF fields
            receipt.cdn_avif_s3_key = avif_keys.get("full")
            receipt.cdn_thumbnail_avif_s3_key = avif_keys.get("thumbnail")
            receipt.cdn_small_avif_s3_key = avif_keys.get("small")
            receipt.cdn_medium_avif_s3_key = avif_keys.get("medium")
        else:
            # Generate and upload all CDN formats
            logger.info(f"  Uploading CDN files to s3://{cdn_bucket}/{base_key}.*")
            cdn_keys = upload_all_cdn_formats(
                image=image,
                s3_bucket=cdn_bucket,
                base_key=base_key,
                generate_thumbnails=True,
            )

            # Map returned keys to Receipt fields
            receipt.cdn_s3_bucket = cdn_bucket
            receipt.cdn_s3_key = cdn_keys.get("jpeg")
            receipt.cdn_webp_s3_key = cdn_keys.get("webp")
            receipt.cdn_avif_s3_key = cdn_keys.get("avif")

            # Thumbnail versions
            receipt.cdn_thumbnail_s3_key = cdn_keys.get("jpeg_thumbnail")
            receipt.cdn_thumbnail_webp_s3_key = cdn_keys.get("webp_thumbnail")
            receipt.cdn_thumbnail_avif_s3_key = cdn_keys.get("avif_thumbnail")

            # Small versions
            receipt.cdn_small_s3_key = cdn_keys.get("jpeg_small")
            receipt.cdn_small_webp_s3_key = cdn_keys.get("webp_small")
            receipt.cdn_small_avif_s3_key = cdn_keys.get("avif_small")

            # Medium versions
            receipt.cdn_medium_s3_key = cdn_keys.get("jpeg_medium")
            receipt.cdn_medium_webp_s3_key = cdn_keys.get("webp_medium")
            receipt.cdn_medium_avif_s3_key = cdn_keys.get("avif_medium")

        # Update DynamoDB
        logger.info(f"  Updating DynamoDB record...")
        dynamo_client.update_receipt(receipt)

        result["success"] = True
        mode = "AVIF" if avif_only else "CDN"
        logger.info(
            f"  Successfully repaired {mode} for "
            f"{receipt.image_id}_{receipt.receipt_id}"
        )

    except Exception as e:
        result["error"] = str(e)
        logger.error(
            f"  Failed to repair {receipt.image_id}_{receipt.receipt_id}: {e}"
        )

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Repair receipts missing CDN files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--stack",
        default="tnorlund/portfolio/dev",
        help="Pulumi stack name (default: tnorlund/portfolio/dev)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of receipts to process (for testing)",
    )
    parser.add_argument(
        "--avif-only",
        action="store_true",
        help="Only add AVIF files to receipts that have CDN but missing AVIF",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    work_dir = os.path.join(parent_dir, "infra")
    logger.info(f"Getting Pulumi stack outputs from {args.stack}...")
    outputs = get_pulumi_outputs(args.stack, work_dir)

    table_name = outputs.get("dynamodb_table_name")
    cdn_bucket = outputs.get("cdn_bucket_name")

    logger.info(f"DynamoDB table: {table_name}")
    logger.info(f"CDN bucket: {cdn_bucket}")

    if not table_name or not cdn_bucket:
        logger.error("Missing required Pulumi outputs (dynamodb_table_name, cdn_bucket_name)")
        sys.exit(1)

    # Initialize clients
    s3 = boto3.client("s3")
    dynamo_client = DynamoClient(table_name=table_name)

    if args.dry_run:
        logger.info("\n*** DRY RUN MODE - No changes will be made ***\n")

    # Query for receipts with missing CDN keys (efficient GSI2 query)
    if args.avif_only:
        logger.info("Querying for receipts with CDN but missing AVIF...")
    else:
        logger.info("Querying for receipts with missing CDN files...")
    receipts = get_receipts_missing_cdn(dynamo_client, avif_only=args.avif_only)
    filter_desc = "missing AVIF" if args.avif_only else "missing CDN files"
    logger.info(f"Found {len(receipts)} receipts with {filter_desc}")

    if not receipts:
        logger.info("No receipts need repair!")
        return

    # Apply limit if specified
    if args.limit:
        receipts = receipts[: args.limit]
        logger.info(f"Processing first {args.limit} receipts (--limit)")

    logger.info(f"\nProcessing {len(receipts)} receipts...\n")

    # Process each receipt
    results = []
    for i, receipt in enumerate(receipts, 1):
        logger.info(
            f"[{i}/{len(receipts)}] Processing "
            f"{receipt.image_id}_{receipt.receipt_id}..."
        )
        result = repair_receipt_cdn(
            s3_client=s3,
            dynamo_client=dynamo_client,
            receipt=receipt,
            cdn_bucket=cdn_bucket,
            dry_run=args.dry_run,
            avif_only=args.avif_only,
        )
        results.append(result)

    # Print summary
    successes = sum(1 for r in results if r["success"])
    failures = sum(1 for r in results if r["error"])

    logger.info("\n" + "=" * 60)
    logger.info("REPAIR SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total receipts processed: {len(results)}")
    logger.info(f"Successful: {successes}")
    logger.info(f"Failed: {failures}")

    if failures > 0:
        logger.info("\nFailed receipts:")
        for r in results:
            if r["error"]:
                logger.error(f"  {r['image_id']}_{r['receipt_id']}: {r['error']}")

    if not args.dry_run and successes == len(results):
        logger.info("\nAll receipts successfully repaired!")
        logger.info("\nNext steps:")
        logger.info("1. Verify CDN files exist: aws s3 ls s3://<cdn-bucket>/assets/")
        logger.info("2. Re-run viz cache job to include these receipts")
        logger.info("3. Refresh frontend to confirm images display")
    elif args.dry_run:
        logger.info(
            "\n[DRY RUN] No changes were made. "
            "Run without --dry-run to apply fixes."
        )


if __name__ == "__main__":
    main()
