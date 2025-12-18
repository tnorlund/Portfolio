#!/usr/bin/env python3
"""
Script to check and fix missing TYPE field in Image and Receipt records.

This script:
1. Scans for Image and Receipt entities missing the TYPE field
2. Adds the TYPE field to any records that are missing it
"""

import argparse
import logging
import os
import sys
from typing import List, Dict, Any, Tuple

import boto3
from boto3.dynamodb.conditions import Attr

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Receipt, Image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class TypeFieldFixer:
    """Handles checking and fixing missing TYPE field in DynamoDB records."""

    def __init__(self, dynamo_table_name: str, dry_run: bool = True):
        self.table_name = dynamo_table_name
        self.dry_run = dry_run
        self.dynamo_client = boto3.client("dynamodb")
        self.resource = boto3.resource("dynamodb")
        self.table = self.resource.Table(dynamo_table_name)

        self.stats = {
            "total_scanned": 0,
            "images_missing_type": 0,
            "receipts_missing_type": 0,
            "images_fixed": 0,
            "receipts_fixed": 0,
            "errors": 0,
        }
        self.problematic_records: List[Tuple[str, Dict[str, Any]]] = []

    def scan_for_missing_type(self):
        """Scan entire table for records missing TYPE field."""
        logger.info("Scanning for records missing TYPE field...")

        # Scan with filter for missing TYPE
        scan_kwargs = {"FilterExpression": Attr("TYPE").not_exists()}

        done = False
        start_key = None

        while not done:
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key

            response = self.table.scan(**scan_kwargs)
            items = response.get("Items", [])
            self.stats["total_scanned"] += response.get("ScannedCount", 0)

            for item in items:
                # Determine record type from PK/SK pattern
                pk = item.get("PK", "")
                sk = item.get("SK", "")

                if pk.startswith("IMAGE#") and sk.startswith("IMAGE#"):
                    self.stats["images_missing_type"] += 1
                    record_type = "IMAGE"
                elif pk.startswith("IMAGE#") and sk.startswith("RECEIPT#"):
                    self.stats["receipts_missing_type"] += 1
                    record_type = "RECEIPT"
                else:
                    # Unknown record type, skip
                    continue

                self.problematic_records.append((record_type, item))
                logger.warning(
                    f"Found {record_type} record missing TYPE field: PK={pk}, SK={sk}"
                )

                if not self.dry_run:
                    self.fix_record(item, record_type)

            start_key = response.get("LastEvaluatedKey", None)
            done = start_key is None

    def fix_record(self, item: Dict[str, Any], record_type: str):
        """Add TYPE field to a record."""
        try:
            # Update the record to add TYPE field
            self.table.update_item(
                Key={"PK": item["PK"], "SK": item["SK"]},
                UpdateExpression="SET #type = :type",
                ExpressionAttributeNames={"#type": "TYPE"},
                ExpressionAttributeValues={":type": record_type},
                ConditionExpression="attribute_not_exists(#type)",
            )

            if record_type == "IMAGE":
                self.stats["images_fixed"] += 1
            else:
                self.stats["receipts_fixed"] += 1

            logger.info(f"Fixed {record_type} record: PK={item['PK']}, SK={item['SK']}")

        except Exception as e:
            self.stats["errors"] += 1
            logger.error(
                f"Failed to fix {record_type} record PK={item['PK']}, SK={item['SK']}: {e}"
            )

    def run(self):
        """Run the check and fix process."""
        self.scan_for_missing_type()

        # Print summary
        logger.info("\n" + "=" * 50)
        logger.info("TYPE FIELD CHECK SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Total records scanned: {self.stats['total_scanned']}")
        logger.info(f"Images missing TYPE: {self.stats['images_missing_type']}")
        logger.info(f"Receipts missing TYPE: {self.stats['receipts_missing_type']}")

        if not self.dry_run:
            logger.info(f"Images fixed: {self.stats['images_fixed']}")
            logger.info(f"Receipts fixed: {self.stats['receipts_fixed']}")
            logger.info(f"Errors: {self.stats['errors']}")

        total_missing = (
            self.stats["images_missing_type"] + self.stats["receipts_missing_type"]
        )

        if self.dry_run and total_missing > 0:
            logger.info("\nThis was a DRY RUN - no changes were made")
            logger.info(f"Run with --no-dry-run to fix {total_missing} records")
        elif total_missing == 0:
            logger.info("\nAll records have the TYPE field correctly set!")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check and fix missing TYPE field in Image/Receipt records"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack to use (dev or prod)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually fix the records (default is dry run)",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    from pulumi import automation as auto

    # Set up the stack
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(parent_dir, "infra")

    logger.info(f"Using stack: {stack_name}")

    # Create a stack reference to get outputs
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )

    # Get the outputs
    outputs = stack.outputs()

    # Extract configuration
    dynamo_table_name = outputs["dynamodb_table_name"].value

    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"Mode: {'LIVE UPDATE' if args.no_dry_run else 'DRY RUN'}")

    # Create and run fixer
    fixer = TypeFieldFixer(
        dynamo_table_name=dynamo_table_name,
        dry_run=not args.no_dry_run,
    )

    fixer.run()


if __name__ == "__main__":
    main()
