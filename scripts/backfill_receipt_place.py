#!/usr/bin/env python3
"""
Executable wrapper for ReceiptPlace backfill.

Initializes clients and runs the backfill operation.

Usage:
    python scripts/backfill_receipt_place.py --dry-run
    python scripts/backfill_receipt_place.py --limit 1000
"""

import argparse
import asyncio
import logging
import sys
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main(
    dry_run: bool = True,
    batch_size: int = 50,
    limit: Optional[int] = None,
    image_id: Optional[str] = None,
    receipt_id: Optional[int] = None,
) -> int:
    """
    Run the ReceiptPlace backfill.

    Args:
        dry_run: If True, don't write to DynamoDB
        batch_size: Batch size for processing
        limit: Maximum receipts to process
        image_id: Process only this image's receipts
        receipt_id: Process only this receipt (requires image_id)

    Returns:
        Exit code (0 = success, 1 = failure)
    """
    try:
        # Import after logging is configured
        from receipt_agent.config.settings import get_settings
        from receipt_agent.subagents.metadata_finder.backfill_receipt_place import (
            ReceiptPlaceBackfiller,
        )
        from receipt_dynamo import DynamoDBClient
        from receipt_places.client import create_places_client

        logger.info("Initializing clients...")

        # Get settings
        settings = get_settings()

        # Initialize DynamoDB client
        dynamo = DynamoDBClient(
            table_name=settings.dynamodb_table_name,
            region=settings.aws_region,
        )
        logger.info(f"✓ DynamoDB client initialized (table={settings.dynamodb_table_name})")

        # Initialize Places API client
        places = create_places_client(
            api_key=settings.google_places_api_key,
            config=settings.places_config,
        )
        logger.info(f"✓ Places API client initialized")

        # Create backfiller
        backfiller = ReceiptPlaceBackfiller(
            dynamo_client=dynamo,
            places_client=places,
            batch_size=batch_size,
        )

        # Run backfill
        logger.info("Starting backfill operation...")
        stats = await backfiller.backfill_all(
            dry_run=dry_run,
            limit=limit,
            image_id=image_id,
            receipt_id=receipt_id,
        )

        # Print summary
        backfiller.print_summary()

        # Return exit code based on success
        if stats.total_failed > 0:
            logger.error(
                f"Backfill completed with {stats.total_failed} failures"
            )
            return 1

        logger.info("✓ Backfill completed successfully")
        return 0

    except Exception as e:
        logger.exception("Fatal error during backfill")
        return 1


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Backfill ReceiptPlace entities from ReceiptMetadata",
        epilog=(
            "Examples:\n"
            "  # Dry run to see what would be created:\n"
            "  python scripts/backfill_receipt_place.py --dry-run\n"
            "\n"
            "  # Actually backfill first 1000 receipts:\n"
            "  python scripts/backfill_receipt_place.py --limit 1000\n"
            "\n"
            "  # Backfill specific receipt:\n"
            "  python scripts/backfill_receipt_place.py "
            "--image-id abc123 --receipt-id 1\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: report what would be created without writing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for DynamoDB pagination (default: 50)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of receipts to backfill (no limit if not specified)",
    )
    parser.add_argument(
        "--image-id",
        help="Backfill only receipts from this image ID",
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        help="Backfill only this receipt (requires --image-id)",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Validate arguments
    if args.receipt_id is not None and not args.image_id:
        print("Error: --receipt-id requires --image-id")
        sys.exit(1)

    # Run backfill
    exit_code = asyncio.run(
        main(
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            image_id=args.image_id,
            receipt_id=args.receipt_id,
        )
    )

    sys.exit(exit_code)
