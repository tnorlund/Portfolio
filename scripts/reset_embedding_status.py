#!/usr/bin/env python3
"""
Reset embedding statuses and delete batch summaries.

This script:
1. Deletes BatchSummary records from DynamoDB (filtered by collection)
2. Resets ReceiptWords and/or ReceiptLines to NONE status
3. Optionally clears ChromaDB snapshots from S3

Usage:
    # Reset only LINES (for row-based embedding migration)
    python scripts/reset_embedding_status.py --env dev --collection lines --dry-run
    python scripts/reset_embedding_status.py --env dev --collection lines --no-dry-run --clear-snapshots

    # Reset only WORDS
    python scripts/reset_embedding_status.py --env dev --collection words --no-dry-run

    # Reset both (default, legacy behavior)
    python scripts/reset_embedding_status.py --env dev --dry-run
    python scripts/reset_embedding_status.py --env dev --no-dry-run --clear-snapshots

    # Use parallel workers for faster processing (recommended: 8-16)
    python scripts/reset_embedding_status.py --env dev --collection lines --parallel 10 --no-dry-run
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

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


def delete_all_batch_summaries(
    dynamo: DynamoClient, dry_run: bool = True, collection: str = "both"
) -> int:
    """Delete BatchSummary records, optionally filtered by collection.

    Args:
        dynamo: DynamoDB client
        dry_run: If True, don't actually delete
        collection: "lines", "words", or "both"
    """
    logger.info("Fetching BatchSummary records...")

    batch_summaries = []
    last_key = None
    while True:
        batch_page, last_key = dynamo.list_batch_summaries(
            limit=100, last_evaluated_key=last_key
        )
        batch_summaries.extend(batch_page)
        if last_key is None:
            break

    # Filter by batch_type based on collection
    if collection == "lines":
        batch_summaries = [
            bs for bs in batch_summaries if bs.batch_type == "LINE_EMBEDDING"
        ]
        logger.info(f"Found {len(batch_summaries)} LINE_EMBEDDING BatchSummary records")
    elif collection == "words":
        batch_summaries = [
            bs for bs in batch_summaries if bs.batch_type == "WORD_EMBEDDING"
        ]
        logger.info(f"Found {len(batch_summaries)} WORD_EMBEDDING BatchSummary records")
    else:
        logger.info(f"Found {len(batch_summaries)} BatchSummary records (all types)")

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


def reset_embedding_status(
    dynamo: DynamoClient,
    dry_run: bool = True,
    collection: str = "both",
    parallel: int = 1,
) -> dict:
    """Reset ReceiptWords and/or ReceiptLines to NONE status.

    Args:
        dynamo: DynamoDB client
        dry_run: If True, don't actually update
        collection: "lines", "words", or "both"
        parallel: Number of parallel workers (1 = sequential)
    """
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
    stats_lock = Lock()

    reset_words = collection in ("words", "both")
    reset_lines = collection in ("lines", "both")

    def process_image(img, idx):
        """Process a single image - thread-safe."""
        local_stats = {"words_reset": 0, "lines_reset": 0, "errors": []}

        try:
            details = dynamo.get_image_details(img.image_id)
        except Exception:
            logger.exception("Error getting details for %s", img.image_id)
            local_stats["errors"].append(f"get_details:{img.image_id}")
            return local_stats

        # Reset ReceiptWords to NONE (if requested)
        if reset_words:
            words_to_update = []
            for rw in details.receipt_words:
                rw.embedding_status = EmbeddingStatus.NONE.value
                words_to_update.append(rw)

            if words_to_update:
                if dry_run:
                    local_stats["words_reset"] += len(words_to_update)
                else:
                    try:
                        for i in range(0, len(words_to_update), 25):
                            chunk = words_to_update[i : i + 25]
                            dynamo.update_receipt_words(chunk)
                        local_stats["words_reset"] += len(words_to_update)
                    except Exception:
                        logger.exception("Error updating words for %s", img.image_id)
                        local_stats["errors"].append(f"update_words:{img.image_id}")

        # Reset ReceiptLines to NONE (if requested)
        if reset_lines:
            lines_to_update = []
            for rl in details.receipt_lines:
                rl.embedding_status = EmbeddingStatus.NONE.value
                lines_to_update.append(rl)

            if lines_to_update:
                if dry_run:
                    local_stats["lines_reset"] += len(lines_to_update)
                else:
                    try:
                        for i in range(0, len(lines_to_update), 25):
                            chunk = lines_to_update[i : i + 25]
                            dynamo.update_receipt_lines(chunk)
                        local_stats["lines_reset"] += len(lines_to_update)
                    except Exception:
                        logger.exception("Error updating lines for %s", img.image_id)
                        local_stats["errors"].append(f"update_lines:{img.image_id}")

        return local_stats

    if parallel > 1:
        # Parallel processing
        logger.info("Processing with %d parallel workers", parallel)
        completed = 0
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {
                executor.submit(process_image, img, idx): idx
                for idx, img in enumerate(images)
            }
            for future in as_completed(futures):
                local_stats = future.result()
                with stats_lock:
                    stats["words_reset"] += local_stats["words_reset"]
                    stats["lines_reset"] += local_stats["lines_reset"]
                    stats["errors"].extend(local_stats["errors"])
                    completed += 1
                    if completed % 50 == 0 or completed == len(images):
                        progress_parts = []
                        if reset_words:
                            progress_parts.append(f"words: {stats['words_reset']}")
                        if reset_lines:
                            progress_parts.append(f"lines: {stats['lines_reset']}")
                        logger.info(
                            "Completed %d/%d images (%s)",
                            completed,
                            len(images),
                            ", ".join(progress_parts),
                        )
    else:
        # Sequential processing (original behavior)
        for idx, img in enumerate(images):
            if idx % 20 == 0:
                progress_parts = []
                if reset_words:
                    progress_parts.append(f"words: {stats['words_reset']}")
                if reset_lines:
                    progress_parts.append(f"lines: {stats['lines_reset']}")
                logger.info(
                    "Processing image %d/%d (%s)",
                    idx + 1,
                    len(images),
                    ", ".join(progress_parts),
                )
            local_stats = process_image(img, idx)
            stats["words_reset"] += local_stats["words_reset"]
            stats["lines_reset"] += local_stats["lines_reset"]
            stats["errors"].extend(local_stats["errors"])

    return stats


def clear_chromadb_snapshots(
    bucket: str, dry_run: bool = True, collection: str = "both"
) -> dict:
    """Clear ChromaDB snapshots from S3.

    Args:
        bucket: S3 bucket name
        dry_run: If True, don't actually delete
        collection: "lines", "words", or "both"
    """
    s3 = boto3.client("s3")
    stats = {"lines_deleted": 0, "words_deleted": 0, "errors": []}

    # Determine which collections to clear
    if collection == "lines":
        collections_to_clear = ["lines"]
    elif collection == "words":
        collections_to_clear = ["words"]
    else:
        collections_to_clear = ["lines", "words"]

    for coll in collections_to_clear:
        prefix = f"{coll}/snapshot/"
        logger.info("Listing objects in s3://%s/%s", bucket, prefix)

        try:
            paginator = s3.get_paginator("list_objects_v2")
            objects_to_delete = []

            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    objects_to_delete.append({"Key": obj["Key"]})

            if not objects_to_delete:
                logger.info("No objects found for %s snapshots", coll)
                continue

            logger.info(
                "Found %d objects to delete for %s",
                len(objects_to_delete),
                coll,
            )

            if dry_run:
                stats[f"{coll}_deleted"] = len(objects_to_delete)
            else:
                # Delete in batches of 1000 (S3 limit)
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i : i + 1000]
                    s3.delete_objects(
                        Bucket=bucket, Delete={"Objects": batch}
                    )
                    stats[f"{coll}_deleted"] += len(batch)
                    logger.info(
                        "Deleted %d/%d objects for %s",
                        stats[f"{coll}_deleted"],
                        len(objects_to_delete),
                        coll,
                    )

        except Exception:
            logger.exception("Error clearing %s snapshots", coll)
            stats["errors"].append(f"clear_{coll}")

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
        "--collection",
        type=str,
        default="both",
        choices=["lines", "words", "both"],
        help="Which collection to reset (default: both)",
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
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of parallel workers for processing images (default: 1 = sequential)",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info("Mode: %s", mode)
    logger.info("Environment: %s", args.env.upper())
    logger.info("Collection: %s", args.collection.upper())

    if not args.dry_run:
        collection_desc = (
            f"{args.collection.upper()} collection"
            if args.collection != "both"
            else "ALL collections"
        )
        logger.warning(
            f"This will DELETE {collection_desc} BatchSummary records and "
            f"RESET {collection_desc} embedding statuses!"
        )
        if args.clear_snapshots:
            logger.warning(
                f"This will also DELETE {collection_desc} ChromaDB snapshots from S3!"
            )
        logger.warning("Press Ctrl+C within 5 seconds to abort...")
        import time

        time.sleep(5)

    # Load config and create client
    config = load_env(env=args.env)
    # Use larger connection pool when running in parallel
    max_pool = args.parallel * 2 if args.parallel > 1 else None
    dynamo = DynamoClient(
        config["dynamodb_table_name"],
        max_pool_connections=max_pool,
    )

    collection_label = (
        args.collection.upper() if args.collection != "both" else "ALL"
    )

    logger.info("=" * 60)
    logger.info(f"Step 1: Delete {collection_label} BatchSummary records")
    logger.info("=" * 60)
    deleted_count = delete_all_batch_summaries(
        dynamo, dry_run=args.dry_run, collection=args.collection
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"Step 2: Reset {collection_label} embedding statuses to NONE")
    logger.info("=" * 60)
    stats = reset_embedding_status(
        dynamo,
        dry_run=args.dry_run,
        collection=args.collection,
        parallel=args.parallel,
    )

    snapshot_stats = None
    if args.clear_snapshots:
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Step 3: Clear {collection_label} ChromaDB snapshots from S3")
        logger.info("=" * 60)
        bucket = config.get("chromadb_bucket_name") or config.get(
            "embedding_chromadb_bucket_name"
        )
        if bucket:
            snapshot_stats = clear_chromadb_snapshots(
                bucket, dry_run=args.dry_run, collection=args.collection
            )
        else:
            logger.error("Could not find ChromaDB bucket in config")

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY (collection: %s)", args.collection.upper())
    logger.info("=" * 60)
    logger.info("BatchSummary records deleted: %d", deleted_count)
    if args.collection in ("words", "both"):
        logger.info("ReceiptWords reset to NONE: %d", stats["words_reset"])
    if args.collection in ("lines", "both"):
        logger.info("ReceiptLines reset to NONE: %d", stats["lines_reset"])

    if snapshot_stats:
        if args.collection in ("lines", "both"):
            logger.info(
                "S3 lines snapshot objects deleted: %d",
                snapshot_stats.get("lines_deleted", 0),
            )
        if args.collection in ("words", "both"):
            logger.info(
                "S3 words snapshot objects deleted: %d",
                snapshot_stats.get("words_deleted", 0),
            )

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
