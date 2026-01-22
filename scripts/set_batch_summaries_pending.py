#!/usr/bin/env python3
"""
Set all batch summaries to PENDING status.

This script updates the status field and GSI1 key for all batch summaries
in DynamoDB to PENDING. This is useful when you need to re-trigger polling
for batches that are already complete in OpenAI but stale in DynamoDB.

Usage:
    # Update all batches
    python scripts/set_batch_summaries_pending.py --env prod --dry-run
    python scripts/set_batch_summaries_pending.py --env prod --no-dry-run

    # Update only LINE_EMBEDDING batches
    python scripts/set_batch_summaries_pending.py --env prod --batch-type LINE_EMBEDDING --dry-run
    python scripts/set_batch_summaries_pending.py --env prod --batch-type LINE_EMBEDDING --no-dry-run

    # Update only WORD_EMBEDDING batches
    python scripts/set_batch_summaries_pending.py --env prod --batch-type WORD_EMBEDDING --no-dry-run
"""

import argparse
import logging
import os
import sys

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import BatchStatus
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def set_all_batch_summaries_to_pending(
    dynamo: DynamoClient,
    dry_run: bool = True,
    target_statuses: list[str] | None = None,
    batch_type: str | None = None,
) -> dict:
    """
    Set all batch summaries to PENDING status.

    Args:
        dynamo: DynamoDB client
        dry_run: If True, don't actually update (default True)
        target_statuses: Only update batches with these statuses.
                        If None, update all non-PENDING batches.
        batch_type: Only update batches of this type (LINE_EMBEDDING or
                   WORD_EMBEDDING). If None, update all batch types.

    Returns:
        dict with stats about the operation
    """
    logger.info("Fetching all BatchSummary records...")

    # Fetch all batch summaries with pagination
    batch_summaries = []
    last_key = None
    while True:
        batch_page, last_key = dynamo.list_batch_summaries(
            limit=100, last_evaluated_key=last_key
        )
        batch_summaries.extend(batch_page)
        if last_key is None:
            break

    logger.info(f"Found {len(batch_summaries)} BatchSummary records total")

    # Filter by batch_type if specified
    if batch_type:
        batch_summaries = [
            bs for bs in batch_summaries if bs.batch_type == batch_type
        ]
        logger.info(f"Filtered to {len(batch_summaries)} {batch_type} batches")

    # Count by current status
    status_counts = {}
    for bs in batch_summaries:
        status_counts[bs.status] = status_counts.get(bs.status, 0) + 1

    logger.info("Current status distribution:")
    for status, count in sorted(status_counts.items()):
        logger.info(f"  {status}: {count}")

    # Filter to only update non-PENDING batches (or specific statuses)
    if target_statuses:
        to_update = [
            bs for bs in batch_summaries if bs.status in target_statuses
        ]
    else:
        to_update = [
            bs
            for bs in batch_summaries
            if bs.status != BatchStatus.PENDING.value
        ]

    logger.info(f"Will update {len(to_update)} batches to PENDING")

    if not to_update:
        logger.info("No batches to update")
        return {
            "total": len(batch_summaries),
            "updated": 0,
            "status_counts": status_counts,
        }

    if dry_run:
        logger.info(
            "[DRY RUN] Would update %d batches to PENDING", len(to_update)
        )
        # Show sample of what would be updated
        for bs in to_update[:5]:
            logger.info(
                f"  Would update: {bs.batch_id} ({bs.batch_type}) "
                f"{bs.status} -> PENDING"
            )
        if len(to_update) > 5:
            logger.info(f"  ... and {len(to_update) - 5} more")
        return {
            "total": len(batch_summaries),
            "updated": len(to_update),
            "status_counts": status_counts,
            "dry_run": True,
        }

    # Update status to PENDING for each batch
    for bs in to_update:
        bs.status = BatchStatus.PENDING.value

    # Update in chunks of 25 (DynamoDB transaction limit)
    updated = 0
    chunk_size = 25
    for i in range(0, len(to_update), chunk_size):
        chunk = to_update[i : i + chunk_size]
        try:
            dynamo.update_batch_summaries(chunk)
            updated += len(chunk)
            logger.info(
                f"Updated {updated}/{len(to_update)} batches to PENDING"
            )
        except Exception as e:
            logger.error(f"Error updating batch summaries chunk: {e}")
            # Try one by one for this chunk
            for bs in chunk:
                try:
                    dynamo.update_batch_summary(bs)
                    updated += 1
                except Exception as inner_e:
                    logger.error(f"Failed to update {bs.batch_id}: {inner_e}")

    return {
        "total": len(batch_summaries),
        "updated": updated,
        "status_counts": status_counts,
        "dry_run": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Set all batch summaries to PENDING status"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment (dev or prod)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually make changes (disables dry-run)",
    )
    parser.add_argument(
        "--status",
        type=str,
        action="append",
        dest="statuses",
        help="Only update batches with this status (can be repeated). "
        "If not specified, updates all non-PENDING batches.",
    )
    parser.add_argument(
        "--batch-type",
        type=str,
        choices=["LINE_EMBEDDING", "WORD_EMBEDDING"],
        dest="batch_type",
        help="Only update batches of this type. "
        "If not specified, updates all batch types.",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info("Mode: %s", mode)
    logger.info("Environment: %s", args.env.upper())
    if args.batch_type:
        logger.info("Batch type filter: %s", args.batch_type)

    if not args.dry_run:
        logger.warning(
            "This will UPDATE all batch summaries to PENDING status!"
        )
        logger.warning("Press Ctrl+C within 5 seconds to abort...")
        import time

        time.sleep(5)

    # Load config and create client
    config = load_env(env=args.env)
    dynamo = DynamoClient(config["dynamodb_table_name"])

    logger.info("=" * 60)
    logger.info("Setting batch summaries to PENDING")
    logger.info("=" * 60)

    result = set_all_batch_summaries_to_pending(
        dynamo,
        dry_run=args.dry_run,
        target_statuses=args.statuses,
        batch_type=args.batch_type,
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("Total batch summaries: %d", result["total"])
    logger.info("Batches updated to PENDING: %d", result["updated"])

    if args.dry_run:
        logger.info(
            "[DRY RUN] No changes were made. Use --no-dry-run to apply changes."
        )


if __name__ == "__main__":
    main()
