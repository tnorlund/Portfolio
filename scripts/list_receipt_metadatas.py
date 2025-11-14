#!/usr/bin/env python3
"""List all ReceiptMetadata records from DynamoDB.

This script lists all ReceiptMetadata records from the specified DynamoDB table,
with options to filter, paginate, and export the results.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import List, Optional

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.data._pulumi import load_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def list_all_metadatas(
    dynamo_client: DynamoClient,
    limit: Optional[int] = None,
    output_file: Optional[str] = None,
) -> List[ReceiptMetadata]:
    """List all ReceiptMetadata records from DynamoDB.

    Args:
        dynamo_client: DynamoDB client instance
        limit: Maximum number of records to retrieve (None for all)
        output_file: Optional file path to save results as JSON

    Returns:
        List of ReceiptMetadata records
    """
    logger.info("Starting to list all ReceiptMetadata records...")
    all_metadatas = []
    last_evaluated_key = None
    page_count = 0

    try:
        while True:
            page_count += 1
            logger.info(f"Fetching page {page_count}...")

            metadatas, last_evaluated_key = dynamo_client.list_receipt_metadatas(
                limit=1000, last_evaluated_key=last_evaluated_key
            )

            all_metadatas.extend(metadatas)
            logger.info(
                f"Page {page_count}: Retrieved {len(metadatas)} records "
                f"(total so far: {len(all_metadatas)})"
            )

            # Check if we've hit the limit
            if limit and len(all_metadatas) >= limit:
                all_metadatas = all_metadatas[:limit]
                logger.info(f"Reached limit of {limit} records")
                break

            # Check if there are more pages
            if last_evaluated_key is None:
                logger.info("No more pages to fetch")
                break

    except Exception as e:
        logger.error(f"Error listing metadatas: {e}", exc_info=True)
        raise

    logger.info(f"Total ReceiptMetadata records retrieved: {len(all_metadatas)}")

    # Save to file if requested
    if output_file:
        logger.info(f"Saving results to {output_file}...")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "image_id": m.image_id,
                        "receipt_id": m.receipt_id,
                        "place_id": m.place_id,
                        "merchant_name": m.merchant_name,
                        "merchant_category": m.merchant_category,
                        "address": m.address,
                        "phone_number": m.phone_number,
                        "matched_fields": m.matched_fields,
                        "validated_by": m.validated_by,
                        "reasoning": m.reasoning,
                        "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                        "validation_status": m.validation_status,
                        "canonical_place_id": m.canonical_place_id,
                        "canonical_merchant_name": m.canonical_merchant_name,
                        "canonical_address": m.canonical_address,
                        "canonical_phone_number": m.canonical_phone_number,
                    }
                    for m in all_metadatas
                ],
                f,
                indent=2,
                default=str,
            )
        logger.info(f"Results saved to {output_file}")

    return all_metadatas


def print_summary(metadatas: List[ReceiptMetadata]) -> None:
    """Print a summary of the metadata records.

    Args:
        metadatas: List of ReceiptMetadata records
    """
    if not metadatas:
        print("No metadata records found.")
        return

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total records: {len(metadatas)}")

    # Count by validation status
    status_counts = {}
    for m in metadatas:
        status = m.validation_status or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1

    print(f"\nBy validation status:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    # Count unique merchants
    unique_merchants = set()
    for m in metadatas:
        if m.merchant_name and m.merchant_name.strip():
            unique_merchants.add(m.merchant_name.strip())

    print(f"\nUnique merchants: {len(unique_merchants)}")

    # Count records with/without place_id
    with_place_id = sum(1 for m in metadatas if m.place_id and m.place_id.strip())
    without_place_id = len(metadatas) - with_place_id

    print(f"\nRecords with place_id: {with_place_id}")
    print(f"Records without place_id: {without_place_id}")

    # Show some examples
    print(f"\n{'='*60}")
    print(f"SAMPLE RECORDS (first 5)")
    print(f"{'='*60}")
    for i, m in enumerate(metadatas[:5], 1):
        print(f"\n{i}. Image: {m.image_id}, Receipt: {m.receipt_id}")
        print(f"   Merchant: {m.merchant_name or '(empty)'}")
        print(f"   Place ID: {m.place_id or '(empty)'}")
        print(f"   Status: {m.validation_status or '(empty)'}")
        print(f"   Matched fields: {m.matched_fields}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="List all ReceiptMetadata records from DynamoDB"
    )
    parser.add_argument(
        "--table",
        type=str,
        help="DynamoDB table name (default: from DYNAMODB_TABLE_NAME env var or Pulumi stack)",
    )
    parser.add_argument(
        "--stack",
        type=str,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod). If provided, table name will be loaded from Pulumi.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of records to retrieve (default: all)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file path for JSON export (optional)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only print summary, don't list all records",
    )

    args = parser.parse_args()

    # Get table name
    table_name = args.table
    if not table_name:
        if args.stack:
            # Load from Pulumi stack
            logger.info(f"Loading table name from Pulumi stack: {args.stack}")
            try:
                env = load_env(env=args.stack)
                table_name = env.get("dynamodb_table_name")
                if not table_name:
                    logger.error(f"dynamodb_table_name not found in {args.stack} stack")
                    sys.exit(1)
            except Exception as e:
                logger.error(f"Failed to load Pulumi config: {e}", exc_info=True)
                sys.exit(1)
        else:
            # Try environment variable
            table_name = os.environ.get("DYNAMODB_TABLE_NAME")
            if not table_name:
                logger.error(
                    "DynamoDB table name must be provided via --table argument, "
                    "--stack argument, or DYNAMODB_TABLE_NAME environment variable"
                )
                sys.exit(1)

    logger.info(f"Using DynamoDB table: {table_name}")

    # Create DynamoDB client
    try:
        dynamo_client = DynamoClient(table_name)
    except Exception as e:
        logger.error(f"Failed to create DynamoDB client: {e}", exc_info=True)
        sys.exit(1)

    # List all metadatas
    try:
        metadatas = list_all_metadatas(
            dynamo_client, limit=args.limit, output_file=args.output
        )
    except Exception as e:
        logger.error(f"Failed to list metadatas: {e}", exc_info=True)
        sys.exit(1)

    # Print summary
    print_summary(metadatas)

    if not args.summary_only:
        print(f"\n{'='*60}")
        print(f"ALL RECORDS")
        print(f"{'='*60}")
        for i, m in enumerate(metadatas, 1):
            print(f"\n{i}. {m.image_id} / Receipt {m.receipt_id}")
            print(f"   Merchant: {m.merchant_name or '(empty)'}")
            print(f"   Place ID: {m.place_id or '(empty)'}")
            print(f"   Status: {m.validation_status or '(empty)'}")


if __name__ == "__main__":
    main()

