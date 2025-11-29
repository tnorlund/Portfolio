#!/usr/bin/env python3
"""
Comprehensive script to reset embeddings and cleanup for fresh batch submission.

This script prepares the system for submitting new embeddings by:
1. Lists all batch summaries and extracts receipt_refs
2. Lists all receipts from DynamoDB
3. Finds receipts NOT in batch summaries (for reporting)
4. Sets embedding_status to NONE for ALL lines/words from ALL receipts (so submit step functions can find them)
5. Deletes all batch summaries (default, or reset to PENDING with --keep-batches)
6. Cleans up S3 (snapshots, poll_results)
7. Deletes all compaction locks

After running this script, you can trigger the submit step functions:
- line-submit-sf-{stack}: Finds lines with embedding_status=NONE and submits to OpenAI
- word-submit-sf-{stack}: Finds words with embedding_status=NONE and submits to OpenAI

Usage:
    # Dry run
    python scripts/reset_embeddings_and_cleanup.py --stack dev --dry-run

    # Execute (default: deletes all batch summaries)
    python scripts/reset_embeddings_and_cleanup.py --stack dev --yes

    # Reset batch summaries to PENDING instead of deleting
    python scripts/reset_embeddings_and_cleanup.py --stack dev --yes --keep-batches
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import BatchStatus, BatchType, EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")
    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )
    return table_name


def get_bucket_name(stack: str) -> str:
    """Get the ChromaDB S3 bucket name from Pulumi stack."""
    env = load_env(env=stack)
    bucket_name = env.get("chromadb_bucket_name") or env.get(
        "embedding_chromadb_bucket_name"
    )
    if not bucket_name:
        raise ValueError(
            f"Could not find chromadb_bucket_name in Pulumi {stack} stack outputs"
        )
    return bucket_name


def list_all_batch_summaries(dynamo_client: DynamoClient) -> List:
    """List all batch summaries from DynamoDB."""
    logger.info("Listing all batch summaries...")
    all_summaries = []
    last_evaluated_key = None

    while True:
        summaries, last_evaluated_key = dynamo_client.list_batch_summaries(
            limit=100, last_evaluated_key=last_evaluated_key
        )
        all_summaries.extend(summaries)
        if not last_evaluated_key:
            break

    logger.info(f"Found {len(all_summaries)} total batch summaries")
    return all_summaries


def extract_receipt_refs_from_batches(batch_summaries: List) -> Set[Tuple[str, int]]:
    """Extract all receipt references from batch summaries."""
    receipt_refs = set()
    for batch in batch_summaries:
        if batch.receipt_refs:
            for image_id, receipt_id in batch.receipt_refs:
                receipt_refs.add((image_id, receipt_id))
    logger.info(f"Extracted {len(receipt_refs)} unique receipt references from batches")
    return receipt_refs


def list_all_receipts(dynamo_client: DynamoClient) -> Set[Tuple[str, int]]:
    """List all receipts from DynamoDB by listing ReceiptMetadata."""
    logger.info("Listing all ReceiptMetadata from DynamoDB...")
    all_receipts = set()
    last_evaluated_key = None

    # Use list_receipt_metadatas to get all receipts
    while True:
        metadatas, last_evaluated_key = dynamo_client.list_receipt_metadatas(
            limit=1000, last_evaluated_key=last_evaluated_key
        )
        for metadata in metadatas:
            all_receipts.add((metadata.image_id, metadata.receipt_id))
        if not last_evaluated_key:
            break

    logger.info(f"Found {len(all_receipts)} total receipts in DynamoDB")
    return all_receipts


def find_receipts_needing_embeddings(
    all_receipts: Set[Tuple[str, int]],
    receipts_in_batches: Set[Tuple[str, int]],
) -> Set[Tuple[str, int]]:
    """Find receipts that are NOT in batch summaries (need embeddings)."""
    receipts_needing_embeddings = all_receipts - receipts_in_batches
    logger.info(
        f"Found {len(receipts_needing_embeddings)} receipts needing embeddings "
        f"({len(all_receipts)} total - {len(receipts_in_batches)} in batches)"
    )
    return receipts_needing_embeddings


def reset_embedding_statuses(
    dynamo_client: DynamoClient,
    receipts: Set[Tuple[str, int]],
    dry_run: bool = False,
) -> Dict:
    """Reset embedding_status to NONE for lines and words of specified receipts."""
    stats = {
        "receipts_processed": 0,
        "receipts_with_lines": 0,
        "receipts_with_words": 0,
        "total_lines_updated": 0,
        "total_words_updated": 0,
        "orphaned_receipts": 0,  # ReceiptMetadata exists but receipt data doesn't
        "errors": [],
    }

    for image_id, receipt_id in receipts:
        try:
            # Get receipt details
            receipt_details = dynamo_client.get_receipt_details(image_id, receipt_id)
            lines = receipt_details.lines
            words = receipt_details.words

            # Update lines
            if lines:
                lines_to_update = [
                    line
                    for line in lines
                    if line.embedding_status != EmbeddingStatus.NONE.value
                ]
                if lines_to_update:
                    for line in lines_to_update:
                        # Ensure we're using string value for DynamoDB
                        line.embedding_status = EmbeddingStatus.NONE.value
                    if not dry_run:
                        dynamo_client.update_receipt_lines(lines_to_update)
                        logger.info(
                            f"Updated {len(lines_to_update)} lines to NONE for {image_id}/{receipt_id}"
                        )
                    else:
                        logger.info(
                            f"[DRY RUN] Would update {len(lines_to_update)} lines to NONE for {image_id}/{receipt_id}"
                        )
                    stats["total_lines_updated"] += len(lines_to_update)
                    stats["receipts_with_lines"] += 1
                else:
                    logger.debug(
                        f"All {len(lines)} lines already have NONE status for {image_id}/{receipt_id}"
                    )

            # Update words
            if words:
                words_to_update = [
                    word
                    for word in words
                    if word.embedding_status != EmbeddingStatus.NONE.value
                ]
                if words_to_update:
                    for word in words_to_update:
                        # Ensure we're using string value for DynamoDB
                        word.embedding_status = EmbeddingStatus.NONE.value
                    if not dry_run:
                        dynamo_client.update_receipt_words(words_to_update)
                        logger.info(
                            f"Updated {len(words_to_update)} words to NONE for {image_id}/{receipt_id}"
                        )
                    else:
                        logger.info(
                            f"[DRY RUN] Would update {len(words_to_update)} words to NONE for {image_id}/{receipt_id}"
                        )
                    stats["total_words_updated"] += len(words_to_update)
                    stats["receipts_with_words"] += 1
                else:
                    logger.debug(
                        f"All {len(words)} words already have NONE status for {image_id}/{receipt_id}"
                    )

            stats["receipts_processed"] += 1

        except Exception as e:
            # Expected edge case: ReceiptMetadata exists but receipt data (lines/words) doesn't
            # This can happen with orphaned metadata or partially deleted receipts
            error_msg = str(e)
            if "not found" in error_msg.lower():
                # This is expected for orphaned receipts - just log at debug level
                logger.debug(
                    f"Skipping orphaned receipt {image_id}/{receipt_id}: {error_msg}"
                )
                stats["orphaned_receipts"] = stats.get("orphaned_receipts", 0) + 1
            else:
                # Unexpected error - log at error level
                logger.error(
                    f"Error processing {image_id}/{receipt_id}: {error_msg}",
                    exc_info=True,
                )
            stats["errors"].append(f"{image_id}/{receipt_id}: {error_msg}")

    return stats


def reset_batch_summaries_to_pending(
    dynamo_client: DynamoClient, dry_run: bool = False
) -> Dict:
    """Reset all batch summaries to PENDING status."""
    logger.info("Resetting all batch summaries to PENDING...")
    all_summaries = list_all_batch_summaries(dynamo_client)

    stats = {"reset": 0, "errors": []}

    for batch in all_summaries:
        # Compare using string value for consistency
        # BatchSummary normalizes enums in __post_init__, so status may be enum or string
        current_status = batch.status.value if isinstance(batch.status, BatchStatus) else str(batch.status)
        if current_status == BatchStatus.PENDING.value:
            continue  # Already PENDING, skip

        if dry_run:
            logger.info(
                f"[DRY RUN] Would reset batch summary {batch.batch_id} from {current_status} to PENDING"
            )
        else:
            try:
                # normalize_enum in BatchSummary.__post_init__ will handle the conversion
                batch.status = BatchStatus.PENDING.value
                dynamo_client.update_batch_summary(batch)
                stats["reset"] += 1
            except Exception as e:
                error_msg = f"Failed to reset {batch.batch_id}: {e}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)

    return stats


def delete_all_batch_summaries(
    dynamo_client: DynamoClient, dry_run: bool = False
) -> Dict:
    """Delete all batch summaries from DynamoDB."""
    logger.info("Deleting all batch summaries...")
    all_summaries = list_all_batch_summaries(dynamo_client)

    stats = {"deleted": 0, "errors": []}

    for batch in all_summaries:
        if dry_run:
            logger.info(f"[DRY RUN] Would delete batch summary {batch.batch_id}")
        else:
            try:
                dynamo_client.delete_batch_summary(batch)
                stats["deleted"] += 1
            except Exception as e:
                error_msg = f"Failed to delete {batch.batch_id}: {e}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)

    return stats


def cleanup_s3_snapshots(bucket: str, dry_run: bool = False) -> Dict:
    """Delete all ChromaDB snapshots from S3."""
    logger.info(f"Cleaning up S3 snapshots in bucket {bucket}...")
    s3 = boto3.client("s3")

    stats = {"prefixes_deleted": 0, "objects_deleted": 0, "errors": []}

    # Delete snapshots for both words and lines collections
    for collection in ["words", "lines"]:
        prefix = f"{collection}/snapshot/"
        try:
            paginator = s3.get_paginator("list_objects_v2")
            objects_deleted = 0

            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                objects = page.get("Contents", [])
                if objects:
                    keys = [obj["Key"] for obj in objects]
                    if dry_run:
                        logger.info(
                            f"[DRY RUN] Would delete {len(keys)} objects from {prefix}"
                        )
                    else:
                        # Delete in batches of 1000 (S3 limit)
                        for i in range(0, len(keys), 1000):
                            batch = keys[i : i + 1000]
                            s3.delete_objects(
                                Bucket=bucket,
                                Delete={"Objects": [{"Key": k} for k in batch]},
                            )
                        objects_deleted += len(keys)
                        logger.info(f"Deleted {len(keys)} objects from {prefix}")

            if objects_deleted > 0:
                stats["objects_deleted"] += objects_deleted
                stats["prefixes_deleted"] += 1

        except Exception as e:
            error_msg = f"Error cleaning up {prefix}: {e}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)

    return stats


def cleanup_s3_poll_results(bucket: str, dry_run: bool = False) -> Dict:
    """Delete all poll_results from S3."""
    logger.info(f"Cleaning up S3 poll_results in bucket {bucket}...")
    s3 = boto3.client("s3")

    stats = {"objects_deleted": 0, "errors": []}

    prefix = "poll_results/"
    try:
        paginator = s3.get_paginator("list_objects_v2")
        all_keys = []

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            all_keys.extend([obj["Key"] for obj in page.get("Contents", [])])

        if all_keys:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would delete {len(all_keys)} objects from {prefix}"
                )
            else:
                # Delete in batches of 1000
                for i in range(0, len(all_keys), 1000):
                    batch = all_keys[i : i + 1000]
                    s3.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": [{"Key": k} for k in batch]},
                    )
                stats["objects_deleted"] = len(all_keys)
                logger.info(f"Deleted {len(all_keys)} objects from {prefix}")
        else:
            logger.info(f"No objects found in {prefix}")

    except Exception as e:
        error_msg = f"Error cleaning up {prefix}: {e}"
        logger.error(error_msg)
        stats["errors"].append(error_msg)

    return stats


def delete_all_locks(dynamo_client: DynamoClient, dry_run: bool = False) -> Dict:
    """Delete all compaction locks."""
    logger.info("Deleting all compaction locks...")
    locks, _ = dynamo_client.list_compaction_locks()

    stats = {"deleted": 0, "errors": []}

    if not locks:
        logger.info("No locks found")
        return stats

    if dry_run:
        logger.info(f"[DRY RUN] Would delete {len(locks)} locks")
    else:
        try:
            dynamo_client.delete_compaction_locks(locks)
            stats["deleted"] = len(locks)
            logger.info(f"Deleted {len(locks)} locks")
        except Exception as e:
            error_msg = f"Error deleting locks: {e}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Reset embeddings and cleanup for fresh batch submission"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode (don't make changes)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--skip-s3-cleanup",
        action="store_true",
        help="Skip S3 cleanup (snapshots and poll_results)",
    )
    parser.add_argument(
        "--skip-locks",
        action="store_true",
        help="Skip lock deletion",
    )
    parser.add_argument(
        "--keep-batches",
        action="store_true",
        help="Reset batch summaries to PENDING instead of deleting them (default: delete all batch summaries)",
    )
    parser.add_argument(
        "--skip-embedding-reset",
        action="store_true",
        help="Skip resetting embedding statuses (only cleanup batch summaries, S3, and locks)",
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.warning("\n⚠️  DRY RUN MODE - No actual changes will occur")
    else:
        logger.warning("\n⚠️  LIVE MODE - This will make changes!")
        if not args.yes:
            response = input("Are you sure you want to proceed? (yes/no): ")
            if response.lower() != "yes":
                logger.info("Cancelled")
                return

    try:
        # Get table and bucket names
        table_name = get_table_name(args.stack)
        bucket_name = get_bucket_name(args.stack)
        logger.info(f"Using DynamoDB table: {table_name}")
        logger.info(f"Using S3 bucket: {bucket_name}")

        dynamo_client = DynamoClient(table_name)

        # Step 1: List all batch summaries and extract receipt refs
        logger.info("\n" + "=" * 80)
        logger.info("STEP 1: Analyzing batch summaries")
        logger.info("=" * 80)
        batch_summaries = list_all_batch_summaries(dynamo_client)
        receipts_in_batches = extract_receipt_refs_from_batches(batch_summaries)

        # Step 2: List all receipts from DynamoDB
        logger.info("\n" + "=" * 80)
        logger.info("STEP 2: Listing all receipts from DynamoDB")
        logger.info("=" * 80)
        all_receipts = list_all_receipts(dynamo_client)

        # Step 3: Find receipts needing embeddings (for reporting)
        logger.info("\n" + "=" * 80)
        logger.info("STEP 3: Finding receipts needing embeddings")
        logger.info("=" * 80)
        receipts_needing_embeddings = find_receipts_needing_embeddings(
            all_receipts, receipts_in_batches
        )

        # Step 4: Reset embedding statuses (optional)
        embedding_stats = {
            "receipts_processed": 0,
            "receipts_with_lines": 0,
            "receipts_with_words": 0,
            "total_lines_updated": 0,
            "total_words_updated": 0,
            "orphaned_receipts": 0,
            "errors": [],
        }
        if not args.skip_embedding_reset:
            logger.info("\n" + "=" * 80)
            logger.info("STEP 4: Resetting embedding statuses to NONE for ALL receipts")
            logger.info("=" * 80)
            embedding_stats = reset_embedding_statuses(
                dynamo_client, all_receipts, dry_run=args.dry_run
            )
        else:
            logger.info("\n" + "=" * 80)
            logger.info("STEP 4: Skipping embedding status reset (--skip-embedding-reset)")
            logger.info("=" * 80)

        # Step 5: Delete or reset batch summaries (default: delete all)
        step_num = 5 if args.skip_embedding_reset else 5
        logger.info("\n" + "=" * 80)
        if args.keep_batches:
            logger.info(f"STEP {step_num}: Resetting all batch summaries to PENDING")
            logger.info("=" * 80)
            batch_stats = reset_batch_summaries_to_pending(
                dynamo_client, dry_run=args.dry_run
            )
        else:
            logger.info(f"STEP {step_num}: Deleting all batch summaries")
            logger.info("=" * 80)
            batch_stats = delete_all_batch_summaries(dynamo_client, dry_run=args.dry_run)

        # Step 6: Cleanup S3
        step_num_s3 = 6 if args.skip_embedding_reset else 6
        s3_stats = {"snapshots": {}, "poll_results": {}}
        if not args.skip_s3_cleanup:
            logger.info("\n" + "=" * 80)
            logger.info(f"STEP {step_num_s3}: Cleaning up S3")
            logger.info("=" * 80)
            s3_stats["snapshots"] = cleanup_s3_snapshots(
                bucket_name, dry_run=args.dry_run
            )
            s3_stats["poll_results"] = cleanup_s3_poll_results(
                bucket_name, dry_run=args.dry_run
            )

        # Step 7: Delete locks
        step_num_locks = 7 if args.skip_embedding_reset else 7
        lock_stats = {}
        if not args.skip_locks:
            logger.info("\n" + "=" * 80)
            logger.info(f"STEP {step_num_locks}: Deleting all compaction locks")
            logger.info("=" * 80)
            lock_stats = delete_all_locks(dynamo_client, dry_run=args.dry_run)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("SUMMARY")
        logger.info("=" * 80)
        if args.keep_batches:
            logger.info(
                f"Batch summaries: {len(batch_summaries)} found, {batch_stats.get('reset', 0)} reset to PENDING"
            )
        else:
            logger.info(
                f"Batch summaries: {len(batch_summaries)} found, {batch_stats.get('deleted', 0)} deleted"
            )
        logger.info(
            f"Receipts: {len(all_receipts)} total, {len(receipts_in_batches)} in batches, "
            f"{len(receipts_needing_embeddings)} needing embeddings"
        )
        if not args.skip_embedding_reset:
            logger.info(
                f"Embedding statuses reset: {embedding_stats['total_lines_updated']} lines, "
                f"{embedding_stats['total_words_updated']} words"
            )
            if embedding_stats.get("orphaned_receipts", 0) > 0:
                logger.info(
                    f"Orphaned receipts skipped: {embedding_stats['orphaned_receipts']} "
                    "(ReceiptMetadata exists but receipt data missing - expected edge case)"
                )
        else:
            logger.info("Embedding statuses: NOT reset (--skip-embedding-reset)")
        if not args.skip_s3_cleanup:
            logger.info(
                f"S3 cleanup: {s3_stats['snapshots']['objects_deleted']} snapshot objects, "
                f"{s3_stats['poll_results']['objects_deleted']} poll_result objects"
            )
        if not args.skip_locks:
            logger.info(f"Locks: {lock_stats.get('deleted', 0)} deleted")

        if args.dry_run:
            logger.info("\n" + "=" * 80)
            logger.info("DRY RUN COMPLETE - No changes were made")
            logger.info("Run without --dry-run to apply changes")
            logger.info("=" * 80)
        else:
            logger.info("\n" + "=" * 80)
            logger.info("CLEANUP COMPLETE")
            logger.info("Next step: Run the submit step functions to create new batch summaries")
            logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

