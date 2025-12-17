#!/usr/bin/env python3
"""
Copy missing ReceiptWordLabels from dev to prod.

This script:
1. Reads exported JSON files from dev.export directory
2. For each image, checks which ReceiptWordLabels already exist in prod
3. Copies only the missing ones

Usage:
    python scripts/copy_missing_receipt_word_labels.py --export-dir dev.export
"""

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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


def convert_datetime_strings(obj: Any) -> Any:
    """Recursively convert ISO datetime strings back to datetime objects."""
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if isinstance(value, str) and (
                value.endswith("Z") or "T" in value and len(value) > 10
            ):
                try:
                    result[key] = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    result[key] = value
            else:
                result[key] = convert_datetime_strings(value)
        return result
    elif isinstance(obj, list):
        return [convert_datetime_strings(item) for item in obj]
    else:
        return obj


def load_export_file(export_path: Path) -> Dict[str, Any]:
    """Load a single export JSON file and convert datetime strings."""
    try:
        with open(export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return convert_datetime_strings(data)
    except Exception as e:
        logger.error(f"Failed to load {export_path}: {e}")
        raise


def get_existing_label_keys(
    prod_client: DynamoClient, image_id: str
) -> Set[Tuple[int, int, int, int, str]]:
    """Get set of existing ReceiptWordLabel keys in prod for an image."""
    try:
        labels, _ = prod_client.list_receipt_word_labels_for_image(image_id)
        return {
            (label.receipt_id, label.line_id, label.word_id, label.label)
            for label in labels
        }
    except Exception as e:
        logger.debug(f"Error getting existing labels for {image_id}: {e}")
        return set()


def copy_missing_labels_for_image(
    export_data: Dict[str, Any],
    prod_client: DynamoClient,
) -> Dict[str, int]:
    """Copy missing ReceiptWordLabels for a single image."""
    stats = {
        "total": 0,
        "copied": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    try:
        # Get image_id
        images = export_data.get("images", [])
        if not images:
            return stats

        image_id = images[0].get("image_id")
        if not image_id:
            return stats

        # Get existing labels in prod
        existing_keys = get_existing_label_keys(prod_client, image_id)

        # Process ReceiptWordLabels
        receipt_word_labels_data = export_data.get("receipt_word_labels", [])
        stats["total"] = len(receipt_word_labels_data)

        if not receipt_word_labels_data:
            return stats

        # Filter to only missing labels
        missing_labels = []
        for label_data in receipt_word_labels_data:
            key = (
                label_data.get("receipt_id"),
                label_data.get("line_id"),
                label_data.get("word_id"),
                label_data.get("label"),
            )
            if key not in existing_keys:
                missing_labels.append(ReceiptWordLabel(**label_data))
            else:
                stats["skipped"] += 1

        if not missing_labels:
            return stats

        # Copy missing labels in batches of 25
        batch_size = 25
        for i in range(0, len(missing_labels), batch_size):
            batch = missing_labels[i : i + batch_size]
            try:
                prod_client.add_receipt_word_labels(batch)
                stats["copied"] += len(batch)
            except Exception as e:
                error_msg = f"Failed to copy batch: {e}"
                stats["errors"].append(error_msg)
                stats["failed"] += len(batch)
                logger.error(error_msg, exc_info=True)

    except Exception as e:
        error_msg = f"Failed to process image: {e}"
        stats["errors"].append(error_msg)
        logger.error(error_msg, exc_info=True)

    return stats


def copy_all_missing_labels(
    export_dir: Path,
    prod_client: DynamoClient,
    max_workers: int = 5,
) -> Dict[str, Any]:
    """Copy all missing ReceiptWordLabels from export directory to prod."""
    export_files = list(export_dir.glob("*.json"))
    logger.info(f"Found {len(export_files)} export files")

    overall_stats = {
        "total_images": len(export_files),
        "total_labels": 0,
        "copied": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    def process_one_file(
        export_file: Path,
    ) -> tuple[str, Dict[str, int], Optional[str]]:
        """Process a single export file. Returns (image_id, stats, error)."""
        try:
            export_data = load_export_file(export_file)
            images = export_data.get("images", [])
            if not images:
                return export_file.stem, {}, "No images in export file"

            image_id = images[0].get("image_id") or export_file.stem
            stats = copy_missing_labels_for_image(export_data, prod_client)
            return image_id, stats, None

        except Exception as e:
            error_msg = f"Failed to process {export_file}: {e}"
            logger.error(error_msg, exc_info=True)
            return export_file.stem, {}, error_msg

    # Process files in parallel
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(process_one_file, f): f for f in export_files
        }

        for future in as_completed(future_to_file):
            completed += 1
            image_id, stats, error = future.result()

            if error:
                overall_stats["failed"] += stats.get("failed", 0)
                overall_stats["errors"].append(error)
            else:
                overall_stats["total_labels"] += stats.get("total", 0)
                overall_stats["copied"] += stats.get("copied", 0)
                overall_stats["skipped"] += stats.get("skipped", 0)
                overall_stats["failed"] += stats.get("failed", 0)
                overall_stats["errors"].extend(stats.get("errors", []))

            if completed % 10 == 0:
                logger.info(
                    f"Progress: {completed}/{len(export_files)} "
                    f"(copied: {overall_stats['copied']}, "
                    f"skipped: {overall_stats['skipped']}, "
                    f"failed: {overall_stats['failed']})"
                )

    return overall_stats


def main():
    parser = argparse.ArgumentParser(
        description="Copy missing ReceiptWordLabels from dev to prod"
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default="dev.export",
        help="Directory containing exported JSON files (default: dev.export)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Number of parallel workers (default: 5)",
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
        # Get prod configuration
        prod_config = get_table_and_bucket_names("prod")
        prod_client = DynamoClient(prod_config["table"])

        # Check export directory
        export_dir = Path(args.export_dir)
        if not export_dir.exists():
            logger.error(f"Export directory does not exist: {export_dir}")
            sys.exit(1)

        # Copy missing labels
        logger.info(
            f"Starting copy of missing ReceiptWordLabels from {export_dir}..."
        )
        stats = copy_all_missing_labels(
            export_dir,
            prod_client,
            max_workers=args.max_workers,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("COPY SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total images processed: {stats['total_images']}")
        logger.info(f"Total labels found: {stats['total_labels']}")
        logger.info(f"Copied: {stats['copied']}")
        logger.info(f"Skipped (already exist): {stats['skipped']}")
        logger.info(f"Failed: {stats['failed']}")

        if stats["errors"]:
            logger.warning(f"\n{len(stats['errors'])} errors occurred:")
            for error in stats["errors"][:10]:
                logger.warning(f"  - {error}")
            if len(stats["errors"]) > 10:
                logger.warning(
                    f"  ... and {len(stats['errors']) - 10} more errors"
                )

        if stats["failed"] > 0:
            logger.error("\n❌ Copy completed with errors")
            sys.exit(1)
        else:
            logger.info("\n✅ Copy completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def get_table_and_bucket_names(stack: str) -> Dict[str, str]:
    """Get DynamoDB table and S3 bucket names from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")

    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")
    raw_bucket = env.get("raw_bucket_name")
    cdn_bucket = env.get("cdn_bucket_name")

    if not table_name or not raw_bucket or not cdn_bucket:
        raise ValueError(
            f"Could not find required config in Pulumi {stack} stack outputs"
        )

    logger.info(
        f"{stack.upper()}: table={table_name}, raw={raw_bucket}, cdn={cdn_bucket}"
    )
    return {
        "table": table_name,
        "raw_bucket": raw_bucket,
        "cdn_bucket": cdn_bucket,
    }


if __name__ == "__main__":
    main()
