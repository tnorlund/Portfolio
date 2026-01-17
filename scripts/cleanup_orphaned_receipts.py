#!/usr/bin/env python3
"""
Cleanup orphaned receipts (receipts without parent Image entity).

This script:
1. Finds receipts where the parent Image entity doesn't exist
2. Deletes all associated entities (Lines, Words, Letters, Labels)
3. Deletes the Receipt itself
4. Optionally deletes raw S3 images
"""

import argparse
import logging
import os
import sys

import boto3

# Add parent directory to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt import Receipt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_pulumi_outputs(stack_name: str, work_dir: str) -> dict:
    """Get Pulumi stack outputs."""
    from pulumi import automation as auto

    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    outputs = stack.outputs()
    return {k: v.value for k, v in outputs.items()}


def find_orphaned_receipts(dynamo_client: DynamoClient) -> list[Receipt]:
    """
    Find receipts without a parent Image entity.
    """
    orphaned = []
    last_key = None
    total_scanned = 0

    while True:
        receipts, last_key = dynamo_client.list_receipts(
            limit=1000,
            last_evaluated_key=last_key,
        )
        total_scanned += len(receipts)

        for receipt in receipts:
            try:
                dynamo_client.get_image(receipt.image_id)
            except EntityNotFoundError:
                # Image doesn't exist - this receipt is orphaned
                orphaned.append(receipt)

        logger.info(
            f"  Scanned {total_scanned} receipts, "
            f"found {len(orphaned)} orphaned..."
        )

        if last_key is None:
            break

    return orphaned


def delete_receipt_and_related(
    dynamo_client: DynamoClient,
    s3_client,
    receipt: Receipt,
    delete_s3: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Delete a receipt and all its related entities.
    """
    result = {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "success": False,
        "deleted": {
            "lines": 0,
            "words": 0,
            "letters": 0,
            "labels": 0,
            "receipt": 0,
            "s3": 0,
        },
        "error": None,
    }

    try:
        # Get all related entities
        lines = []
        words = []
        letters = []
        labels = []

        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(
                receipt.image_id, receipt.receipt_id
            )
        except Exception:
            pass

        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                receipt.image_id, receipt.receipt_id
            )
        except Exception:
            pass

        try:
            letters = dynamo_client.list_receipt_letters_from_receipt(
                receipt.image_id, receipt.receipt_id
            )
        except Exception:
            pass

        try:
            labels = dynamo_client.list_receipt_word_labels_from_receipt(
                receipt.image_id, receipt.receipt_id
            )
        except Exception:
            pass

        logger.info(
            f"  Found: {len(lines)} lines, {len(words)} words, "
            f"{len(letters)} letters, {len(labels)} labels"
        )

        if dry_run:
            logger.info(f"  [DRY RUN] Would delete all entities")
            result["deleted"]["lines"] = len(lines)
            result["deleted"]["words"] = len(words)
            result["deleted"]["letters"] = len(letters)
            result["deleted"]["labels"] = len(labels)
            result["deleted"]["receipt"] = 1
            if delete_s3:
                result["deleted"]["s3"] = 1
            result["success"] = True
            return result

        # Delete in order: labels -> letters -> words -> lines -> receipt
        if labels:
            dynamo_client.delete_receipt_word_labels(labels)
            result["deleted"]["labels"] = len(labels)
            logger.info(f"  Deleted {len(labels)} labels")

        if letters:
            dynamo_client.delete_receipt_letters(letters)
            result["deleted"]["letters"] = len(letters)
            logger.info(f"  Deleted {len(letters)} letters")

        if words:
            dynamo_client.delete_receipt_words(words)
            result["deleted"]["words"] = len(words)
            logger.info(f"  Deleted {len(words)} words")

        if lines:
            dynamo_client.delete_receipt_lines(lines)
            result["deleted"]["lines"] = len(lines)
            logger.info(f"  Deleted {len(lines)} lines")

        # Delete the receipt itself
        dynamo_client.delete_receipt(receipt)
        result["deleted"]["receipt"] = 1
        logger.info(f"  Deleted receipt")

        # Optionally delete S3 raw image
        if delete_s3 and receipt.raw_s3_bucket and receipt.raw_s3_key:
            try:
                s3_client.delete_object(
                    Bucket=receipt.raw_s3_bucket, Key=receipt.raw_s3_key
                )
                result["deleted"]["s3"] = 1
                logger.info(f"  Deleted S3 object: s3://{receipt.raw_s3_bucket}/{receipt.raw_s3_key}")
            except Exception as e:
                logger.warning(f"  Failed to delete S3 object: {e}")

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"  Error: {e}")

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Cleanup orphaned receipts (no parent Image entity)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--stack",
        default="tnorlund/portfolio/dev",
        help="Pulumi stack name (default: tnorlund/portfolio/dev)",
    )
    parser.add_argument(
        "--delete-s3",
        action="store_true",
        help="Also delete raw S3 images (default: keep them)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of receipts to process (for testing)",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    work_dir = os.path.join(parent_dir, "infra")
    logger.info(f"Getting Pulumi stack outputs from {args.stack}...")
    outputs = get_pulumi_outputs(args.stack, work_dir)

    table_name = outputs.get("dynamodb_table_name")
    logger.info(f"DynamoDB table: {table_name}")

    if not table_name:
        logger.error("Missing required Pulumi output: dynamodb_table_name")
        sys.exit(1)

    # Initialize clients
    s3 = boto3.client("s3")
    dynamo_client = DynamoClient(table_name=table_name)

    if args.dry_run:
        logger.info("\n*** DRY RUN MODE - No changes will be made ***\n")

    # Find orphaned receipts
    logger.info("Finding orphaned receipts (no parent Image entity)...")
    orphaned = find_orphaned_receipts(dynamo_client)
    logger.info(f"Found {len(orphaned)} orphaned receipts")

    if not orphaned:
        logger.info("No orphaned receipts found!")
        return

    # Apply limit if specified
    if args.limit:
        orphaned = orphaned[: args.limit]
        logger.info(f"Processing first {args.limit} receipts (--limit)")

    logger.info(f"\nProcessing {len(orphaned)} orphaned receipts...\n")

    # Process each receipt
    results = []
    total_deleted = {
        "lines": 0,
        "words": 0,
        "letters": 0,
        "labels": 0,
        "receipts": 0,
        "s3": 0,
    }

    for i, receipt in enumerate(orphaned, 1):
        logger.info(
            f"[{i}/{len(orphaned)}] Processing "
            f"{receipt.image_id}_{receipt.receipt_id}..."
        )
        result = delete_receipt_and_related(
            dynamo_client=dynamo_client,
            s3_client=s3,
            receipt=receipt,
            delete_s3=args.delete_s3,
            dry_run=args.dry_run,
        )
        results.append(result)

        # Accumulate totals
        for key in ["lines", "words", "letters", "labels"]:
            total_deleted[key] += result["deleted"].get(key, 0)
        total_deleted["receipts"] += result["deleted"].get("receipt", 0)
        total_deleted["s3"] += result["deleted"].get("s3", 0)

    # Print summary
    successes = sum(1 for r in results if r["success"])
    failures = sum(1 for r in results if r["error"])

    logger.info("\n" + "=" * 60)
    logger.info("CLEANUP SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Orphaned receipts processed: {len(results)}")
    logger.info(f"Successful: {successes}")
    logger.info(f"Failed: {failures}")
    logger.info("")
    logger.info("Entities deleted:")
    logger.info(f"  Receipts: {total_deleted['receipts']}")
    logger.info(f"  Lines: {total_deleted['lines']}")
    logger.info(f"  Words: {total_deleted['words']}")
    logger.info(f"  Letters: {total_deleted['letters']}")
    logger.info(f"  Labels: {total_deleted['labels']}")
    logger.info(f"  S3 objects: {total_deleted['s3']}")

    if failures > 0:
        logger.info("\nFailed receipts:")
        for r in results:
            if r["error"]:
                logger.error(f"  {r['image_id']}_{r['receipt_id']}: {r['error']}")

    if not args.dry_run and successes == len(results):
        logger.info("\nAll orphaned receipts successfully cleaned up!")
    elif args.dry_run:
        logger.info(
            "\n[DRY RUN] No changes were made. "
            "Run without --dry-run to apply cleanup."
        )


if __name__ == "__main__":
    main()
