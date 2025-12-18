#!/usr/bin/env python3
"""
Format corrupted entities by adding missing required fields with reasonable defaults.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def format_corrupted_entities(
    table_name: str, raw_bucket: str, cdn_bucket: str, dry_run: bool = True
):
    """Format corrupted entities by adding missing required fields."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    stats = {
        "corrupted_found": 0,
        "entities_formatted": 0,
        "errors": 0,
        "images_formatted": 0,
        "receipts_formatted": 0,
    }

    logger.info("Finding and formatting corrupted entities...")

    # Process IMAGE entities
    image_scan_kwargs = {
        "FilterExpression": "#type = :image_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {
            ":image_type": "IMAGE",
            ":pk_prefix": "IMAGE#",
            ":sk_prefix": "IMAGE#",
        },
    }

    corrupted_images = []
    done = False
    start_key = None

    while not done:
        if start_key:
            image_scan_kwargs["ExclusiveStartKey"] = start_key

        response = table.scan(**image_scan_kwargs)
        items = response.get("Items", [])

        for item in items:
            # Check if Image entity is corrupted
            required_fields = [
                "width",
                "height",
                "timestamp_added",
                "raw_s3_bucket",
                "raw_s3_key",
                "image_type",
            ]
            missing_fields = [field for field in required_fields if field not in item]

            if missing_fields:
                corrupted_images.append(
                    {"item": item, "missing_fields": missing_fields}
                )
                stats["corrupted_found"] += 1

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

    logger.info(f"Found {len(corrupted_images)} corrupted IMAGE entities")

    # Process RECEIPT entities
    receipt_scan_kwargs = {
        "FilterExpression": "#type = :receipt_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {
            ":receipt_type": "RECEIPT",
            ":pk_prefix": "IMAGE#",
            ":sk_prefix": "RECEIPT#",
        },
    }

    corrupted_receipts = []
    done = False
    start_key = None

    while not done:
        if start_key:
            receipt_scan_kwargs["ExclusiveStartKey"] = start_key

        response = table.scan(**receipt_scan_kwargs)
        items = response.get("Items", [])

        for item in items:
            # Check if Receipt entity is corrupted
            required_fields = [
                "width",
                "height",
                "timestamp_added",
                "raw_s3_bucket",
                "raw_s3_key",
                "top_left",
                "top_right",
                "bottom_left",
                "bottom_right",
            ]
            missing_fields = [field for field in required_fields if field not in item]

            if missing_fields:
                corrupted_receipts.append(
                    {"item": item, "missing_fields": missing_fields}
                )
                stats["corrupted_found"] += 1

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

    logger.info(f"Found {len(corrupted_receipts)} corrupted RECEIPT entities")
    logger.info(f"Total corrupted entities: {stats['corrupted_found']}")

    # Format IMAGE entities
    def format_image_entity(corrupted_image):
        """Format a single IMAGE entity."""
        try:
            item = corrupted_image["item"]
            missing_fields = corrupted_image["missing_fields"]

            # Extract image ID from PK
            image_id = item["PK"].replace("IMAGE#", "")

            # Create formatted entity with existing data
            formatted_item = dict(item)

            # Add missing fields with reasonable defaults
            if "width" not in formatted_item:
                formatted_item["width"] = 1000  # Default width
            if "height" not in formatted_item:
                formatted_item["height"] = 1000  # Default height
            if "timestamp_added" not in formatted_item:
                formatted_item["timestamp_added"] = datetime.now(
                    timezone.utc
                ).isoformat()
            if "raw_s3_bucket" not in formatted_item:
                formatted_item["raw_s3_bucket"] = raw_bucket
            if "raw_s3_key" not in formatted_item:
                formatted_item["raw_s3_key"] = f"{image_id}.jpg"  # Default to .jpg
            if "image_type" not in formatted_item:
                formatted_item["image_type"] = "JPEG"  # Default type

            # Add other standard fields if missing
            if "site_s3_bucket" not in formatted_item:
                formatted_item["site_s3_bucket"] = cdn_bucket
            if "site_s3_key" not in formatted_item:
                formatted_item["site_s3_key"] = f"{image_id}.jpg"

            # Add CDN fields (initially null) if missing
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
            for field in cdn_fields:
                if field not in formatted_item:
                    formatted_item[field] = None

            logger.info(f"Formatting IMAGE {image_id}: added {missing_fields}")

            if not dry_run:
                table.put_item(Item=formatted_item)

            stats["images_formatted"] += 1
            stats["entities_formatted"] += 1
            return True

        except Exception as e:
            logger.error(f"Failed to format IMAGE {item.get('PK', 'unknown')}: {e}")
            stats["errors"] += 1
            return False

    def format_receipt_entity(corrupted_receipt):
        """Format a single RECEIPT entity."""
        try:
            item = corrupted_receipt["item"]
            missing_fields = corrupted_receipt["missing_fields"]

            # Extract image ID from PK
            image_id = item["PK"].replace("IMAGE#", "")
            receipt_id = item["SK"].replace("RECEIPT#", "")

            # Create formatted entity with existing data
            formatted_item = dict(item)

            # Add missing fields with reasonable defaults
            if "width" not in formatted_item:
                formatted_item["width"] = 1000  # Default width
            if "height" not in formatted_item:
                formatted_item["height"] = 1000  # Default height
            if "timestamp_added" not in formatted_item:
                formatted_item["timestamp_added"] = datetime.now(
                    timezone.utc
                ).isoformat()
            if "raw_s3_bucket" not in formatted_item:
                formatted_item["raw_s3_bucket"] = raw_bucket
            if "raw_s3_key" not in formatted_item:
                formatted_item["raw_s3_key"] = f"{image_id}.jpg"  # Default to .jpg

            # Add bounding box coordinates (default to full image)
            if "top_left" not in formatted_item:
                formatted_item["top_left"] = [0, 0]
            if "top_right" not in formatted_item:
                formatted_item["top_right"] = [formatted_item["width"], 0]
            if "bottom_left" not in formatted_item:
                formatted_item["bottom_left"] = [0, formatted_item["height"]]
            if "bottom_right" not in formatted_item:
                formatted_item["bottom_right"] = [
                    formatted_item["width"],
                    formatted_item["height"],
                ]

            logger.info(
                f"Formatting RECEIPT {image_id}/{receipt_id}: added {missing_fields}"
            )

            if not dry_run:
                table.put_item(Item=formatted_item)

            stats["receipts_formatted"] += 1
            stats["entities_formatted"] += 1
            return True

        except Exception as e:
            logger.error(
                f"Failed to format RECEIPT {item.get('PK', 'unknown')}/{item.get('SK', 'unknown')}: {e}"
            )
            stats["errors"] += 1
            return False

    # Process all entities
    all_entities = []
    for img in corrupted_images:
        all_entities.append(("IMAGE", img, format_image_entity))
    for rcpt in corrupted_receipts:
        all_entities.append(("RECEIPT", rcpt, format_receipt_entity))

    if not all_entities:
        logger.info("No corrupted entities found to format!")
        return

    logger.info(f"Formatting {len(all_entities)} corrupted entities...")

    # Process entities in parallel
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(format_func, entity_data): (entity_type, entity_data)
            for entity_type, entity_data, format_func in all_entities
        }

        completed = 0
        for future in as_completed(futures):
            try:
                future.result()
                completed += 1

                if completed % 25 == 0:
                    logger.info(
                        f"Progress: {completed}/{len(all_entities)} entities formatted"
                    )

            except Exception as e:
                logger.error(f"Error formatting entity: {e}")

    # Print summary
    logger.info("\n" + "=" * 50)
    logger.info("ENTITY FORMATTING SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Corrupted entities found: {stats['corrupted_found']}")
    logger.info(f"Images formatted: {stats['images_formatted']}")
    logger.info(f"Receipts formatted: {stats['receipts_formatted']}")
    logger.info(f"Total entities formatted: {stats['entities_formatted']}")
    logger.info(f"Errors: {stats['errors']}")

    if dry_run:
        logger.info("\nThis was a DRY RUN - no changes were made")
        logger.info("Run with --no-dry-run to apply formatting")
    else:
        logger.info(
            f"\nSuccessfully formatted {stats['entities_formatted']} corrupted entities!"
        )
        logger.info("All entities now have required fields with reasonable defaults.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Format corrupted entities by adding missing required fields"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually format the entities (default is dry run)",
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
    raw_bucket = prod_outputs["raw_bucket_name"].value
    cdn_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"PROD table: {prod_table}")
    logger.info(f"Raw bucket: {raw_bucket}")
    logger.info(f"CDN bucket: {cdn_bucket}")
    logger.info(f"Mode: {'LIVE FORMAT' if args.no_dry_run else 'DRY RUN'}")

    # Format corrupted entities
    format_corrupted_entities(
        prod_table, raw_bucket, cdn_bucket, dry_run=not args.no_dry_run
    )


if __name__ == "__main__":
    main()
