#!/usr/bin/env python3
"""
Fix corrupted entities by copying complete data from PITR restore table.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List
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


def fix_corrupted_from_restore(
    prod_table: str, restore_table: str, dry_run: bool = True
):
    """Fix corrupted entities using data from restore table."""
    dynamodb = boto3.resource("dynamodb")
    prod_table_resource = dynamodb.Table(prod_table)
    restore_table_resource = dynamodb.Table(restore_table)

    stats = {
        "corrupted_found": 0,
        "restored_found": 0,
        "entities_fixed": 0,
        "entities_not_in_restore": 0,
        "errors": 0,
    }

    logger.info("Finding corrupted entities in PROD...")

    # Find corrupted IMAGE entities
    corrupted_entities = []

    scan_kwargs = {
        "FilterExpression": "#type = :image_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {
            ":image_type": "IMAGE",
            ":pk_prefix": "IMAGE#",
            ":sk_prefix": "IMAGE#",
        },
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key

        response = prod_table_resource.scan(**scan_kwargs)
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
                corrupted_entities.append(
                    {
                        "PK": item["PK"],
                        "SK": item["SK"],
                        "TYPE": "IMAGE",
                        "missing_fields": missing_fields,
                        "current_item": item,
                    }
                )
                stats["corrupted_found"] += 1

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

        if len(corrupted_entities) % 50 == 0 and len(corrupted_entities) > 0:
            logger.info(
                f"Found {len(corrupted_entities)} corrupted IMAGE entities so far..."
            )

    logger.info(f"Found {len(corrupted_entities)} corrupted IMAGE entities total")

    # Now find corrupted RECEIPT entities
    receipt_scan_kwargs = {
        "FilterExpression": "#type = :receipt_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {
            ":receipt_type": "RECEIPT",
            ":pk_prefix": "IMAGE#",
            ":sk_prefix": "RECEIPT#",
        },
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            receipt_scan_kwargs["ExclusiveStartKey"] = start_key

        response = prod_table_resource.scan(**receipt_scan_kwargs)
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
                corrupted_entities.append(
                    {
                        "PK": item["PK"],
                        "SK": item["SK"],
                        "TYPE": "RECEIPT",
                        "missing_fields": missing_fields,
                        "current_item": item,
                    }
                )
                stats["corrupted_found"] += 1

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

    logger.info(
        f"Found {stats['corrupted_found']} corrupted entities total (Images + Receipts)"
    )

    if not corrupted_entities:
        logger.info("No corrupted entities found!")
        return

    # Fix entities using restore table
    logger.info("Fixing entities using restore table data...")

    def fix_entity(entity_info):
        """Fix a single entity."""
        try:
            # Get the complete entity from restore table
            restore_response = restore_table_resource.get_item(
                Key={"PK": entity_info["PK"], "SK": entity_info["SK"]}
            )

            if "Item" not in restore_response:
                logger.warning(
                    f"Entity not found in restore table: {entity_info['PK']}, {entity_info['SK']}"
                )
                stats["entities_not_in_restore"] += 1
                return False

            restored_item = restore_response["Item"]
            stats["restored_found"] += 1

            # Log what we're fixing
            s3_key = restored_item.get("raw_s3_key", "N/A")
            s3_bucket = restored_item.get("raw_s3_bucket", "N/A")
            logger.info(f"Fixing {entity_info['TYPE']}: {entity_info['PK']}")
            logger.info(f"  S3: {s3_bucket}/{s3_key}")
            logger.info(f"  Was missing: {entity_info['missing_fields']}")

            if not dry_run:
                # Update the entity in PROD table
                prod_table_resource.put_item(Item=restored_item)

            stats["entities_fixed"] += 1
            return True

        except Exception as e:
            logger.error(f"Failed to fix {entity_info['PK']}, {entity_info['SK']}: {e}")
            stats["errors"] += 1
            return False

    # Process entities in parallel
    logger.info(f"Processing {len(corrupted_entities)} entities...")

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fix_entity, entity): entity for entity in corrupted_entities
        }

        completed = 0
        for future in as_completed(futures):
            try:
                future.result()
                completed += 1

                if completed % 25 == 0:
                    logger.info(
                        f"Progress: {completed}/{len(corrupted_entities)} entities processed"
                    )

            except Exception as e:
                logger.error(f"Error processing entity: {e}")

    # Print summary
    logger.info("\n" + "=" * 50)
    logger.info("ENTITY FIXING SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Corrupted entities found: {stats['corrupted_found']}")
    logger.info(f"Entities found in restore table: {stats['restored_found']}")
    logger.info(f"Entities not in restore table: {stats['entities_not_in_restore']}")
    logger.info(f"Entities fixed: {stats['entities_fixed']}")
    logger.info(f"Errors: {stats['errors']}")

    if dry_run:
        logger.info("\nThis was a DRY RUN - no changes were made")
        logger.info("Run with --no-dry-run to apply fixes")
    else:
        logger.info(
            f"\nSuccessfully fixed {stats['entities_fixed']} corrupted entities!"
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fix corrupted entities using PITR restore data"
    )
    parser.add_argument(
        "--restore-table",
        default="ReceiptsTable-d7ff76a-pitr-test-20250711-191032",
        help="Name of the restore table to use",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually fix the entities (default is dry run)",
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

    logger.info(f"PROD table: {prod_table}")
    logger.info(f"Restore table: {args.restore_table}")
    logger.info(f"Mode: {'LIVE FIX' if args.no_dry_run else 'DRY RUN'}")

    # Fix corrupted entities
    fix_corrupted_from_restore(
        prod_table, args.restore_table, dry_run=not args.no_dry_run
    )


if __name__ == "__main__":
    main()
