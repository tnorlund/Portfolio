#!/usr/bin/env python3
"""
List OpenAI batch statuses from DynamoDB.

Usage:
    python scripts/list_batch_status.py --env dev
    python scripts/list_batch_status.py --env prod
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

import openai
from openai import OpenAI

from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.data.dynamo_client import DynamoClient


def list_batch_status(env: str):
    """List batch summaries and their statuses."""
    config = load_env(env=env)
    client = DynamoClient(config["dynamodb_table_name"])

    # Load OpenAI API key from Pulumi secrets
    if not os.environ.get("OPENAI_API_KEY"):
        secrets = load_secrets(env=env)
        openai_key = secrets.get("portfolio:OPENAI_API_KEY")
        if openai_key:
            os.environ["OPENAI_API_KEY"] = openai_key

    openai_client = OpenAI()

    # List all batch summaries
    batches, last_key = client.list_batch_summaries()
    while last_key:
        batch_page, last_key = client.list_batch_summaries(
            last_evaluated_key=last_key,
        )
        batches.extend(batch_page)

    print(f"Found {len(batches)} batch summaries in {env.upper()}\n")
    print(
        f"{'Batch Type':<18} {'DynamoDB':<12} {'OpenAI':<14} {'Receipts':<10} "
        f"{'Submitted':<22} {'OpenAI Batch ID'}"
    )
    print("-" * 120)

    openai_status_counts: Counter = Counter()
    dynamo_status_counts: Counter = Counter()

    for batch in sorted(batches, key=lambda b: b.submitted_at, reverse=True):
        submitted = str(batch.submitted_at)[:19] if batch.submitted_at else "N/A"
        num_receipts = len(batch.receipt_refs) if batch.receipt_refs else 0
        oai_id = batch.openai_batch_id[:30] if batch.openai_batch_id else "N/A"
        dynamo_status_counts[batch.status] += 1

        try:
            oai_batch = openai_client.batches.retrieve(batch.openai_batch_id)
            oai_status = oai_batch.status
        except openai.APIError as e:
            oai_status = f"error: {e}"

        openai_status_counts[oai_status] += 1

        print(
            f"{batch.batch_type:<18} {batch.status:<12} {oai_status:<14} "
            f"{num_receipts:<10} {submitted:<22} {oai_id}"
        )

    # Summary
    print(f"\n=== DynamoDB Status Summary ===")
    for status, count in sorted(dynamo_status_counts.items()):
        print(f"  {status}: {count}")

    print(f"\n=== OpenAI Status Summary ===")
    for status, count in sorted(openai_status_counts.items()):
        print(f"  {status}: {count}")


def main():
    parser = argparse.ArgumentParser(description="List OpenAI batch statuses")
    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        choices=["dev", "prod"],
        help="Environment (default: dev)",
    )

    args = parser.parse_args()
    list_batch_status(args.env)


if __name__ == "__main__":
    main()
