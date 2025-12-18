#!/usr/bin/env python3
"""
Script to properly fix Image and Receipt entities by loading them through
the receipt_dynamo package and re-saving them with correct structure.

This ensures all fields including TYPE are properly set according to the
entity definitions.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any

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


class EntityFixer:
    """Properly fixes entities by loading and re-saving them."""

    def __init__(self, dynamo_table_name: str, dry_run: bool = True):
        self.dynamo_client = DynamoClient(dynamo_table_name)
        self.dry_run = dry_run
        self.stats = {
            "total_images": 0,
            "total_receipts": 0,
            "images_fixed": 0,
            "receipts_fixed": 0,
            "errors": 0,
        }

    def fix_all_entities(self):
        """Fix all Image and Receipt entities."""
        logger.info("Starting to fix all Image and Receipt entities...")

        # Fix all Images
        self.fix_all_images()

        # Fix all Receipts
        self.fix_all_receipts()

        # Print summary
        self.print_summary()

    def fix_all_images(self):
        """Load and re-save all Image entities."""
        logger.info("Fixing all Image entities...")

        # Get all images
        from receipt_dynamo.constants import ImageType

        for image_type in [ImageType.PHOTO, ImageType.SCAN]:
            last_evaluated_key = None

            while True:
                try:
                    # Get a batch of images
                    images, last_evaluated_key = self.dynamo_client.list_images_by_type(
                        image_type=image_type,
                        limit=50,  # Process in smaller batches
                        last_evaluated_key=last_evaluated_key,
                    )

                    self.stats["total_images"] += len(images)

                    # Update each image
                    for image in images:
                        if not self.dry_run:
                            try:
                                # Simply update the image - this will ensure all fields are correct
                                self.dynamo_client.update_image(image)
                                self.stats["images_fixed"] += 1
                                logger.debug(f"Fixed Image: {image.image_id}")
                            except Exception as e:
                                self.stats["errors"] += 1
                                logger.error(
                                    f"Failed to fix Image {image.image_id}: {e}"
                                )
                        else:
                            self.stats["images_fixed"] += 1

                    # If no more items, break
                    if not last_evaluated_key:
                        break

                except Exception as e:
                    logger.error(f"Error processing images: {e}")
                    break

    def fix_all_receipts(self):
        """Load and re-save all Receipt entities."""
        logger.info("Fixing all Receipt entities...")

        last_evaluated_key = None

        while True:
            try:
                # Get a batch of receipts
                receipts, last_evaluated_key = self.dynamo_client.list_receipts(
                    limit=50,  # Process in smaller batches
                    last_evaluated_key=last_evaluated_key,
                )

                self.stats["total_receipts"] += len(receipts)

                # Update each receipt
                for receipt in receipts:
                    if not self.dry_run:
                        try:
                            # Simply update the receipt - this will ensure all fields are correct
                            self.dynamo_client.update_receipt(receipt)
                            self.stats["receipts_fixed"] += 1
                            logger.debug(
                                f"Fixed Receipt: {receipt.image_id}:{receipt.receipt_id}"
                            )
                        except Exception as e:
                            self.stats["errors"] += 1
                            logger.error(
                                f"Failed to fix Receipt {receipt.image_id}:{receipt.receipt_id}: {e}"
                            )
                    else:
                        self.stats["receipts_fixed"] += 1

                # If no more items, break
                if not last_evaluated_key:
                    break

            except Exception as e:
                logger.error(f"Error processing receipts: {e}")
                break

    def print_summary(self):
        """Print summary of fixes."""
        logger.info("\n" + "=" * 50)
        logger.info("ENTITY FIX SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Total Images processed: {self.stats['total_images']}")
        logger.info(f"Total Receipts processed: {self.stats['total_receipts']}")

        if not self.dry_run:
            logger.info(f"Images fixed: {self.stats['images_fixed']}")
            logger.info(f"Receipts fixed: {self.stats['receipts_fixed']}")
            logger.info(f"Errors: {self.stats['errors']}")
        else:
            logger.info(f"Images to be fixed: {self.stats['images_fixed']}")
            logger.info(f"Receipts to be fixed: {self.stats['receipts_fixed']}")
            logger.info("\nThis was a DRY RUN - no changes were made")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Properly fix all Image and Receipt entities"
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
        help="Actually fix the entities (default is dry run)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

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
    fixer = EntityFixer(
        dynamo_table_name=dynamo_table_name,
        dry_run=not args.no_dry_run,
    )

    fixer.fix_all_entities()


if __name__ == "__main__":
    main()
