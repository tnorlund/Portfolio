#!/usr/bin/env python3
"""List all ReceiptPlace records from DynamoDB.

ReceiptPlace is the merchant/location source of truth. This read-only census
script lists those records with options to paginate and export the results.
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Optional

from boto3.dynamodb.types import TypeDeserializer

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo import DynamoClient  # noqa: E402
from receipt_dynamo.data._pulumi import load_env  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
_DESERIALIZER = TypeDeserializer()


def _deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert one low-level DynamoDB item to ordinary Python values."""
    return {
        key: _DESERIALIZER.deserialize(value) for key, value in item.items()
    }


def list_all_places(
    client: DynamoClient,
    limit: Optional[int] = None,
    output_file: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List canonical place rows through the raw ``GSITYPE`` index.

    This census intentionally avoids entity conversion. Historical place rows
    can carry drifted secondary-index projections, while their scalar
    ``merchant_name`` and place fields remain authoritative for read-only
    census work.

    Args:
        client: Public DynamoDB client exposing the raw ReceiptPlace census.
        limit: Maximum number of records to retrieve (None for all)
        output_file: Optional file path to save results as JSON

    Returns:
        Deserialized ``RECEIPT_PLACE`` rows.
    """
    logger.info("Starting to list all ReceiptPlace records...")
    all_places = []
    last_evaluated_key = None
    page_count = 0

    try:
        while True:
            page_count += 1
            logger.info(f"Fetching page {page_count}...")

            remaining = None if limit is None else limit - len(all_places)
            # the data layer's raw census read: low-level items, no strict
            # entity conversion (drifted projections stay readable)
            items, last_evaluated_key = client.list_receipt_places_raw(
                limit=remaining,
                last_evaluated_key=last_evaluated_key,
            )
            places = [_deserialize_item(item) for item in items]

            all_places.extend(places)
            logger.info(
                f"Page {page_count}: Retrieved {len(places)} records "
                f"(total so far: {len(all_places)})"
            )

            # Check if we've hit the limit
            if limit and len(all_places) >= limit:
                all_places = all_places[:limit]
                logger.info(f"Reached limit of {limit} records")
                break

            # Check if there are more pages
            if last_evaluated_key is None:
                logger.info("No more pages to fetch")
                break

    except Exception as e:
        logger.error(f"Error listing places: {e}", exc_info=True)
        raise

    logger.info(f"Total ReceiptPlace records retrieved: {len(all_places)}")

    # Save to file if requested
    if output_file:
        logger.info(f"Saving results to {output_file}...")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                all_places,
                f,
                indent=2,
                default=str,
            )
        logger.info(f"Results saved to {output_file}")

    return all_places


def print_summary(places: list[dict[str, Any]]) -> None:
    """Print a summary of the receipt-place records.

    Args:
        places: List of ReceiptPlace records
    """
    if not places:
        print("No receipt-place records found.")
        return

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total records: {len(places)}")

    # Count by validation status
    status_counts = {}
    for place in places:
        status = place.get("validation_status") or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1

    print("\nBy validation status:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    # Count unique merchants
    unique_merchants = set()
    for place in places:
        merchant_name = place.get("merchant_name")
        if isinstance(merchant_name, str) and merchant_name.strip():
            unique_merchants.add(merchant_name.strip())

    print(f"\nUnique merchants: {len(unique_merchants)}")

    # Count records with/without place_id
    with_place_id = sum(
        1
        for place in places
        if isinstance(place.get("place_id"), str) and place["place_id"].strip()
    )
    without_place_id = len(places) - with_place_id

    print(f"\nRecords with place_id: {with_place_id}")
    print(f"Records without place_id: {without_place_id}")

    # Show some examples
    print(f"\n{'='*60}")
    print("SAMPLE RECORDS (first 5)")
    print(f"{'='*60}")
    for i, place in enumerate(places[:5], 1):
        print(
            f"\n{i}. Image: {place.get('image_id')}, "
            f"Receipt: {place.get('receipt_id')}"
        )
        print(f"   Merchant: {place.get('merchant_name') or '(empty)'}")
        print(f"   Place ID: {place.get('place_id') or '(empty)'}")
        print(f"   Status: {place.get('validation_status') or '(empty)'}")
        print(f"   Matched fields: {place.get('matched_fields', [])}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="List all ReceiptPlace records from DynamoDB"
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
                    logger.error(
                        f"dynamodb_table_name not found in {args.stack} stack"
                    )
                    sys.exit(1)
            except Exception as e:
                logger.error(
                    f"Failed to load Pulumi config: {e}", exc_info=True
                )
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

    # Standard client resolution: DynamoClient defaults the region
    # (us-east-1) and honors DYNAMODB_ENDPOINT_URL, so the census works
    # without AWS_REGION in the environment and against local endpoints.
    # Raw reads still bypass strict entity conversion via
    # list_receipt_places_raw (drifted index projections stay readable).
    try:
        client = DynamoClient(table_name)
    except Exception as e:
        logger.error(f"Failed to create DynamoDB client: {e}", exc_info=True)
        sys.exit(1)

    # List all receipt places
    try:
        places = list_all_places(
            client,
            limit=args.limit,
            output_file=args.output,
        )
    except Exception as e:
        logger.error(f"Failed to list places: {e}", exc_info=True)
        sys.exit(1)

    # Print summary
    print_summary(places)

    if not args.summary_only:
        print(f"\n{'='*60}")
        print("ALL RECORDS")
        print(f"{'='*60}")
        for i, place in enumerate(places, 1):
            print(
                f"\n{i}. {place.get('image_id')} / "
                f"Receipt {place.get('receipt_id')}"
            )
            print(f"   Merchant: {place.get('merchant_name') or '(empty)'}")
            print(f"   Place ID: {place.get('place_id') or '(empty)'}")
            print(f"   Status: {place.get('validation_status') or '(empty)'}")


if __name__ == "__main__":
    main()
