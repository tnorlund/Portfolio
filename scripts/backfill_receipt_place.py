#!/usr/bin/env python3
"""
Executable wrapper for ReceiptPlace backfill.

Initializes clients from Pulumi stack and runs the backfill operation.

Usage:
    python scripts/backfill_receipt_place.py --stack main --dry-run
    python scripts/backfill_receipt_place.py --stack main --limit 1000
    python scripts/backfill_receipt_place.py --stack dev --limit 100 --image-id abc123
"""

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NullCacheManager:
    """No-op cache manager to avoid DynamoDB dependency for backfill script."""

    def get(self, search_type: Any, search_value: str) -> None:
        """Always return None (no cache)."""
        return None

    def put(self, search_type: Any, search_value: str, place_data: Any) -> None:
        """Do nothing (don't cache)."""
        pass


async def main(
    stack: str,
    dry_run: bool = True,
    batch_size: int = 50,
    limit: Optional[int] = None,
    image_id: Optional[str] = None,
    receipt_id: Optional[int] = None,
) -> int:
    """
    Run the ReceiptPlace backfill.

    Args:
        stack: Pulumi stack name (e.g., "main", "dev", "prod")
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
        from receipt_agent.subagents.metadata_finder.backfill_receipt_place import (
            ReceiptPlaceBackfiller,
        )
        from receipt_dynamo import DynamoClient
        from receipt_dynamo.data._pulumi import load_env, load_secrets
        from receipt_places.client import create_places_client

        logger.info(f"Loading Pulumi stack: {stack}")

        # Load infrastructure from Pulumi stack
        env = load_env(stack)
        secrets = load_secrets(stack)

        if not env:
            logger.error(f"Failed to load Pulumi stack '{stack}'. Make sure it exists.")
            return 1

        # Extract values from Pulumi outputs
        dynamodb_table_name = env.get("dynamodb_table_name")
        aws_region = env.get("aws_region", "us-east-1")
        # Try both key formats (with and without portfolio: prefix)
        google_places_api_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY") or secrets.get("google_places_api_key")

        if not dynamodb_table_name:
            logger.error("dynamodb_table_name not found in Pulumi stack outputs")
            return 1

        if not google_places_api_key:
            logger.error("google_places_api_key not found in Pulumi stack secrets")
            return 1

        logger.info("Initializing clients...")

        # Initialize DynamoDB client
        dynamo = DynamoClient(
            table_name=dynamodb_table_name,
            region=aws_region,
        )
        logger.info(f"✓ DynamoDB client initialized (table={dynamodb_table_name}, region={aws_region})")

        # Initialize Places API client with no-op cache to avoid DynamoDB dependency
        places = create_places_client(
            api_key=google_places_api_key,
            cache_manager=NullCacheManager(),  # Use no-op cache for backfill
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
        description="Backfill ReceiptPlace entities from ReceiptMetadata using Pulumi stack",
        epilog=(
            "Examples:\n"
            "  # Dry run to see what would be created:\n"
            "  python scripts/backfill_receipt_place.py --stack main --dry-run\n"
            "\n"
            "  # Actually backfill first 1000 receipts:\n"
            "  python scripts/backfill_receipt_place.py --stack main --limit 1000\n"
            "\n"
            "  # Backfill specific receipt:\n"
            "  python scripts/backfill_receipt_place.py --stack dev "
            "--image-id abc123 --receipt-id 1\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--stack",
        required=True,
        help="Pulumi stack name (e.g., 'main', 'dev', 'prod')",
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
            stack=args.stack,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            image_id=args.image_id,
            receipt_id=args.receipt_id,
        )
    )

    sys.exit(exit_code)
