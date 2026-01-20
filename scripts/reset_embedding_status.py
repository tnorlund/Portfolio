#!/usr/bin/env python3
"""
Reset all embedding statuses and delete batch summaries.

This script:
1. Deletes ALL BatchSummary records from DynamoDB
2. Resets ALL ReceiptWords to NONE status
3. Resets ALL ReceiptLines to NONE status
4. Optionally clears ChromaDB snapshots from S3

Usage:
    python scripts/reset_embedding_status.py --env dev --dry-run
    python scripts/reset_embedding_status.py --env dev --no-dry-run
    python scripts/reset_embedding_status.py --env dev --no-dry-run --clear-snapshots
"""

import argparse
import logging
import os
import sys

import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def delete_all_batch_summaries(dynamo: DynamoClient, dry_run: bool = True) -> int:
    """Delete all BatchSummary records."""
    logger.info("Fetching all BatchSummary records...")

    batch_summaries = []
    last_key = None
    while True:
        batch_page, last_key = dynamo.list_batch_summaries(
            limit=100, last_evaluated_key=last_key
        )
        batch_summaries.extend(batch_page)
        if last_key is None:
            break

    logger.info(f"Found {len(batch_summaries)} BatchSummary records")

    if dry_run:
        logger.info("[DRY RUN] Would delete %d BatchSummary records", len(batch_summaries))
        return len(batch_summaries)

    # Delete in chunks of 25 (DynamoDB transaction limit)
    deleted = 0
    for i in range(0, len(batch_summaries), 25):
        chunk = batch_summaries[i : i + 25]
        try:
            dynamo.delete_batch_summaries(chunk)
            deleted += len(chunk)
            if deleted % 100 == 0 or deleted == len(batch_summaries):
                logger.info(f"Deleted {deleted}/{len(batch_summaries)} BatchSummary records")
        except Exception:
            logger.exception("Error deleting batch summaries chunk")
            # Try one by one
            for bs in chunk:
                try:
                    dynamo.delete_batch_summary(bs)
                    deleted += 1
                except Exception:
                    logger.exception("Failed to delete %s", bs.batch_id)

    return deleted


def reset_embedding_status(dynamo: DynamoClient, dry_run: bool = True) -> dict:
    """Reset ALL ReceiptWords and ReceiptLines to NONE status."""
    logger.info("Fetching all images...")

    images = []
    last_key = None
    while True:
        batch, last_key = dynamo.list_images(limit=100, last_evaluated_key=last_key)
        images.extend(batch)
        if last_key is None:
            break

    logger.info("Found %d images to process", len(images))

    stats = {
        "words_reset": 0,
        "lines_reset": 0,
        "errors": [],
    }

    for idx, img in enumerate(images):
        if idx % 20 == 0:
            logger.info(
                "Processing image %d/%d (words: %d, lines: %d)",
                idx + 1,
                len(images),
                stats["words_reset"],
                stats["lines_reset"],
            )

        try:
            details = dynamo.get_image_details(img.image_id)
        except Exception:
            logger.exception("Error getting details for %s", img.image_id)
            stats["errors"].append(f"get_details:{img.image_id}")
            continue

        # Reset ALL ReceiptWords to NONE
        words_to_update = []
        for rw in details.receipt_words:
            rw.embedding_status = EmbeddingStatus.NONE.value
            words_to_update.append(rw)

        if words_to_update:
            if dry_run:
                stats["words_reset"] += len(words_to_update)
            else:
                try:
                    for i in range(0, len(words_to_update), 25):
                        chunk = words_to_update[i : i + 25]
                        dynamo.update_receipt_words(chunk)
                    stats["words_reset"] += len(words_to_update)
                except Exception:
                    logger.exception("Error updating words for %s", img.image_id)
                    stats["errors"].append(f"update_words:{img.image_id}")

        # Reset ALL ReceiptLines to NONE
        lines_to_update = []
        for rl in details.receipt_lines:
            rl.embedding_status = EmbeddingStatus.NONE.value
            lines_to_update.append(rl)

        if lines_to_update:
            if dry_run:
                stats["lines_reset"] += len(lines_to_update)
            else:
                try:
                    for i in range(0, len(lines_to_update), 25):
                        chunk = lines_to_update[i : i + 25]
                        dynamo.update_receipt_lines(chunk)
                    stats["lines_reset"] += len(lines_to_update)
                except Exception:
                    logger.exception("Error updating lines for %s", img.image_id)
                    stats["errors"].append(f"update_lines:{img.image_id}")

    return stats


def clear_chromadb_snapshots(bucket: str, dry_run: bool = True) -> dict:
    """Clear ChromaDB snapshots from S3."""
    s3 = boto3.client("s3")
    stats = {"lines_deleted": 0, "words_deleted": 0, "errors": []}

    for collection in ["lines", "words"]:
        prefix = f"{collection}/snapshot/"
        logger.info("Listing objects in s3://%s/%s", bucket, prefix)

        try:
            paginator = s3.get_paginator("list_objects_v2")
            objects_to_delete = []

            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    objects_to_delete.append({"Key": obj["Key"]})

            if not objects_to_delete:
                logger.info("No objects found for %s snapshots", collection)
                continue

            logger.info(
                "Found %d objects to delete for %s",
                len(objects_to_delete),
                collection,
            )

            if dry_run:
                stats[f"{collection}_deleted"] = len(objects_to_delete)
            else:
                # Delete in batches of 1000 (S3 limit)
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i : i + 1000]
                    s3.delete_objects(
                        Bucket=bucket, Delete={"Objects": batch}
                    )
                    stats[f"{collection}_deleted"] += len(batch)
                    logger.info(
                        "Deleted %d/%d objects for %s",
                        stats[f"{collection}_deleted"],
                        len(objects_to_delete),
                        collection,
                    )

        except Exception:
            logger.exception("Error clearing %s snapshots", collection)
            stats["errors"].append(f"clear_{collection}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Reset embedding statuses and delete batch summaries"
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
        "--clear-snapshots",
        action="store_true",
        default=False,
        help="Also clear ChromaDB snapshots from S3",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info("Mode: %s", mode)
    logger.info("Environment: %s", args.env.upper())

    if not args.dry_run:
        logger.warning(
            "This will DELETE all BatchSummary records and RESET ALL embedding statuses!"
        )
        if args.clear_snapshots:
            logger.warning("This will also DELETE all ChromaDB snapshots from S3!")
        logger.warning("Press Ctrl+C within 5 seconds to abort...")
        import time

        time.sleep(5)

    # Load config and create client
    config = load_env(env=args.env)
    dynamo = DynamoClient(config["dynamodb_table_name"])

    logger.info("=" * 60)
    logger.info("Step 1: Delete all BatchSummary records")
    logger.info("=" * 60)
    deleted_count = delete_all_batch_summaries(dynamo, dry_run=args.dry_run)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Step 2: Reset ALL embedding statuses to NONE")
    logger.info("=" * 60)
    stats = reset_embedding_status(dynamo, dry_run=args.dry_run)

    snapshot_stats = None
    if args.clear_snapshots:
        logger.info("")
        logger.info("=" * 60)
        logger.info("Step 3: Clear ChromaDB snapshots from S3")
        logger.info("=" * 60)
        bucket = config.get("chromadb_bucket_name") or config.get(
            "embedding_chromadb_bucket_name"
        )
        if bucket:
            snapshot_stats = clear_chromadb_snapshots(bucket, dry_run=args.dry_run)
        else:
            logger.error("Could not find ChromaDB bucket in config")

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("BatchSummary records deleted: %d", deleted_count)
    logger.info("ReceiptWords reset to NONE: %d", stats["words_reset"])
    logger.info("ReceiptLines reset to NONE: %d", stats["lines_reset"])

    if snapshot_stats:
        logger.info("S3 lines snapshot objects deleted: %d", snapshot_stats["lines_deleted"])
        logger.info("S3 words snapshot objects deleted: %d", snapshot_stats["words_deleted"])

    all_errors = stats["errors"]
    if snapshot_stats:
        all_errors.extend(snapshot_stats.get("errors", []))

    if all_errors:
        logger.warning("%d errors occurred:", len(all_errors))
        for err in all_errors[:10]:
            logger.warning("  - %s", err)
        if len(all_errors) > 10:
            logger.warning("  ... and %d more", len(all_errors) - 10)

    if args.dry_run:
        logger.info("[DRY RUN] No changes were made. Use --no-dry-run to apply changes.")


if __name__ == "__main__":
    main()
