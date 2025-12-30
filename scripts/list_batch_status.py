#!/usr/bin/env python3
"""
List OpenAI batch statuses from DynamoDB.

Usage:
    python scripts/list_batch_status.py --env prod
    python scripts/list_batch_status.py --env dev --limit 50
"""

import argparse
import os
import sys
from collections import Counter

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient


def list_batch_status(env: str, limit: int = 200, show_openai: bool = False):
    """List batch summaries and their statuses."""
    config = load_env(env=env)
    client = DynamoClient(config["dynamodb_table_name"])

    # List all batch summaries
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

    print(f"Found {len(batches)} batch summaries in {env.upper()}\n")
    print(
        f"{'Batch Type':<18} {'Status':<12} {'Receipts':<10} "
        f"{'Submitted':<22} {'OpenAI Batch ID'}"
    )
    print("-" * 100)

    for batch in sorted(batches, key=lambda b: b.submitted_at, reverse=True)[:50]:
        submitted = str(batch.submitted_at)[:19] if batch.submitted_at else "N/A"
        num_receipts = len(batch.receipt_refs) if batch.receipt_refs else 0
        oai_id = batch.openai_batch_id[:30] if batch.openai_batch_id else "N/A"
        print(
            f"{batch.batch_type:<18} {batch.status:<12} {num_receipts:<10} "
            f"{submitted:<22} {oai_id}"
        )

    # Summary by status
    print("\n=== Status Summary ===")
    status_counts = Counter(batch.status for batch in batches)
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    # Find in-progress batches
    in_progress = [
        b for b in batches if b.status in ("VALIDATING", "IN_PROGRESS", "FINALIZING")
    ]
    if in_progress:
        print(f"\n=== {len(in_progress)} batches in progress ===")
        for b in in_progress:
            print(f"  {b.batch_type}: {b.openai_batch_id}")

    # Find pending batches
    pending = [b for b in batches if b.status == "PENDING"]
    if pending:
        print(f"\n=== {len(pending)} batches pending ===")

    # Optionally check OpenAI status
    if show_openai and (in_progress or pending):
        try:
            from openai import OpenAI

            openai_client = OpenAI()
            print("\n=== OpenAI Status Check ===")
            for b in (in_progress + pending)[:10]:
                try:
                    oai_batch = openai_client.batches.retrieve(b.openai_batch_id)
                    print(f"  {b.openai_batch_id[:30]}: {oai_batch.status}")
                except Exception as e:
                    print(f"  {b.openai_batch_id[:30]}: Error - {e}")
        except ImportError:
            print("\nNote: Install openai package to check OpenAI status directly")


def main():
    parser = argparse.ArgumentParser(description="List OpenAI batch statuses")
    parser.add_argument(
        "--env",
        type=str,
        default="prod",
        choices=["dev", "prod"],
        help="Environment (default: prod)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum batches to list (default: 200)",
    )
    parser.add_argument(
        "--openai",
        action="store_true",
        help="Check OpenAI API for current status",
    )

    args = parser.parse_args()
    list_batch_status(args.env, args.limit, args.openai)


if __name__ == "__main__":
    main()
