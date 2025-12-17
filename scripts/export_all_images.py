#!/usr/bin/env python3
"""
Export all images and their sub-records from DynamoDB to local storage.

This script:
1. Gets table name from Pulumi stack (dev or prod)
2. Lists all images from the table
3. Exports each image with all sub-records (lines, words, receipts, labels, etc.)
4. Saves to local directory: {stack}.export/{image_id}.json

Usage:
    # Export from dev
    python scripts/export_all_images.py --stack dev --output-dir dev.export

    # Export from prod
    python scripts/export_all_images.py --stack prod --output-dir prod.export
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.export_image import export_image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")

    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )

    logger.info(f"{stack.upper()} table: {table_name}")
    return table_name


def export_all_images(
    table_name: str,
    output_dir: str,
    limit: Optional[int] = None,
    start_from: Optional[str] = None,
) -> dict:
    """
    Export all images and their sub-records from DynamoDB.

    Args:
        table_name: DynamoDB table name
        output_dir: Directory to save exported JSON files
        limit: Optional limit on number of images to export (for testing)
        start_from: Optional image_id to start from (for resuming)

    Returns:
        Dictionary with export statistics
    """
    client = DynamoClient(table_name)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {output_path.absolute()}")

    # Statistics
    stats = {
        "total_images": 0,
        "exported": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    # List all images
    logger.info("Listing all images from DynamoDB...")
    last_evaluated_key = None
    start_exporting = start_from is None

    while True:
        try:
            images, last_evaluated_key = client.list_images(
                limit=500,  # Process in batches
                last_evaluated_key=last_evaluated_key,
            )

            if not images:
                break

            stats["total_images"] += len(images)

            for image in images:
                image_id = image.image_id

                # Skip until we reach start_from
                if not start_exporting:
                    if image_id == start_from:
                        start_exporting = True
                    else:
                        stats["skipped"] += 1
                        continue

                # Check if already exported
                json_file = output_path / f"{image_id}.json"
                if json_file.exists():
                    logger.debug(f"Skipping {image_id} (already exported)")
                    stats["skipped"] += 1
                    continue

                # Export image
                try:
                    export_image(table_name, image_id, str(output_path))
                    stats["exported"] += 1

                    if stats["exported"] % 10 == 0:
                        logger.info(
                            f"Exported {stats['exported']} images "
                            f"(failed: {stats['failed']}, skipped: {stats['skipped']})"
                        )

                    # Check limit
                    if limit and stats["exported"] >= limit:
                        logger.info(f"Reached limit of {limit} images")
                        return stats

                except Exception as e:
                    stats["failed"] += 1
                    error_msg = f"Failed to export {image_id}: {e}"
                    logger.error(error_msg, exc_info=True)
                    stats["errors"].append(error_msg)

            # Check if we're done
            if not last_evaluated_key:
                break

        except Exception as e:
            logger.error(f"Error listing images: {e}", exc_info=True)
            break

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Export all images and sub-records from DynamoDB to local storage"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: {stack}.export)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of images to export (for testing)",
    )
    parser.add_argument(
        "--start-from",
        type=str,
        default=None,
        help="Image ID to start from (for resuming)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine output directory
    output_dir = args.output_dir or f"{args.stack}.export"

    try:
        # Get table name from Pulumi
        table_name = get_table_name(args.stack)

        # Export all images
        logger.info(f"Starting export from {args.stack.upper()} stack...")
        stats = export_all_images(
            table_name=table_name,
            output_dir=output_dir,
            limit=args.limit,
            start_from=args.start_from,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("EXPORT SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total images found: {stats['total_images']}")
        logger.info(f"Successfully exported: {stats['exported']}")
        logger.info(f"Already existed (skipped): {stats['skipped']}")
        logger.info(f"Failed: {stats['failed']}")
        logger.info(f"Output directory: {Path(output_dir).absolute()}")

        if stats["errors"]:
            logger.warning(f"\n{len(stats['errors'])} errors occurred:")
            for error in stats["errors"][:10]:  # Show first 10 errors
                logger.warning(f"  - {error}")
            if len(stats["errors"]) > 10:
                logger.warning(f"  ... and {len(stats['errors']) - 10} more errors")

        if stats["failed"] > 0:
            logger.error("\n❌ Export completed with errors")
            sys.exit(1)
        else:
            logger.info("\n✅ Export completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

