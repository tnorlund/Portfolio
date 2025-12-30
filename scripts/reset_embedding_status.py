#!/usr/bin/env python3
"""
Reset all embedding statuses and delete batch summaries.

This script:
1. Deletes ALL BatchSummary records from DynamoDB
2. Resets ALL ReceiptWords with PENDING status to NONE
3. Resets ALL ReceiptLines with PENDING status to NONE

Usage:
    python scripts/reset_embedding_status.py --env prod --dry-run
    python scripts/reset_embedding_status.py --env prod --no-dry-run
"""

import argparse
import logging
import os
import sys
from typing import List

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_word import EmbeddingStatus

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
        except Exception as e:
            logger.error(f"Error deleting batch summaries chunk: {e}")
            # Try one by one
            for bs in chunk:
                try:
                    dynamo.delete_batch_summary(bs)
                    deleted += 1
                except Exception as e2:
                    logger.error(f"Failed to delete {bs.batch_id}: {e2}")

    return deleted


def reset_embedding_status(dynamo: DynamoClient, dry_run: bool = True) -> dict:
    """Reset all PENDING ReceiptWords and ReceiptLines to NONE."""
    logger.info("Fetching all images...")

    images = []
    last_key = None
    while True:
        batch, last_key = dynamo.list_images(limit=100, last_evaluated_key=last_key)
        images.extend(batch)
        if last_key is None:
            break

    logger.info(f"Found {len(images)} images to process")

    stats = {
        "words_reset": 0,
        "lines_reset": 0,
        "words_already_none": 0,
        "lines_already_none": 0,
        "words_success": 0,
        "lines_success": 0,
        "errors": [],
    }

    for idx, img in enumerate(images):
        if idx % 20 == 0:
            logger.info(
                f"Processing image {idx + 1}/{len(images)} "
                f"(words: {stats['words_reset']}, lines: {stats['lines_reset']})"
            )

        try:
            details = dynamo.get_image_details(img.image_id)
        except Exception as e:
            logger.error(f"Error getting details for {img.image_id}: {e}")
            stats["errors"].append(f"get_details:{img.image_id}:{e}")
            continue

        # Process ReceiptWords
        words_to_update = []
        for rw in details.receipt_words:
            status = rw.embedding_status
            if isinstance(status, str):
                status = status.upper()
            elif hasattr(status, "value"):
                status = status.value

            if status == "PENDING":
                rw.embedding_status = EmbeddingStatus.NONE.value
                words_to_update.append(rw)
            elif status == "NONE":
                stats["words_already_none"] += 1
            elif status == "SUCCESS":
                stats["words_success"] += 1

        if words_to_update:
            if dry_run:
                stats["words_reset"] += len(words_to_update)
            else:
                try:
                    # Update in chunks of 25
                    for i in range(0, len(words_to_update), 25):
                        chunk = words_to_update[i : i + 25]
                        dynamo.update_receipt_words(chunk)
                    stats["words_reset"] += len(words_to_update)
                except Exception as e:
                    logger.error(f"Error updating words for {img.image_id}: {e}")
                    stats["errors"].append(f"update_words:{img.image_id}:{e}")

        # Process ReceiptLines
        lines_to_update = []
        for rl in details.receipt_lines:
            status = rl.embedding_status
            if isinstance(status, str):
                status = status.upper()
            elif hasattr(status, "value"):
                status = status.value

            if status == "PENDING":
                rl.embedding_status = EmbeddingStatus.NONE.value
                lines_to_update.append(rl)
            elif status == "NONE":
                stats["lines_already_none"] += 1
            elif status == "SUCCESS":
                stats["lines_success"] += 1

        if lines_to_update:
            if dry_run:
                stats["lines_reset"] += len(lines_to_update)
            else:
                try:
                    # Update in chunks of 25
                    for i in range(0, len(lines_to_update), 25):
                        chunk = lines_to_update[i : i + 25]
                        dynamo.update_receipt_lines(chunk)
                    stats["lines_reset"] += len(lines_to_update)
                except Exception as e:
                    logger.error(f"Error updating lines for {img.image_id}: {e}")
                    stats["errors"].append(f"update_lines:{img.image_id}:{e}")

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

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"Mode: {mode}")
    logger.info(f"Environment: {args.env.upper()}")

    if not args.dry_run:
        logger.warning("This will DELETE all BatchSummary records and RESET embedding statuses!")
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
    logger.info("Step 2: Reset PENDING embedding statuses to NONE")
    logger.info("=" * 60)
    stats = reset_embedding_status(dynamo, dry_run=args.dry_run)

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"BatchSummary records deleted: {deleted_count}")
    logger.info(f"ReceiptWords reset PENDING -> NONE: {stats['words_reset']}")
    logger.info(f"ReceiptWords already NONE: {stats['words_already_none']}")
    logger.info(f"ReceiptWords SUCCESS (unchanged): {stats['words_success']}")
    logger.info(f"ReceiptLines reset PENDING -> NONE: {stats['lines_reset']}")
    logger.info(f"ReceiptLines already NONE: {stats['lines_already_none']}")
    logger.info(f"ReceiptLines SUCCESS (unchanged): {stats['lines_success']}")

    if stats["errors"]:
        logger.warning(f"\n{len(stats['errors'])} errors occurred:")
        for err in stats["errors"][:10]:
            logger.warning(f"  - {err}")
        if len(stats["errors"]) > 10:
            logger.warning(f"  ... and {len(stats['errors']) - 10} more")

    if args.dry_run:
        logger.info("\n[DRY RUN] No changes were made. Use --no-dry-run to apply changes.")


if __name__ == "__main__":
    main()
