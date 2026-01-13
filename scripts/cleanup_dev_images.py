#!/usr/bin/env python3
"""
Delete images from dev that don't exist in prod.

This script:
1. Fetches all image IDs from dev and prod
2. Finds images in dev that are NOT in prod
3. Deletes those images AND all their child records (cascade delete)

Usage:
    python scripts/cleanup_dev_images.py --dry-run
    python scripts/cleanup_dev_images.py --no-dry-run
"""

import argparse
import logging
import os
import sys
import time
from typing import List, Set

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_all_image_ids(dynamo: DynamoClient) -> Set[str]:
    """Fetch all image IDs from a DynamoDB table."""
    image_ids = set()
    last_key = None

    while True:
        images, last_key = dynamo.list_images(limit=100, last_evaluated_key=last_key)
        for img in images:
            image_ids.add(img.image_id)

        if last_key is None:
            break

    return image_ids


def delete_image_cascade(dynamo: DynamoClient, image_id: str, dry_run: bool = True) -> dict:
    """
    Delete an image and ALL its child records.

    Returns stats about what was deleted.
    """
    stats = {
        "receipts": 0,
        "receipt_lines": 0,
        "receipt_words": 0,
        "receipt_letters": 0,
        "receipt_word_labels": 0,
        "receipt_places": 0,
        "lines": 0,
        "words": 0,
        "letters": 0,
        "ocr_jobs": 0,
        "ocr_routing_decisions": 0,
        "errors": [],
    }

    try:
        # Get full image details (includes all children)
        details = dynamo.get_image_details(image_id)
    except Exception as e:
        stats["errors"].append(f"get_image_details: {e}")
        return stats

    # Delete receipt word labels first (deepest level)
    if details.receipt_word_labels:
        stats["receipt_word_labels"] = len(details.receipt_word_labels)
        if not dry_run:
            try:
                for i in range(0, len(details.receipt_word_labels), 25):
                    chunk = details.receipt_word_labels[i:i + 25]
                    dynamo.delete_receipt_word_labels(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_receipt_word_labels: {e}")

    # Delete receipt letters
    if details.receipt_letters:
        stats["receipt_letters"] = len(details.receipt_letters)
        if not dry_run:
            try:
                for i in range(0, len(details.receipt_letters), 25):
                    chunk = details.receipt_letters[i:i + 25]
                    dynamo.delete_receipt_letters(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_receipt_letters: {e}")

    # Delete receipt words
    if details.receipt_words:
        stats["receipt_words"] = len(details.receipt_words)
        if not dry_run:
            try:
                for i in range(0, len(details.receipt_words), 25):
                    chunk = details.receipt_words[i:i + 25]
                    dynamo.delete_receipt_words(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_receipt_words: {e}")

    # Delete receipt lines
    if details.receipt_lines:
        stats["receipt_lines"] = len(details.receipt_lines)
        if not dry_run:
            try:
                for i in range(0, len(details.receipt_lines), 25):
                    chunk = details.receipt_lines[i:i + 25]
                    dynamo.delete_receipt_lines(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_receipt_lines: {e}")

    # Delete receipt places
    if details.receipt_places:
        stats["receipt_places"] = len(details.receipt_places)
        if not dry_run:
            try:
                for i in range(0, len(details.receipt_places), 25):
                    chunk = details.receipt_places[i:i + 25]
                    dynamo.delete_receipt_places(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_receipt_places: {e}")

    # Delete receipts
    if details.receipts:
        stats["receipts"] = len(details.receipts)
        if not dry_run:
            try:
                for i in range(0, len(details.receipts), 25):
                    chunk = details.receipts[i:i + 25]
                    dynamo.delete_receipts(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_receipts: {e}")

    # Delete OCR letters/words/lines (non-receipt) - children before parents
    if details.letters:
        stats["letters"] = len(details.letters)
        if not dry_run:
            try:
                for i in range(0, len(details.letters), 25):
                    chunk = details.letters[i:i + 25]
                    dynamo.delete_letters(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_letters: {e}")

    if details.words:
        stats["words"] = len(details.words)
        if not dry_run:
            try:
                for i in range(0, len(details.words), 25):
                    chunk = details.words[i:i + 25]
                    dynamo.delete_words(chunk)
            except Exception as e:
                stats["errors"].append(f"delete_words: {e}")

    if details.lines:
        stats["lines"] = len(details.lines)
        if not dry_run:
            try:
                dynamo.delete_lines_from_image(image_id)
            except Exception as e:
                stats["errors"].append(f"delete_lines_from_image: {e}")

    # Delete OCR jobs
    if details.ocr_jobs:
        stats["ocr_jobs"] = len(details.ocr_jobs)
        if not dry_run:
            try:
                dynamo.delete_ocr_jobs(details.ocr_jobs)
            except Exception as e:
                stats["errors"].append(f"delete_ocr_jobs: {e}")

    # Delete OCR routing decisions
    if details.ocr_routing_decisions:
        stats["ocr_routing_decisions"] = len(details.ocr_routing_decisions)
        if not dry_run:
            try:
                dynamo.delete_ocr_routing_decisions(details.ocr_routing_decisions)
            except Exception as e:
                stats["errors"].append(f"delete_ocr_routing_decisions: {e}")

    # Finally, delete the image itself
    if not dry_run:
        try:
            dynamo.delete_image(image_id)
        except Exception as e:
            stats["errors"].append(f"delete_image: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Delete dev images that don't exist in prod"
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
        "--limit",
        type=int,
        default=None,
        help="Limit number of images to delete (for testing)",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"Mode: {mode}")

    if not args.dry_run:
        logger.warning("This will DELETE images from DEV that don't exist in PROD!")
        logger.warning("Press Ctrl+C within 5 seconds to abort...")
        time.sleep(5)

    # Load configs for both environments
    logger.info("Loading dev config...")
    dev_config = load_env(env="dev")
    dev_dynamo = DynamoClient(dev_config["dynamodb_table_name"])

    logger.info("Loading prod config...")
    prod_config = load_env(env="prod")
    prod_dynamo = DynamoClient(prod_config["dynamodb_table_name"])

    # Get all image IDs from both environments
    logger.info("Fetching all image IDs from DEV...")
    dev_image_ids = get_all_image_ids(dev_dynamo)
    logger.info(f"Found {len(dev_image_ids)} images in DEV")

    logger.info("Fetching all image IDs from PROD...")
    prod_image_ids = get_all_image_ids(prod_dynamo)
    logger.info(f"Found {len(prod_image_ids)} images in PROD")

    # Find images in dev that are NOT in prod
    images_to_delete = dev_image_ids - prod_image_ids
    logger.info(f"Found {len(images_to_delete)} images in DEV that are NOT in PROD")

    if not images_to_delete:
        logger.info("No images to delete. DEV and PROD are in sync.")
        return

    # Apply limit if specified
    if args.limit:
        images_to_delete = set(list(images_to_delete)[:args.limit])
        logger.info(f"Limited to {len(images_to_delete)} images")

    # Delete each image with cascade
    total_stats = {
        "images_deleted": 0,
        "receipts": 0,
        "receipt_lines": 0,
        "receipt_words": 0,
        "receipt_letters": 0,
        "receipt_word_labels": 0,
        "receipt_places": 0,
        "lines": 0,
        "words": 0,
        "letters": 0,
        "ocr_jobs": 0,
        "ocr_routing_decisions": 0,
        "errors": [],
    }

    for idx, image_id in enumerate(images_to_delete, 1):
        if idx % 10 == 0 or idx == 1:
            logger.info(f"Processing image {idx}/{len(images_to_delete)}: {image_id[:8]}...")

        stats = delete_image_cascade(dev_dynamo, image_id, dry_run=args.dry_run)

        total_stats["images_deleted"] += 1
        for key in ["receipts", "receipt_lines", "receipt_words", "receipt_letters",
                    "receipt_word_labels", "receipt_places",
                    "lines", "words", "letters", "ocr_jobs", "ocr_routing_decisions"]:
            total_stats[key] += stats.get(key, 0)
        total_stats["errors"].extend(stats.get("errors", []))

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Images to delete: {total_stats['images_deleted']}")
    logger.info(f"  Receipts: {total_stats['receipts']}")
    logger.info(f"  Receipt Lines: {total_stats['receipt_lines']}")
    logger.info(f"  Receipt Words: {total_stats['receipt_words']}")
    logger.info(f"  Receipt Letters: {total_stats['receipt_letters']}")
    logger.info(f"  Receipt Word Labels: {total_stats['receipt_word_labels']}")
    logger.info(f"  Receipt Places: {total_stats['receipt_places']}")
    logger.info(f"  Lines (OCR): {total_stats['lines']}")
    logger.info(f"  Words (OCR): {total_stats['words']}")
    logger.info(f"  Letters (OCR): {total_stats['letters']}")
    logger.info(f"  OCR Jobs: {total_stats['ocr_jobs']}")
    logger.info(f"  OCR Routing Decisions: {total_stats['ocr_routing_decisions']}")

    if total_stats["errors"]:
        logger.warning(f"\n{len(total_stats['errors'])} errors occurred:")
        for err in total_stats["errors"][:10]:
            logger.warning(f"  - {err}")
        if len(total_stats["errors"]) > 10:
            logger.warning(f"  ... and {len(total_stats['errors']) - 10} more")

    if args.dry_run:
        logger.info("\n[DRY RUN] No changes were made. Use --no-dry-run to apply changes.")


if __name__ == "__main__":
    main()
