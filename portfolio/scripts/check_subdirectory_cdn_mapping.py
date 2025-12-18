#!/usr/bin/env python3
"""
Check if existing PROD entities can be mapped to subdirectory CDN pattern.
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


def check_subdirectory_pattern_for_entity(
    entity_id: str,
    entity_type: str,
    s3_client,
    cdn_bucket: str,
    receipt_number: str = None,
):
    """Check if subdirectory pattern CDN files exist for an entity."""
    found_files = []

    # For Images: assets/{image_id}/1_{size}.{format}
    # For Receipts: assets/{image_id}/{receipt_number}_{size}.{format}

    if entity_type == "IMAGE":
        patterns = [
            # Thumbnails
            f"assets/{entity_id}/1_thumbnail.jpg",
            f"assets/{entity_id}/1_thumbnail.webp",
            f"assets/{entity_id}/1_thumbnail.avif",
            # Small
            f"assets/{entity_id}/1_small.jpg",
            f"assets/{entity_id}/1_small.webp",
            f"assets/{entity_id}/1_small.avif",
            # Medium
            f"assets/{entity_id}/1_medium.jpg",
            f"assets/{entity_id}/1_medium.webp",
            f"assets/{entity_id}/1_medium.avif",
            # Full size
            f"assets/{entity_id}/1.jpg",
            f"assets/{entity_id}/1.webp",
            f"assets/{entity_id}/1.avif",
        ]
    else:  # RECEIPT
        receipt_num = int(receipt_number.replace("RECEIPT#", ""))
        patterns = [
            # Thumbnails
            f"assets/{entity_id}/{receipt_num}_thumbnail.jpg",
            f"assets/{entity_id}/{receipt_num}_thumbnail.webp",
            f"assets/{entity_id}/{receipt_num}_thumbnail.avif",
            # Small
            f"assets/{entity_id}/{receipt_num}_small.jpg",
            f"assets/{entity_id}/{receipt_num}_small.webp",
            f"assets/{entity_id}/{receipt_num}_small.avif",
            # Medium
            f"assets/{entity_id}/{receipt_num}_medium.jpg",
            f"assets/{entity_id}/{receipt_num}_medium.webp",
            f"assets/{entity_id}/{receipt_num}_medium.avif",
            # Full size
            f"assets/{entity_id}/{receipt_num}.jpg",
            f"assets/{entity_id}/{receipt_num}.webp",
            f"assets/{entity_id}/{receipt_num}.avif",
        ]

    for pattern in patterns:
        try:
            s3_client.head_object(Bucket=cdn_bucket, Key=pattern)
            found_files.append(pattern)
        except s3_client.exceptions.ClientError:
            pass

    return found_files


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check if PROD entities can use subdirectory CDN pattern"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of entities to check",
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

    dynamodb = boto3.resource("dynamodb")
    s3_client = boto3.client("s3")
    table = dynamodb.Table(prod_table)

    # Check a sample of Image entities
    logger.info(f"\nChecking {args.limit} IMAGE entities for subdirectory pattern...")
    image_scan_kwargs = {
        "FilterExpression": "#type = :image_type",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {":image_type": "IMAGE"},
        "Limit": args.limit,
    }

    response = table.scan(**image_scan_kwargs)
    images = response.get("Items", [])

    images_with_subdirectory = 0
    for entity in images:
        entity_id = entity["PK"].replace("IMAGE#", "")
        found_files = check_subdirectory_pattern_for_entity(
            entity_id, "IMAGE", s3_client, cdn_bucket
        )

        if found_files:
            images_with_subdirectory += 1
            logger.info(
                f"✅ Image {entity_id}: {len(found_files)} files in subdirectory pattern"
            )
            logger.info(f"   Sample files: {found_files[:3]}...")
        else:
            logger.info(f"❌ Image {entity_id}: No subdirectory pattern files found")

    # Check a sample of Receipt entities
    logger.info(f"\nChecking {args.limit} RECEIPT entities for subdirectory pattern...")
    receipt_scan_kwargs = {
        "FilterExpression": "#type = :receipt_type",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {":receipt_type": "RECEIPT"},
        "Limit": args.limit,
    }

    response = table.scan(**receipt_scan_kwargs)
    receipts = response.get("Items", [])

    receipts_with_subdirectory = 0
    for entity in receipts:
        image_id = entity["PK"].replace("IMAGE#", "")
        receipt_id = entity["SK"]
        found_files = check_subdirectory_pattern_for_entity(
            image_id, "RECEIPT", s3_client, cdn_bucket, receipt_id
        )

        if found_files:
            receipts_with_subdirectory += 1
            logger.info(
                f"✅ Receipt {image_id}/{receipt_id}: {len(found_files)} files in subdirectory pattern"
            )
            logger.info(f"   Sample files: {found_files[:3]}...")
        else:
            logger.info(
                f"❌ Receipt {image_id}/{receipt_id}: No subdirectory pattern files found"
            )

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUBDIRECTORY PATTERN CHECK SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Images checked: {len(images)}")
    logger.info(f"Images with subdirectory pattern: {images_with_subdirectory}")
    logger.info(f"Receipts checked: {len(receipts)}")
    logger.info(f"Receipts with subdirectory pattern: {receipts_with_subdirectory}")

    if images_with_subdirectory > 0 or receipts_with_subdirectory > 0:
        logger.info("\n✅ Some entities can use subdirectory CDN pattern!")
        logger.info("Consider updating CDN fields to use this pattern.")
    else:
        logger.info("\n❌ No entities found with subdirectory CDN pattern")
        logger.info(
            "The flat pattern seems to be the correct one for current entities."
        )


if __name__ == "__main__":
    main()
