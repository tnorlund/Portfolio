#!/usr/bin/env python3
"""
Verify that LabelEvaluator visualization receipts have valid CDN images in S3.

This script:
1. Fetches the visualization cache from S3
2. Checks every CDN key to ensure images exist in both dev and prod buckets
3. Reports any missing images
"""

import argparse
import json
import logging
import os
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Add parent directory to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_pulumi_outputs(stack_name: str, work_dir: str) -> dict[str, Any]:
    """Get Pulumi stack outputs."""
    from pulumi import automation as auto

    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    outputs = stack.outputs()
    return {k: v.value for k, v in outputs.items()}


def check_s3_object_exists(s3_client, bucket: str, key: str) -> bool:
    """Check if an S3 object exists."""
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def get_visualization_cache(s3_client, cache_bucket: str) -> list[dict[str, Any]]:
    """Fetch all receipt cache files from the label evaluator viz cache bucket."""
    receipts = []
    prefix = "receipts/"

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=cache_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".json"):
                    try:
                        response = s3_client.get_object(Bucket=cache_bucket, Key=key)
                        receipt_data = json.loads(response["Body"].read().decode("utf-8"))
                        receipts.append(receipt_data)
                    except ClientError as e:
                        logger.warning("Failed to fetch %s: %s", key, e)
    except ClientError as e:
        logger.error("Failed to list cache files from %s/%s: %s", cache_bucket, prefix, e)

    return receipts


def verify_receipt_cdn_images(
    receipt: dict[str, Any],
    s3_client,
    cdn_bucket: str,
    environment: str,
) -> dict[str, Any]:
    """Verify all CDN images for a single receipt."""
    image_id = receipt.get("image_id", "unknown")
    receipt_id = receipt.get("receipt_id", "unknown")
    receipt_key = f"{image_id}_{receipt_id}"

    # CDN fields to check - full and medium sizes for all formats
    cdn_fields = [
        ("cdn_s3_key", "Full JPEG"),
        ("cdn_webp_s3_key", "Full WebP"),
        ("cdn_avif_s3_key", "Full AVIF"),
        ("cdn_medium_s3_key", "Medium JPEG"),
        ("cdn_medium_webp_s3_key", "Medium WebP"),
        ("cdn_medium_avif_s3_key", "Medium AVIF"),
    ]

    result = {
        "receipt_key": receipt_key,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "environment": environment,
        "fields_checked": 0,
        "fields_populated": 0,
        "images_found": 0,
        "images_missing": [],
        "empty_fields": [],
    }

    for field_name, field_desc in cdn_fields:
        result["fields_checked"] += 1
        cdn_key = receipt.get(field_name)

        if not cdn_key:
            result["empty_fields"].append(f"{field_desc} ({field_name})")
            continue

        result["fields_populated"] += 1

        if check_s3_object_exists(s3_client, cdn_bucket, cdn_key):
            result["images_found"] += 1
        else:
            result["images_missing"].append({
                "field": field_name,
                "description": field_desc,
                "s3_key": cdn_key,
            })

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Verify LabelEvaluator visualization CDN images"
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod", "both"],
        default="both",
        help="Environment(s) to check (default: both)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output for each receipt",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    work_dir = os.path.join(parent_dir, "infra")
    s3_client = boto3.client("s3")

    environments = []
    if args.env in ("dev", "both"):
        environments.append(("dev", "tnorlund/portfolio/dev"))
    if args.env in ("prod", "both"):
        environments.append(("prod", "tnorlund/portfolio/prod"))

    # Store results for comparison
    all_results = {}

    for env_name, stack_name in environments:
        logger.info("=" * 60)
        logger.info(f"Checking {env_name.upper()} environment")
        logger.info("=" * 60)

        # Get Pulumi outputs
        logger.info("Getting Pulumi stack outputs...")
        outputs = get_pulumi_outputs(stack_name, work_dir)

        cdn_bucket = outputs.get("cdn_bucket_name")
        cache_bucket = outputs.get("label_evaluator_viz_cache_bucket")

        if not cdn_bucket:
            logger.error(f"Could not determine CDN bucket for {env_name}")
            continue

        if not cache_bucket:
            logger.error(f"Could not determine viz cache bucket for {env_name}")
            continue

        logger.info(f"CDN bucket: {cdn_bucket}")
        logger.info(f"Viz cache bucket: {cache_bucket}")

        # Fetch visualization cache
        logger.info("Fetching visualization cache receipts...")
        receipts = get_visualization_cache(s3_client, cache_bucket)

        if not receipts:
            logger.error(f"No visualization cache receipts found for {env_name}")
            continue
        logger.info(f"Found {len(receipts)} receipts in visualization cache")

        # Check each receipt
        env_results = {
            "receipts_checked": 0,
            "receipts_with_all_images": 0,
            "receipts_with_missing_images": 0,
            "receipts_with_empty_fields": 0,
            "total_images_found": 0,
            "total_images_missing": 0,
            "missing_details": [],
        }

        for receipt in receipts:
            result = verify_receipt_cdn_images(receipt, s3_client, cdn_bucket, env_name)
            env_results["receipts_checked"] += 1
            env_results["total_images_found"] += result["images_found"]
            env_results["total_images_missing"] += len(result["images_missing"])

            has_issues = False

            if result["images_missing"]:
                env_results["receipts_with_missing_images"] += 1
                env_results["missing_details"].append(result)
                has_issues = True

            if result["empty_fields"]:
                env_results["receipts_with_empty_fields"] += 1
                has_issues = True

            if not has_issues and result["images_found"] == result["fields_populated"]:
                env_results["receipts_with_all_images"] += 1

            if args.verbose or has_issues:
                logger.info(f"\n  Receipt: {result['receipt_key']}")
                logger.info(f"    Fields populated: {result['fields_populated']}/{result['fields_checked']}")
                logger.info(f"    Images found: {result['images_found']}/{result['fields_populated']}")

                if result["empty_fields"]:
                    logger.warning(f"    Empty CDN fields: {', '.join(result['empty_fields'])}")

                if result["images_missing"]:
                    for missing in result["images_missing"]:
                        logger.error(f"    ❌ Missing: {missing['description']} -> s3://{cdn_bucket}/{missing['s3_key']}")

        # Print environment summary
        logger.info(f"\n{env_name.upper()} Summary:")
        logger.info(f"  Receipts checked: {env_results['receipts_checked']}")
        logger.info(f"  Receipts with all images: {env_results['receipts_with_all_images']}")
        logger.info(f"  Receipts with missing images: {env_results['receipts_with_missing_images']}")
        logger.info(f"  Receipts with empty CDN fields: {env_results['receipts_with_empty_fields']}")
        logger.info(f"  Total images found: {env_results['total_images_found']}")
        logger.info(f"  Total images missing: {env_results['total_images_missing']}")

        all_results[env_name] = env_results

    # Cross-environment comparison if both were checked
    if len(all_results) == 2:
        logger.info("\n" + "=" * 60)
        logger.info("CROSS-ENVIRONMENT COMPARISON")
        logger.info("=" * 60)

        dev_missing = set()
        prod_missing = set()

        if "dev" in all_results:
            for detail in all_results["dev"]["missing_details"]:
                for missing in detail["images_missing"]:
                    dev_missing.add(missing["s3_key"])

        if "prod" in all_results:
            for detail in all_results["prod"]["missing_details"]:
                for missing in detail["images_missing"]:
                    prod_missing.add(missing["s3_key"])

        only_missing_in_dev = dev_missing - prod_missing
        only_missing_in_prod = prod_missing - dev_missing
        missing_in_both = dev_missing & prod_missing

        if only_missing_in_dev:
            logger.warning(f"\nImages missing ONLY in DEV ({len(only_missing_in_dev)}):")
            for key in sorted(only_missing_in_dev)[:10]:
                logger.warning(f"  - {key}")
            if len(only_missing_in_dev) > 10:
                logger.warning(f"  ... and {len(only_missing_in_dev) - 10} more")

        if only_missing_in_prod:
            logger.warning(f"\nImages missing ONLY in PROD ({len(only_missing_in_prod)}):")
            for key in sorted(only_missing_in_prod)[:10]:
                logger.warning(f"  - {key}")
            if len(only_missing_in_prod) > 10:
                logger.warning(f"  ... and {len(only_missing_in_prod) - 10} more")

        if missing_in_both:
            logger.error(f"\nImages missing in BOTH environments ({len(missing_in_both)}):")
            for key in sorted(missing_in_both)[:10]:
                logger.error(f"  - {key}")
            if len(missing_in_both) > 10:
                logger.error(f"  ... and {len(missing_in_both) - 10} more")

        if not dev_missing and not prod_missing:
            logger.info("✅ All visualization receipts have valid CDN images in both environments!")


if __name__ == "__main__":
    main()
