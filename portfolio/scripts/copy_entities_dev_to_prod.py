#!/usr/bin/env python3
"""
Script to copy corrupted Image and Receipt entities from DEV to PROD
with corrected bucket names.

This script:
1. Identifies corrupted entities in PROD (those with TYPE but missing other fields)
2. Fetches the correct entity from DEV
3. Updates bucket names to PROD values
4. Writes the corrected entity to PROD
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Receipt, Image, item_to_image, item_to_receipt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class EntityCopier:
    """Copies entities from DEV to PROD with bucket name corrections."""

    def __init__(
        self,
        dev_table: str,
        prod_table: str,
        dev_raw_bucket: str,
        prod_raw_bucket: str,
        dev_site_bucket: str,
        prod_site_bucket: str,
        dry_run: bool = True,
    ):
        self.dev_client = DynamoClient(dev_table)
        self.prod_client = DynamoClient(prod_table)

        # Also create direct table access for checking corrupted records
        self.dynamodb = boto3.resource("dynamodb")
        self.dev_table = self.dynamodb.Table(dev_table)
        self.prod_table = self.dynamodb.Table(prod_table)

        self.dev_raw_bucket = dev_raw_bucket
        self.prod_raw_bucket = prod_raw_bucket
        self.dev_site_bucket = dev_site_bucket
        self.prod_site_bucket = prod_site_bucket

        self.dry_run = dry_run

        self.stats = {
            "images_checked": 0,
            "receipts_checked": 0,
            "images_corrupted": 0,
            "receipts_corrupted": 0,
            "images_fixed": 0,
            "receipts_fixed": 0,
            "errors": 0,
        }

        self.corrupted_entities = []

    def find_corrupted_entities(self):
        """Find all corrupted entities in PROD."""
        logger.info("Scanning PROD for corrupted entities...")

        # We already know which entities were corrupted - they were the ones missing TYPE
        # Let's check those specific entities from our previous scan

        # First, let's scan for entities that have TYPE but might be missing other fields
        scan_kwargs = {
            "FilterExpression": "attribute_exists(#type)",
            "ExpressionAttributeNames": {"#type": "TYPE"},
        }

        done = False
        start_key = None

        while not done:
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key

            response = self.prod_table.scan(**scan_kwargs)
            items = response.get("Items", [])

            for item in items:
                pk = item.get("PK", "")
                sk = item.get("SK", "")
                entity_type = item.get("TYPE", "")

                if (
                    entity_type == "IMAGE"
                    and pk.startswith("IMAGE#")
                    and sk.startswith("IMAGE#")
                ):
                    self.stats["images_checked"] += 1
                    # Check if Image entity is corrupted (missing required fields)
                    if self.is_image_corrupted(item):
                        # First check if this entity exists in DEV
                        dev_item = self.dev_table.get_item(
                            Key={"PK": pk, "SK": sk}
                        ).get("Item")
                        if dev_item:
                            self.stats["images_corrupted"] += 1
                            self.corrupted_entities.append(("IMAGE", pk, sk))
                            logger.debug(
                                f"Found corrupted Image that exists in DEV: {pk}"
                            )
                        else:
                            logger.warning(f"Corrupted Image not found in DEV: {pk}")

                elif (
                    entity_type == "RECEIPT"
                    and pk.startswith("IMAGE#")
                    and sk.startswith("RECEIPT#")
                ):
                    self.stats["receipts_checked"] += 1
                    # Check if Receipt entity is corrupted (missing required fields)
                    if self.is_receipt_corrupted(item):
                        # First check if this entity exists in DEV
                        dev_item = self.dev_table.get_item(
                            Key={"PK": pk, "SK": sk}
                        ).get("Item")
                        if dev_item:
                            self.stats["receipts_corrupted"] += 1
                            self.corrupted_entities.append(("RECEIPT", pk, sk))
                            logger.debug(
                                f"Found corrupted Receipt that exists in DEV: {pk}, {sk}"
                            )
                        else:
                            logger.warning(
                                f"Corrupted Receipt not found in DEV: {pk}, {sk}"
                            )

            start_key = response.get("LastEvaluatedKey", None)
            done = start_key is None

            # Progress update
            total_checked = (
                self.stats["images_checked"] + self.stats["receipts_checked"]
            )
            total_corrupted = (
                self.stats["images_corrupted"] + self.stats["receipts_corrupted"]
            )
            logger.info(
                f"Checked {total_checked} entities, found {total_corrupted} corrupted that exist in DEV..."
            )

    def is_image_corrupted(self, item: Dict[str, Any]) -> bool:
        """Check if an Image entity is corrupted."""
        required_fields = [
            "width",
            "height",
            "timestamp_added",
            "raw_s3_bucket",
            "raw_s3_key",
            "image_type",
        ]
        for field in required_fields:
            if field not in item:
                return True
        return False

    def is_receipt_corrupted(self, item: Dict[str, Any]) -> bool:
        """Check if a Receipt entity is corrupted."""
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
        for field in required_fields:
            if field not in item:
                return True
        return False

    def fix_corrupted_entity(self, entity_type: str, pk: str, sk: str) -> bool:
        """Fix a single corrupted entity by copying from DEV."""
        try:
            # Get the entity from DEV
            dev_item = self.dev_table.get_item(Key={"PK": pk, "SK": sk}).get("Item")

            if not dev_item:
                logger.warning(f"{entity_type} not found in DEV: {pk}, {sk}")
                return False

            # Update bucket names
            if (
                "raw_s3_bucket" in dev_item
                and dev_item["raw_s3_bucket"] == self.dev_raw_bucket
            ):
                dev_item["raw_s3_bucket"] = self.prod_raw_bucket

            if (
                "cdn_s3_bucket" in dev_item
                and dev_item["cdn_s3_bucket"] == self.dev_site_bucket
            ):
                dev_item["cdn_s3_bucket"] = self.prod_site_bucket

            if not self.dry_run:
                # Write the corrected entity to PROD
                self.prod_table.put_item(Item=dev_item)

                if entity_type == "IMAGE":
                    self.stats["images_fixed"] += 1
                else:
                    self.stats["receipts_fixed"] += 1

                logger.debug(f"Fixed {entity_type}: {pk}, {sk}")
            else:
                if entity_type == "IMAGE":
                    self.stats["images_fixed"] += 1
                else:
                    self.stats["receipts_fixed"] += 1

            return True

        except Exception as e:
            logger.error(f"Failed to fix {entity_type} {pk}, {sk}: {e}")
            self.stats["errors"] += 1
            return False

    def fix_all_corrupted_entities(self):
        """Fix all corrupted entities found."""
        if not self.corrupted_entities:
            logger.info("No corrupted entities to fix!")
            return

        logger.info(f"Fixing {len(self.corrupted_entities)} corrupted entities...")

        # Process in batches with threading
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self.fix_corrupted_entity, entity_type, pk, sk): (
                    entity_type,
                    pk,
                    sk,
                )
                for entity_type, pk, sk in self.corrupted_entities
            }

            completed = 0
            for future in as_completed(futures):
                entity_info = futures[future]
                try:
                    future.result()
                    completed += 1

                    if completed % 10 == 0:
                        logger.info(
                            f"Progress: {completed}/{len(self.corrupted_entities)} entities fixed"
                        )

                except Exception as e:
                    logger.error(f"Failed to process {entity_info}: {e}")

    def run(self):
        """Run the complete copy process."""
        # Find corrupted entities
        self.find_corrupted_entities()

        # Fix them
        self.fix_all_corrupted_entities()

        # Print summary
        self.print_summary()

    def print_summary(self):
        """Print summary of the operation."""
        logger.info("\n" + "=" * 50)
        logger.info("ENTITY COPY SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Images checked: {self.stats['images_checked']}")
        logger.info(f"Images corrupted: {self.stats['images_corrupted']}")
        logger.info(f"Receipts checked: {self.stats['receipts_checked']}")
        logger.info(f"Receipts corrupted: {self.stats['receipts_corrupted']}")

        if not self.dry_run:
            logger.info(f"Images fixed: {self.stats['images_fixed']}")
            logger.info(f"Receipts fixed: {self.stats['receipts_fixed']}")
            logger.info(f"Errors: {self.stats['errors']}")
        else:
            logger.info(f"Images to be fixed: {self.stats['images_fixed']}")
            logger.info(f"Receipts to be fixed: {self.stats['receipts_fixed']}")
            logger.info("\nThis was a DRY RUN - no changes were made")
            logger.info("Run with --no-dry-run to apply changes")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Copy corrupted entities from DEV to PROD with corrected bucket names"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually copy the entities (default is dry run)",
    )

    args = parser.parse_args()

    # Get configurations from Pulumi for both stacks
    from pulumi import automation as auto

    work_dir = os.path.join(parent_dir, "infra")

    # Get DEV configuration
    logger.info("Getting DEV configuration...")
    dev_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/dev",
        work_dir=work_dir,
    )
    dev_outputs = dev_stack.outputs()
    dev_table = dev_outputs["dynamodb_table_name"].value
    dev_raw_bucket = dev_outputs["raw_bucket_name"].value
    dev_site_bucket = dev_outputs["cdn_bucket_name"].value

    # Get PROD configuration
    logger.info("Getting PROD configuration...")
    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()
    prod_table = prod_outputs["dynamodb_table_name"].value
    prod_raw_bucket = prod_outputs["raw_bucket_name"].value
    prod_site_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"DEV table: {dev_table}")
    logger.info(f"PROD table: {prod_table}")
    logger.info(f"DEV buckets: raw={dev_raw_bucket}, site={dev_site_bucket}")
    logger.info(f"PROD buckets: raw={prod_raw_bucket}, site={prod_site_bucket}")
    logger.info(f"Mode: {'LIVE UPDATE' if args.no_dry_run else 'DRY RUN'}")

    # Create and run copier
    copier = EntityCopier(
        dev_table=dev_table,
        prod_table=prod_table,
        dev_raw_bucket=dev_raw_bucket,
        prod_raw_bucket=prod_raw_bucket,
        dev_site_bucket=dev_site_bucket,
        prod_site_bucket=prod_site_bucket,
        dry_run=not args.no_dry_run,
    )

    copier.run()


if __name__ == "__main__":
    main()
