#!/usr/bin/env python3
"""
Clean up batch summaries associated with deleted images.

This script:
1. Lists all batch summaries
2. Finds batch summaries that reference deleted image IDs
3. Either deletes batch summaries (if all refs are deleted) or updates them
   (if only some refs are deleted)

Usage:
    # Dry-run
    python scripts/cleanup_batch_summaries.py --stack dev \
      --deleted-images deletion_results/deletion_results.json \
      --dry-run

    # Actually cleanup
    python scripts/cleanup_batch_summaries.py --stack dev \
      --deleted-images deletion_results/deletion_results.json \
      --no-dry-run --yes
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Set

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env

# Configure logging
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


def get_deleted_image_ids(deletion_results_path: str) -> Set[str]:
    """Extract deleted image IDs from deletion results."""
    with open(deletion_results_path, "r") as f:
        results = json.load(f)

    deleted_ids = set()
    for deletion in results.get("deletions", []):
        deleted_ids.add(deletion["image_id"])

    return deleted_ids


def cleanup_batch_summaries(
    dynamo_client: DynamoClient,
    deleted_image_ids: Set[str],
    dry_run: bool = True,
) -> Dict:
    """Clean up batch summaries associated with deleted images."""
    results = {
        "dry_run": dry_run,
        "total_batch_summaries": 0,
        "affected_batch_summaries": 0,
        "deleted": 0,
        "updated": 0,
        "errors": [],
        "actions": [],
    }

    # List all batch summaries
    logger.info("Listing all batch summaries...")
    all_batch_summaries = []
    last_evaluated_key = None

    while True:
        batch_summaries, last_evaluated_key = dynamo_client.list_batch_summaries(
            limit=100, last_evaluated_key=last_evaluated_key
        )
        all_batch_summaries.extend(batch_summaries)
        results["total_batch_summaries"] += len(batch_summaries)

        if not last_evaluated_key:
            break

    logger.info(f"Found {len(all_batch_summaries)} total batch summaries")

    # Check each batch summary
    for batch_summary in all_batch_summaries:
        if not batch_summary.receipt_refs:
            continue

        # Check which receipt_refs reference deleted images
        deleted_refs = [
            (image_id, receipt_id)
            for image_id, receipt_id in batch_summary.receipt_refs
            if image_id in deleted_image_ids
        ]
        remaining_refs = [
            (image_id, receipt_id)
            for image_id, receipt_id in batch_summary.receipt_refs
            if image_id not in deleted_image_ids
        ]

        if not deleted_refs:
            # No deleted images referenced, skip
            continue

        results["affected_batch_summaries"] += 1

        if not remaining_refs:
            # All refs are for deleted images - delete the batch summary
            action = {
                "batch_id": batch_summary.batch_id,
                "action": "delete",
                "reason": "All receipt_refs reference deleted images",
                "deleted_refs": len(deleted_refs),
                "remaining_refs": 0,
            }

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would delete batch summary {batch_summary.batch_id} "
                    f"({len(deleted_refs)} refs to deleted images)"
                )
            else:
                try:
                    dynamo_client.delete_batch_summary(batch_summary)
                    logger.info(
                        f"✅ Deleted batch summary {batch_summary.batch_id} "
                        f"({len(deleted_refs)} refs to deleted images)"
                    )
                    results["deleted"] += 1
                except Exception as e:
                    error_msg = f"Failed to delete batch summary {batch_summary.batch_id}: {e}"
                    logger.error(error_msg, exc_info=True)
                    results["errors"].append(error_msg)
                    action["error"] = error_msg

            results["actions"].append(action)

        else:
            # Some refs remain - update to remove deleted refs
            action = {
                "batch_id": batch_summary.batch_id,
                "action": "update",
                "reason": "Removing deleted image refs, keeping remaining",
                "deleted_refs": len(deleted_refs),
                "remaining_refs": len(remaining_refs),
            }

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would update batch summary {batch_summary.batch_id}: "
                    f"remove {len(deleted_refs)} deleted refs, keep {len(remaining_refs)} refs"
                )
            else:
                try:
                    # Update batch summary with remaining refs
                    batch_summary.receipt_refs = remaining_refs
                    dynamo_client.update_batch_summary(batch_summary)
                    logger.info(
                        f"✅ Updated batch summary {batch_summary.batch_id}: "
                        f"removed {len(deleted_refs)} deleted refs, kept {len(remaining_refs)} refs"
                    )
                    results["updated"] += 1
                except Exception as e:
                    error_msg = f"Failed to update batch summary {batch_summary.batch_id}: {e}"
                    logger.error(error_msg, exc_info=True)
                    results["errors"].append(error_msg)
                    action["error"] = error_msg

            results["actions"].append(action)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Clean up batch summaries associated with deleted images"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--deleted-images",
        default="deletion_results/deletion_results.json",
        help="Path to deletion_results.json",
    )
    parser.add_argument(
        "--output-dir",
        default="batch_cleanup_results",
        help="Output directory for results",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually cleanup (overrides --dry-run)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # Get deleted image IDs
        deleted_image_ids = get_deleted_image_ids(args.deleted_images)
        logger.info(f"Found {len(deleted_image_ids)} deleted image IDs")

        if args.dry_run:
            logger.warning("\n⚠️  DRY RUN MODE - No actual changes will occur")
        else:
            logger.warning(
                "\n⚠️  LIVE MODE - This will DELETE/UPDATE batch summaries!"
            )
            if not args.yes:
                response = input("Are you sure you want to proceed? (yes/no): ")
                if response.lower() != "yes":
                    logger.info("Cancelled")
                    return
            else:
                logger.info("Proceeding with --yes flag (skipping confirmation)")

        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        # Cleanup batch summaries
        logger.info("\nCleaning up batch summaries...")
        results = cleanup_batch_summaries(
            client, deleted_image_ids, dry_run=args.dry_run
        )

        # Save results
        output_path = Path(args.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        results_path = output_path / "batch_cleanup_results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("BATCH SUMMARY CLEANUP SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total batch summaries: {results['total_batch_summaries']}")
        logger.info(
            f"Affected batch summaries: {results['affected_batch_summaries']}"
        )
        logger.info(f"Deleted: {results['deleted']}")
        logger.info(f"Updated: {results['updated']}")
        logger.info(f"Errors: {len(results['errors'])}")
        logger.info(f"Results saved to {results_path}")

        if results["errors"]:
            logger.warning(f"\n⚠️  {len(results['errors'])} errors occurred:")
            for error in results["errors"][:5]:
                logger.warning(f"  - {error}")
            if len(results["errors"]) > 5:
                logger.warning(f"  ... and {len(results['errors']) - 5} more")
        else:
            logger.info("\n✅ Batch summary cleanup completed successfully!")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


