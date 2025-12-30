#!/usr/bin/env python3
"""
Sync receipt word labels from dev to prod DynamoDB.

This script:
1. Dumps all labels from dev and prod to NDJSON files (cached)
2. Compares labels between dev and prod
3. Copies missing labels from dev to prod (preserving validation_status)

Usage:
    # Dry run (default) - dump and compare only
    python scripts/sync_labels_dev_to_prod.py

    # Actually sync missing labels
    python scripts/sync_labels_dev_to_prod.py --no-dry-run

    # Force re-dump (ignore cached NDJSON files)
    python scripts/sync_labels_dev_to_prod.py --force-dump

    # Also update validation_status for existing labels that differ
    python scripts/sync_labels_dev_to_prod.py --update-status --no-dry-run
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Cache directory for NDJSON dumps
CACHE_DIR = Path(parent_dir) / "logs" / "label_cache"


def get_label_key(label: ReceiptWordLabel) -> Tuple[str, int, int, int, str]:
    """Get unique key for a label (image_id, receipt_id, line_id, word_id, label)."""
    return (
        label.image_id,
        label.receipt_id,
        label.line_id,
        label.word_id,
        label.label,
    )


def label_to_dict(label: ReceiptWordLabel) -> dict:
    """Convert label to dict for JSON serialization."""
    return dict(label)


def dict_to_label(d: dict) -> ReceiptWordLabel:
    """Convert dict back to ReceiptWordLabel."""
    return ReceiptWordLabel(**d)


def dump_all_labels(
    client: DynamoClient,
    output_path: Path,
    env_name: str,
) -> int:
    """
    Dump all labels from DynamoDB to NDJSON file.

    Returns count of labels dumped.
    """
    logger.info(f"Dumping all labels from {env_name}...")

    count = 0
    last_key = None

    with open(output_path, "w") as f:
        while True:
            batch, last_key = client.list_receipt_word_labels(
                limit=1000,
                last_evaluated_key=last_key,
            )

            for label in batch:
                f.write(json.dumps(label_to_dict(label)) + "\n")
                count += 1

            if count % 5000 == 0 and count > 0:
                logger.info(f"  {env_name}: dumped {count} labels...")

            if not last_key:
                break

    logger.info(f"  {env_name}: dumped {count} labels to {output_path}")
    return count


def load_labels_from_ndjson(path: Path) -> List[ReceiptWordLabel]:
    """Load labels from NDJSON file."""
    labels = []
    with open(path, "r") as f:
        for line in f:
            if line.strip():
                labels.append(dict_to_label(json.loads(line)))
    return labels


def compare_labels(
    dev_labels: List[ReceiptWordLabel],
    prod_labels: List[ReceiptWordLabel],
) -> Dict[str, List]:
    """
    Compare labels between dev and prod.

    Returns dict with:
        - missing_in_prod: labels in dev but not prod
        - status_differs: (dev_label, prod_label) tuples where validation_status differs
        - same: labels that match exactly
    """
    logger.info("Comparing labels...")

    dev_by_key = {get_label_key(l): l for l in dev_labels}
    prod_by_key = {get_label_key(l): l for l in prod_labels}

    dev_keys = set(dev_by_key.keys())
    prod_keys = set(prod_by_key.keys())

    missing_in_prod = [dev_by_key[k] for k in dev_keys - prod_keys]
    only_in_prod = prod_keys - dev_keys

    status_differs = []
    same = []

    for key in dev_keys & prod_keys:
        dev_label = dev_by_key[key]
        prod_label = prod_by_key[key]

        if dev_label.validation_status != prod_label.validation_status:
            status_differs.append((dev_label, prod_label))
        else:
            same.append(dev_label)

    logger.info(f"  Missing in prod: {len(missing_in_prod)}")
    logger.info(f"  Only in prod: {len(only_in_prod)}")
    logger.info(f"  Status differs: {len(status_differs)}")
    logger.info(f"  Same: {len(same)}")

    return {
        "missing_in_prod": missing_in_prod,
        "only_in_prod": list(only_in_prod),
        "status_differs": status_differs,
        "same": same,
    }


def sync_labels(
    prod_client: DynamoClient,
    missing_labels: List[ReceiptWordLabel],
    status_differs: List[Tuple[ReceiptWordLabel, ReceiptWordLabel]],
    dry_run: bool = True,
    update_status: bool = False,
) -> Dict[str, int]:
    """
    Sync labels to prod.

    Returns statistics about the sync.
    """
    stats = {
        "copied": 0,
        "status_updated": 0,
        "errors": 0,
    }

    # Copy missing labels in batches
    if missing_labels:
        if dry_run:
            logger.info(f"[DRY RUN] Would copy {len(missing_labels)} missing labels")
            stats["copied"] = len(missing_labels)
        else:
            logger.info(f"Copying {len(missing_labels)} missing labels...")
            batch_size = 25
            for i in range(0, len(missing_labels), batch_size):
                batch = missing_labels[i:i + batch_size]
                try:
                    prod_client.add_receipt_word_labels(batch)
                    stats["copied"] += len(batch)
                except Exception as e:
                    logger.error(f"Error copying batch: {e}")
                    stats["errors"] += len(batch)

                if (i + batch_size) % 500 == 0:
                    logger.info(f"  Copied {stats['copied']} labels...")

    # Update validation_status for differing labels
    if status_differs and update_status:
        if dry_run:
            logger.info(f"[DRY RUN] Would update status for {len(status_differs)} labels")
            stats["status_updated"] = len(status_differs)
        else:
            logger.info(f"Updating status for {len(status_differs)} labels...")
            for dev_label, prod_label in status_differs:
                try:
                    # Create updated label with dev's validation_status
                    updated = ReceiptWordLabel(
                        image_id=prod_label.image_id,
                        receipt_id=prod_label.receipt_id,
                        line_id=prod_label.line_id,
                        word_id=prod_label.word_id,
                        label=prod_label.label,
                        reasoning=prod_label.reasoning,
                        timestamp_added=prod_label.timestamp_added,
                        validation_status=dev_label.validation_status,
                        label_proposed_by=prod_label.label_proposed_by,
                        label_consolidated_from=prod_label.label_consolidated_from,
                    )
                    prod_client.add_receipt_word_labels([updated])
                    stats["status_updated"] += 1
                except Exception as e:
                    logger.error(f"Error updating label: {e}")
                    stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Sync receipt word labels from dev to prod"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode - don't actually write to prod (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually sync labels to prod",
    )
    parser.add_argument(
        "--update-status",
        action="store_true",
        default=False,
        help="Also update validation_status for labels that exist in both but differ",
    )
    parser.add_argument(
        "--force-dump",
        action="store_true",
        default=False,
        help="Force re-dump from DynamoDB (ignore cached NDJSON files)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY RUN" if args.dry_run else "LIVE SYNC"
    logger.info(f"Mode: {mode}")
    if args.dry_run:
        logger.info("No changes will be made. Use --no-dry-run to actually sync.")
    if args.update_status:
        logger.info("Will also update validation_status for differing labels")

    try:
        # Create cache directory
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        dev_cache = CACHE_DIR / "dev_labels.ndjson"
        prod_cache = CACHE_DIR / "prod_labels.ndjson"

        # Get configurations from Pulumi
        logger.info("Loading configurations from Pulumi...")
        dev_config = load_env(env="dev")
        prod_config = load_env(env="prod")

        dev_table = dev_config["dynamodb_table_name"]
        prod_table = prod_config["dynamodb_table_name"]
        logger.info(f"Dev table: {dev_table}")
        logger.info(f"Prod table: {prod_table}")

        # Create clients
        dev_client = DynamoClient(dev_table)
        prod_client = DynamoClient(prod_table)

        # Dump labels (or use cache)
        if args.force_dump or not dev_cache.exists():
            dump_all_labels(dev_client, dev_cache, "dev")
        else:
            logger.info(f"Using cached dev labels from {dev_cache}")

        if args.force_dump or not prod_cache.exists():
            dump_all_labels(prod_client, prod_cache, "prod")
        else:
            logger.info(f"Using cached prod labels from {prod_cache}")

        # Load labels from cache
        logger.info("Loading labels from cache...")
        dev_labels = load_labels_from_ndjson(dev_cache)
        prod_labels = load_labels_from_ndjson(prod_cache)
        logger.info(f"  Dev: {len(dev_labels)} labels")
        logger.info(f"  Prod: {len(prod_labels)} labels")

        # Compare
        comparison = compare_labels(dev_labels, prod_labels)

        # Sync
        stats = sync_labels(
            prod_client=prod_client,
            missing_labels=comparison["missing_in_prod"],
            status_differs=comparison["status_differs"],
            dry_run=args.dry_run,
            update_status=args.update_status,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("SYNC SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Dev labels: {len(dev_labels)}")
        logger.info(f"Prod labels: {len(prod_labels)}")
        logger.info(f"Missing in prod: {len(comparison['missing_in_prod'])}")
        logger.info(f"Only in prod: {len(comparison['only_in_prod'])}")
        logger.info(f"Status differs: {len(comparison['status_differs'])}")
        logger.info(f"Already in sync: {len(comparison['same'])}")
        logger.info("")
        logger.info(f"Labels {'to copy' if args.dry_run else 'copied'}: {stats['copied']}")
        if args.update_status:
            logger.info(
                f"Status {'to update' if args.dry_run else 'updated'}: {stats['status_updated']}"
            )
        logger.info(f"Errors: {stats['errors']}")

        if args.dry_run:
            logger.info("\n[DRY RUN] No changes made. Use --no-dry-run to sync.")
        else:
            # Invalidate prod cache after sync
            if prod_cache.exists():
                prod_cache.unlink()
                logger.info("Invalidated prod cache")

            if stats["errors"] > 0:
                logger.error("\nSync completed with errors")
                sys.exit(1)
            else:
                logger.info("\nSync completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
