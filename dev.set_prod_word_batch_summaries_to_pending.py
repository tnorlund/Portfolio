#!/usr/bin/env python3
"""Set word embedding batch summaries to PENDING status in production.

This script sets all word embedding batch summaries to PENDING status,
allowing the word ingestion step function to pick them up and process them again.

Usage:
    python dev.set_prod_word_batch_summaries_to_pending.py [options] [env]

Examples:
    # Dry run - show what would be updated (default)
    python dev.set_prod_word_batch_summaries_to_pending.py prod

    # Actually update production
    python dev.set_prod_word_batch_summaries_to_pending.py --execute prod

    # Skip confirmation prompt
    python dev.set_prod_word_batch_summaries_to_pending.py --execute --yes prod
"""

import argparse
import os
import sys
import traceback
from pathlib import Path
# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "receipt_dynamo"))

import boto3
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.constants import BatchStatus, BatchType


def list_word_batch_summaries(
    dynamo: DynamoClient,
):
    """List all word embedding batch summaries regardless of status.

    Lists all batch summaries and filters for WORD_EMBEDDING type to catch
    any with malformed or unexpected status values.
    """
    all_summaries = []
    last_evaluated_key = None

    print("   Fetching all batch summaries from DynamoDB...")

    while True:
        summaries, last_evaluated_key = dynamo.list_batch_summaries(
            limit=100,
            last_evaluated_key=last_evaluated_key,
        )

        if not summaries:
            break

        # Filter to only WORD_EMBEDDING batches
        word_summaries = [
            s for s in summaries if s.batch_type == BatchType.WORD_EMBEDDING.value
        ]

        all_summaries.extend(word_summaries)
        print(
            f"   Fetched {len(summaries)} batch summaries "
            f"({len(word_summaries)} word embedding) "
            f"(total word embedding: {len(all_summaries)})..."
        )

        if last_evaluated_key is None:
            break

    return all_summaries


def set_batch_summaries_to_pending(
    dynamo: DynamoClient,
    summaries,
    dry_run: bool = True,
) -> tuple[int, int]:
    """Set word embedding batch summaries to PENDING status.

    Returns:
        Tuple of (success_count, error_count)
    """
    success_count = 0
    error_count = 0
    batch_to_update = []

    mode_str = "[DRY RUN] " if dry_run else "[EXECUTE] "
    print(f"\n{mode_str}Setting batch summaries to PENDING...")

    for summary in summaries:
        try:
            # Only update if not already PENDING
            if summary.status == BatchStatus.PENDING.value:
                print(
                    f"   ⏭️  Skipping {summary.batch_id} - already PENDING"
                )
                continue

            original_status = summary.status
            summary.status = BatchStatus.PENDING.value
            batch_to_update.append(summary)

            if dry_run:
                print(
                    f"   [DRY RUN] Would update: {summary.batch_id} "
                    f"({original_status} -> PENDING)"
                )
                success_count += 1
            else:
                # Update in batches of 25 (DynamoDB transaction limit)
                if len(batch_to_update) >= 25:
                    dynamo.update_batch_summaries(batch_to_update)
                    for s in batch_to_update:
                        print(
                            f"   ✓ Updated: {s.batch_id} "
                            f"({s.status if hasattr(s, '_original_status') else original_status} -> PENDING)"
                        )
                    success_count += len(batch_to_update)
                    batch_to_update = []

        except Exception as e:
            error_count += 1
            print(f"   ✗ Error updating {summary.batch_id}: {str(e)[:100]}")

    # Update remaining batches
    if batch_to_update and not dry_run:
        try:
            dynamo.update_batch_summaries(batch_to_update)
            for s in batch_to_update:
                print(f"   ✓ Updated: {s.batch_id} -> PENDING")
            success_count += len(batch_to_update)
        except Exception as e:
            error_count += len(batch_to_update)
            print(f"   ✗ Error updating final batch: {str(e)[:100]}")

    return success_count, error_count


def main():
    parser = argparse.ArgumentParser(
        description="Set word embedding batch summaries to PENDING status"
    )
    parser.add_argument(
        "env",
        nargs="?",
        default="prod",
        help="Environment (dev/prod, default: prod)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually update batches (default is dry run)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (use with --execute)",
    )
    args = parser.parse_args()

    env = args.env
    dry_run = not args.execute
    skip_confirmation = args.yes

    mode_str = "[DRY RUN] " if dry_run else "[EXECUTE] "
    print(f"{mode_str}Set word embedding batch summaries to PENDING")
    print(f"Environment: {env}")
    print("=" * 80)

    # Step 1: Load Pulumi config
    print("\n[1/3] Loading configuration from Pulumi...")
    try:
        infra_dir = project_root / "infra"
        if infra_dir.exists():
            env_vars = load_env(env=env, working_dir=str(infra_dir))
        else:
            env_vars = load_env(env=env)

        table_name = env_vars.get("dynamodb_table_name") or os.environ.get(
            "DYNAMODB_TABLE_NAME"
        )
        if not table_name:
            print("✗ DYNAMODB_TABLE_NAME not set, trying to find table from AWS...")
            try:
                dynamodb = boto3.client("dynamodb", region_name="us-east-1")
                response = dynamodb.list_tables()

                if env == "dev":
                    for table in response.get("TableNames", []):
                        if "receipts" in table.lower() and "dc5be22" in table:
                            table_name = table
                            break
                elif env == "prod":
                    for table in response.get("TableNames", []):
                        if "receipts" in table.lower() and "d7ff76a" in table:
                            table_name = table
                            break

                if not table_name:
                    for table in response.get("TableNames", []):
                        if "receipts" in table.lower():
                            table_name = table
                            break

                if not table_name:
                    print(
                        "✗ DYNAMODB_TABLE_NAME not set and could not find table from AWS"
                    )
                    return 1

            except Exception as aws_error:
                print(f"   ✗ Could not find table from AWS: {aws_error}")
                return 1

        print(f"✓ Using DynamoDB table: {table_name}")

    except Exception as e:
        print(f"✗ Failed to load Pulumi config: {e}")
        traceback.print_exc()
        return 1

    # Step 2: List word embedding batch summaries
    print("\n[2/3] Listing word embedding batch summaries...")
    try:
        dynamo = DynamoClient(table_name)
        summaries = list_word_batch_summaries(dynamo)

        print(f"\n✓ Found {len(summaries)} word embedding batch summaries")

        if not summaries:
            print("No word embedding batches found")
            return 0

        # Show summary
        print("\nBatch summary:")
        status_counts = {}
        for s in summaries:
            status_counts[s.status] = status_counts.get(s.status, 0) + 1

        print("  Status breakdown:")
        for status, count in sorted(status_counts.items()):
            print(f"    {status}: {count}")

        # Filter to only non-PENDING batches
        non_pending_summaries = [
            s for s in summaries if s.status != BatchStatus.PENDING.value
        ]
        print(f"\n  Batches to update (non-PENDING): {len(non_pending_summaries)}")

        if not non_pending_summaries:
            print("No batches to update (all are already PENDING)")
            return 0

    except Exception as e:
        print(f"✗ Failed to list batch summaries: {e}")
        traceback.print_exc()
        return 1

    # Step 3: Set batch summaries to PENDING
    if not dry_run and not skip_confirmation:
        response = input(
            f"\n⚠️  Are you sure you want to set {len(non_pending_summaries)} "
            f"batch(es) to PENDING? (yes/no): "
        )
        if response.lower() != "yes":
            print("Cancelled.")
            return 0

    success_count, error_count = set_batch_summaries_to_pending(
        dynamo,
        non_pending_summaries,
        dry_run=dry_run,
    )

    if dry_run:
        print(
            f"\n✅ Dry run completed. Use --execute to actually update {success_count} batch(es)."
        )
    else:
        if error_count == 0:
            print(f"\n✅ Successfully updated {success_count} batch(es) to PENDING")
        else:
            print(
                f"\n⚠️  Updated {success_count} batch(es), {error_count} error(s) occurred"
            )

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
