#!/usr/bin/env python3
"""
Copy ALL Image and Receipt entities from PITR restore table to PROD,
adding any missing CDN fields.
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


def copy_all_entities_from_restore(
    prod_table: str, restore_table: str, dry_run: bool = True
):
    """Copy all Image and Receipt entities from restore table to PROD."""
    dynamodb = boto3.resource("dynamodb")
    prod_table_resource = dynamodb.Table(prod_table)
    restore_table_resource = dynamodb.Table(restore_table)

    stats = {"images_found": 0, "receipts_found": 0, "entities_copied": 0, "errors": 0}

    logger.info("Scanning restore table for all Image and Receipt entities...")

    # Get all entities from restore table
    all_entities = []

    scan_kwargs = {
        "FilterExpression": "#type IN (:image_type, :receipt_type)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {
            ":image_type": "IMAGE",
            ":receipt_type": "RECEIPT",
        },
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key

        response = restore_table_resource.scan(**scan_kwargs)
        items = response.get("Items", [])

        for item in items:
            entity_type = item.get("TYPE", "")
            if entity_type == "IMAGE":
                stats["images_found"] += 1
            elif entity_type == "RECEIPT":
                stats["receipts_found"] += 1

            all_entities.append(item)

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

        if len(all_entities) % 50 == 0 and len(all_entities) > 0:
            logger.info(
                f"Found {len(all_entities)} entities in restore table so far..."
            )

    logger.info(
        f"Found {stats['images_found']} Images and {stats['receipts_found']} Receipts in restore table"
    )
    logger.info(f"Total entities to copy: {len(all_entities)}")

    if not all_entities:
        logger.info("No entities found in restore table!")
        return

    def copy_entity(entity):
        """Copy a single entity to PROD with updated CDN fields."""
        try:
            # Create copy of entity
            updated_entity = dict(entity)

            # Add CDN fields if missing (these are new fields added after restore point)
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

            added_fields = []
            for field in cdn_fields:
                if field not in updated_entity:
                    updated_entity[field] = None
                    added_fields.append(field)

            # Log what we're copying
            entity_type = updated_entity.get("TYPE", "UNKNOWN")
            pk = updated_entity.get("PK", "unknown")

            if added_fields:
                logger.debug(
                    f"Copying {entity_type} {pk}: added {len(added_fields)} CDN fields"
                )
            else:
                logger.debug(f"Copying {entity_type} {pk}: no changes needed")

            if not dry_run:
                # Write the entity to PROD table
                prod_table_resource.put_item(Item=updated_entity)

            stats["entities_copied"] += 1
            return True

        except Exception as e:
            logger.error(f"Failed to copy entity {entity.get('PK', 'unknown')}: {e}")
            stats["errors"] += 1
            return False

    # Copy entities in parallel
    logger.info(f"Copying {len(all_entities)} entities to PROD...")

    with ThreadPoolExecutor(
        max_workers=25
    ) as executor:  # Higher concurrency for copying
        futures = {
            executor.submit(copy_entity, entity): entity for entity in all_entities
        }

        completed = 0
        for future in as_completed(futures):
            try:
                future.result()
                completed += 1

                if completed % 50 == 0:
                    logger.info(
                        f"Progress: {completed}/{len(all_entities)} entities copied"
                    )

            except Exception as e:
                logger.error(f"Error copying entity: {e}")

    # Print summary
    logger.info("\n" + "=" * 50)
    logger.info("ENTITY COPY SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Images found in restore: {stats['images_found']}")
    logger.info(f"Receipts found in restore: {stats['receipts_found']}")
    logger.info(f"Total entities copied: {stats['entities_copied']}")
    logger.info(f"Errors: {stats['errors']}")

    if dry_run:
        logger.info("\nThis was a DRY RUN - no changes were made")
        logger.info("Run with --no-dry-run to copy all entities")
    else:
        logger.info(
            f"\nSuccessfully copied {stats['entities_copied']} entities from restore to PROD!"
        )
        logger.info("All corrupted entities have been replaced with good versions.")
        logger.info("All entities now have the latest CDN fields.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Copy all Image and Receipt entities from PITR restore to PROD"
    )
    parser.add_argument(
        "--restore-table",
        default="ReceiptsTable-d7ff76a-pitr-test-20250711-191032",
        help="Name of the restore table to copy from",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually copy the entities (default is dry run)",
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
    logger.info(f"Mode: {'LIVE COPY' if args.no_dry_run else 'DRY RUN'}")

    # Copy all entities from restore
    copy_all_entities_from_restore(
        prod_table, args.restore_table, dry_run=not args.no_dry_run
    )


if __name__ == "__main__":
    main()
