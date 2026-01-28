#!/usr/bin/env python3
"""
Backfill ReceiptSummaryRecord for all existing receipts.

This script iterates through all receipts in the database, computes
ReceiptSummary from their word labels, and persists ReceiptSummaryRecord
to DynamoDB.

Usage:
    python scripts/backfill_receipt_summaries.py [--env dev|prod] [--dry-run]
"""

import argparse
import logging

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityError,
    OperationError,
)
from receipt_dynamo.entities import ReceiptSummary, ReceiptSummaryRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def backfill_summaries(
    client: DynamoClient,
    batch_size: int = 25,
    dry_run: bool = False,
) -> dict:
    """Backfill ReceiptSummaryRecord for all receipts.

    Args:
        client: DynamoDB client
        batch_size: Number of records to upsert in each batch
        dry_run: If True, don't write to DynamoDB

    Returns:
        Stats dictionary with counts
    """
    stats = {
        "receipts_processed": 0,
        "summaries_created": 0,
        "summaries_with_total": 0,
        "summaries_with_date": 0,
        "errors": 0,
    }

    # Load all places for merchant names
    logger.info("Loading receipt places for merchant names...")
    places_by_key = {}
    last_key = None
    while True:
        places, last_key = client.list_receipt_places(
            limit=1000,
            last_evaluated_key=last_key,
        )
        for place in places:
            key = f"{place.image_id}_{place.receipt_id}"
            places_by_key[key] = place
        if last_key is None:
            break
    logger.info("Loaded %d places", len(places_by_key))

    # Process receipts in batches
    pending_records: list[ReceiptSummaryRecord] = []
    last_key = None
    page_num = 0

    while True:
        page_num += 1
        logger.info("Processing page %d...", page_num)

        page = client.list_receipt_details(
            limit=100,
            last_evaluated_key=last_key,
        )

        for key, bundle in page.bundles.items():
            try:
                # Get merchant name from place
                place = places_by_key.get(key)
                merchant_name = place.merchant_name if place else None

                # Compute summary from word labels
                summary = ReceiptSummary.from_word_labels_and_words(
                    image_id=bundle.receipt.image_id,
                    receipt_id=bundle.receipt.receipt_id,
                    merchant_name=merchant_name,
                    word_labels=bundle.word_labels,
                    words=bundle.words,
                )

                # Create record for persistence
                record = ReceiptSummaryRecord.from_summary(summary)
                pending_records.append(record)

                stats["receipts_processed"] += 1
                if summary.grand_total is not None:
                    stats["summaries_with_total"] += 1
                if summary.date is not None:
                    stats["summaries_with_date"] += 1

                # Batch upsert when we have enough
                if len(pending_records) >= batch_size:
                    if not dry_run:
                        client.upsert_receipt_summaries(pending_records)
                    stats["summaries_created"] += len(pending_records)
                    logger.info(
                        "  Upserted %d summaries (total: %d)",
                        len(pending_records),
                        stats["summaries_created"],
                    )
                    pending_records = []

            except (EntityError, OperationError) as e:
                logger.error("Entity error processing %s: %s", key, e)
                stats["errors"] += 1
            except DynamoDBError as e:
                logger.error("DynamoDB error processing %s: %s", key, e)
                stats["errors"] += 1

        last_key = page.last_evaluated_key
        if last_key is None:
            break

    # Upsert remaining records
    if pending_records:
        if not dry_run:
            client.upsert_receipt_summaries(pending_records)
        stats["summaries_created"] += len(pending_records)
        logger.info("  Upserted final %d summaries", len(pending_records))

    return stats


def main() -> None:
    """Run the backfill script with CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Backfill ReceiptSummaryRecord for all receipts"
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod"],
        default="dev",
        help="Environment to run against (default: dev)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to DynamoDB, just compute summaries",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of records to upsert in each batch (default: 25)",
    )
    args = parser.parse_args()

    logger.info("Starting backfill for %s environment...", args.env)
    if args.dry_run:
        logger.info("DRY RUN - no changes will be written")

    # Load config and create client
    config = load_env(env=args.env)
    client = DynamoClient(table_name=config["dynamodb_table_name"])

    # Run backfill
    stats = backfill_summaries(
        client,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    # Print summary
    logger.info("=" * 50)
    logger.info("Backfill complete!")
    logger.info("  Receipts processed: %d", stats["receipts_processed"])
    logger.info("  Summaries created:  %d", stats["summaries_created"])
    logger.info("  With grand_total:   %d", stats["summaries_with_total"])
    logger.info("  With date:          %d", stats["summaries_with_date"])
    logger.info("  Errors:             %d", stats["errors"])


if __name__ == "__main__":
    main()
