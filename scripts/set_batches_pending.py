#!/usr/bin/env python3
"""
Set batch summaries to PENDING status for reprocessing.

This script updates batch summaries to PENDING status so the status polling
Lambda will pick them up and trigger compaction (which includes cloud sync).

Usage:
    # Dry run (default) - see what would be changed
    python scripts/set_batches_pending.py --env prod

    # Actually update
    python scripts/set_batches_pending.py --env prod --no-dry-run

    # Filter by current status
    python scripts/set_batches_pending.py --env prod --status COMPLETED

    # Filter by batch type
    python scripts/set_batches_pending.py --env prod --batch-type WORD_EMBEDDING
"""

import argparse
import os
import sys
import time
from collections import Counter

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient


def set_batches_pending(
    env: str,
    dry_run: bool = True,
    status_filter: str | None = None,
    batch_type_filter: str | None = None,
    limit: int = 500,
):
    """Set batch summaries to PENDING status."""
    config = load_env(env=env)
    client = DynamoClient(config["dynamodb_table_name"])

    print(f"\n{'=' * 60}")
    print(f"Set Batch Summaries to PENDING")
    print(f"{'=' * 60}")
    print(f"Environment: {env.upper()}")
    print(f"Table: {config['dynamodb_table_name']}")
    print(f"Dry Run: {dry_run}")
    if status_filter:
        print(f"Status Filter: {status_filter}")
    if batch_type_filter:
        print(f"Batch Type Filter: {batch_type_filter}")
    print(f"{'=' * 60}\n")

    # List all batch summaries
    print("Fetching batch summaries...")
    batches = []
    last_key = None
    while len(batches) < limit:
        batch_page, last_key = client.list_batch_summaries(
            limit=min(100, limit - len(batches)),
            last_evaluated_key=last_key,
        )
        batches.extend(batch_page)
        if last_key is None:
            break

    print(f"Found {len(batches)} total batch summaries\n")

    # Show current status distribution
    print("Current status distribution:")
    status_counts = Counter(batch.status for batch in batches)
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print()

    # Filter batches
    batches_to_update = []
    for batch in batches:
        # Skip if already PENDING
        if batch.status == BatchStatus.PENDING.value:
            continue

        # Apply status filter
        if status_filter and batch.status != status_filter:
            continue

        # Apply batch type filter
        if batch_type_filter and batch.batch_type != batch_type_filter:
            continue

        batches_to_update.append(batch)

    print(f"Batches to update to PENDING: {len(batches_to_update)}")

    if not batches_to_update:
        print("No batches to update.")
        return

    # Show what will be updated
    print("\nBatches to update:")
    print(f"{'Batch Type':<18} {'Current Status':<12} {'Receipts':<10} {'OpenAI Batch ID'}")
    print("-" * 80)
    for batch in batches_to_update[:20]:
        num_receipts = len(batch.receipt_refs) if batch.receipt_refs else 0
        oai_id = batch.openai_batch_id[:30] if batch.openai_batch_id else "N/A"
        print(f"{batch.batch_type:<18} {batch.status:<12} {num_receipts:<10} {oai_id}")
    if len(batches_to_update) > 20:
        print(f"... and {len(batches_to_update) - 20} more")

    if dry_run:
        print("\n[DRY RUN] No changes made. Use --no-dry-run to apply changes.")
        return

    # Confirm before proceeding
    print(f"\n{'!' * 60}")
    print(f"WARNING: About to update {len(batches_to_update)} batch summaries to PENDING")
    print(f"{'!' * 60}")
    print("Press Ctrl+C within 5 seconds to abort...")
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        print("\nAborted.")
        return

    # Update batches
    print("\nUpdating batches...")
    updated = 0
    errors = 0

    for i, batch in enumerate(batches_to_update):
        try:
            # Update status to PENDING
            batch.status = BatchStatus.PENDING.value
            client.update_batch_summary(batch)
            updated += 1

            if (i + 1) % 10 == 0:
                print(f"  Updated {i + 1}/{len(batches_to_update)}...")

        except Exception as e:
            errors += 1
            print(f"  Error updating batch {batch.batch_id}: {e}")

    print(f"\nComplete!")
    print(f"  Updated: {updated}")
    print(f"  Errors: {errors}")

    # Verify GSI by querying PENDING status
    print("\nVerifying GSI update...")
    pending_batches, _ = client.get_batch_summaries_by_status(
        status=BatchStatus.PENDING,
        batch_type=BatchType.WORD_EMBEDDING,
        limit=10,
    )
    print(f"  Found {len(pending_batches)} WORD_EMBEDDING batches with PENDING status via GSI")

    pending_lines, _ = client.get_batch_summaries_by_status(
        status=BatchStatus.PENDING,
        batch_type=BatchType.LINE_EMBEDDING,
        limit=10,
    )
    print(f"  Found {len(pending_lines)} LINE_EMBEDDING batches with PENDING status via GSI")


def main():
    parser = argparse.ArgumentParser(
        description="Set batch summaries to PENDING status"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment (dev or prod)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually make changes (default is dry run)",
    )
    parser.add_argument(
        "--status",
        type=str,
        choices=[s.value for s in BatchStatus],
        help="Only update batches with this status",
    )
    parser.add_argument(
        "--batch-type",
        type=str,
        choices=[t.value for t in BatchType],
        help="Only update batches of this type",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum batches to process (default: 500)",
    )

    args = parser.parse_args()

    set_batches_pending(
        env=args.env,
        dry_run=not args.no_dry_run,
        status_filter=args.status,
        batch_type_filter=args.batch_type,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
