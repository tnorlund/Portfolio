#!/usr/bin/env python3
"""
Check if CDN images exist in the bucket and update entity records accordingly.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional
import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_cdn_images(table_name: str, cdn_bucket: str, limit: int = 10):
    """Check if CDN images exist for a sample of Image entities."""
    dynamodb = boto3.resource("dynamodb")
    s3 = boto3.client("s3")
    table = dynamodb.Table(table_name)

    logger.info(f"Checking CDN images for sample of Image entities in {cdn_bucket}...")

    # Get sample Image entities
    scan_kwargs = {
        "FilterExpression": "#type = :image_type",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {":image_type": "IMAGE"},
        "Limit": limit,
    }

    response = table.scan(**scan_kwargs)
    image_entities = response.get("Items", [])

    logger.info(f"Checking {len(image_entities)} sample Image entities...")

    stats = {
        "entities_checked": 0,
        "entities_with_cdn_nulls": 0,
        "entities_with_cdn_values": 0,
        "cdn_images_found": 0,
        "cdn_images_missing": 0,
    }

    for entity in image_entities:
        stats["entities_checked"] += 1
        image_id = entity["PK"].replace("IMAGE#", "")

        logger.info(f"\nChecking Image: {image_id}")

        # Check current CDN field values
        cdn_fields = [
            "cdn_thumbnail_s3_key",
            "cdn_thumbnail_webp_s3_key",
            "cdn_thumbnail_avif_s3_key",
            "cdn_small_s3_key",
            "cdn_small_webp_s3_key",
            "cdn_small_avif_s3_key",
            "cdn_medium_s3_key",
            "cdn_medium_webp_s3_key",
            "cdn_medium_avif_s3_key",
        ]

        has_null_cdn = any(entity.get(field) is None for field in cdn_fields)
        has_value_cdn = any(entity.get(field) is not None for field in cdn_fields)

        if has_null_cdn:
            stats["entities_with_cdn_nulls"] += 1
        if has_value_cdn:
            stats["entities_with_cdn_values"] += 1

        logger.info(
            f"  CDN fields: {sum(1 for field in cdn_fields if entity.get(field) is not None)}/9 populated"
        )

        # Check if CDN images actually exist in S3
        expected_cdn_files = [
            f"{image_id}_thumbnail.jpg",
            f"{image_id}_thumbnail.webp",
            f"{image_id}_thumbnail.avif",
            f"{image_id}_small.jpg",
            f"{image_id}_small.webp",
            f"{image_id}_small.avif",
            f"{image_id}_medium.jpg",
            f"{image_id}_medium.webp",
            f"{image_id}_medium.avif",
        ]

        existing_files = []
        for file_key in expected_cdn_files:
            try:
                s3.head_object(Bucket=cdn_bucket, Key=file_key)
                existing_files.append(file_key)
                stats["cdn_images_found"] += 1
            except s3.exceptions.ClientError:
                stats["cdn_images_missing"] += 1

        if existing_files:
            logger.info(f"  Found CDN files: {existing_files}")
        else:
            logger.info(f"  No CDN files found in bucket")

        # Show current field values vs what should be there
        for field, expected_file in zip(cdn_fields, expected_cdn_files):
            current_value = entity.get(field)
            file_exists = expected_file in existing_files

            if file_exists and current_value is None:
                logger.info(f"  ⚠️  {field}: NULL but {expected_file} exists in bucket")
            elif not file_exists and current_value is not None:
                logger.info(
                    f"  ⚠️  {field}: {current_value} but file missing from bucket"
                )
            elif file_exists and current_value == expected_file:
                logger.info(f"  ✅ {field}: {current_value} (correct)")

    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("CDN IMAGE CHECK SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Entities checked: {stats['entities_checked']}")
    logger.info(f"Entities with NULL CDN fields: {stats['entities_with_cdn_nulls']}")
    logger.info(
        f"Entities with populated CDN fields: {stats['entities_with_cdn_values']}"
    )
    logger.info(f"CDN images found in bucket: {stats['cdn_images_found']}")
    logger.info(f"CDN images missing from bucket: {stats['cdn_images_missing']}")

    if stats["cdn_images_found"] > 0:
        logger.info(f"\n✅ CDN images exist in bucket - entities should be updated")
    else:
        logger.info(f"\n❌ No CDN images found in bucket")

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check if CDN images exist and compare with entity records"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of Image entities to check (default: 10)",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    from pulumi import automation as auto

    work_dir = os.path.join(parent_dir, "infra")

    logger.info("Getting PROD configuration...")
    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()
    prod_table = prod_outputs["dynamodb_table_name"].value
    cdn_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"PROD table: {prod_table}")
    logger.info(f"CDN bucket: {cdn_bucket}")

    # Check CDN images
    check_cdn_images(prod_table, cdn_bucket, args.limit)


if __name__ == "__main__":
    main()
