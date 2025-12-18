#!/usr/bin/env python3
"""
Copy DynamoDB records from dev to prod, updating bucket names and resetting embedding status.

This script:
1. Reads exported JSON files from dev.export directory
2. Checks if each image already exists in prod
3. Updates bucket names (raw_s3_bucket, cdn_s3_bucket) for Image and Receipt entities
4. Resets embedding_status to NONE for ReceiptLine and ReceiptWord entities
5. Copies all related entities (lines, words, labels, metadata, OCR jobs, etc.)
6. Uses batch writes for efficiency

Usage:
    # Dry run first
    python scripts/copy_dynamodb_dev_to_prod.py --dry-run

    # Actually copy
    python scripts/copy_dynamodb_dev_to_prod.py --no-dry-run
"""

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.image import Image
from receipt_dynamo.entities.letter import Letter
from receipt_dynamo.entities.line import Line
from receipt_dynamo.entities.ocr_job import OCRJob
from receipt_dynamo.entities.ocr_routing_decision import OCRRoutingDecision
from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import EmbeddingStatus, ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.word import Word

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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


def convert_datetime_strings(obj: Any) -> Any:
    """Recursively convert ISO datetime strings back to datetime objects."""
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if isinstance(value, str) and (
                value.endswith("Z") or "T" in value and len(value) > 10
            ):
                try:
                    result[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
        # Convert datetime strings to datetime objects
        return convert_datetime_strings(data)
    except Exception as e:
        logger.error(f"Failed to load {export_path}: {e}")
        raise


def update_bucket_names(
    item: Dict[str, Any],
    dev_raw_bucket: str,
    prod_raw_bucket: str,
    dev_cdn_bucket: str,
    prod_cdn_bucket: str,
) -> Dict[str, Any]:
    """Update bucket names in an entity dict."""
    updated = item.copy()

    # Update raw_s3_bucket
    if "raw_s3_bucket" in updated and updated["raw_s3_bucket"] == dev_raw_bucket:
        updated["raw_s3_bucket"] = prod_raw_bucket

    # Update cdn_s3_bucket
    if "cdn_s3_bucket" in updated and updated["cdn_s3_bucket"] == dev_cdn_bucket:
        updated["cdn_s3_bucket"] = prod_cdn_bucket

    return updated


def reset_embedding_status(item: Dict[str, Any]) -> Dict[str, Any]:
    """Reset embedding_status to NONE for ReceiptLine and ReceiptWord entities."""
    updated = item.copy()
    if "embedding_status" in updated:
        updated["embedding_status"] = EmbeddingStatus.NONE.value
    return updated


def image_exists_in_prod(prod_client: DynamoClient, image_id: str) -> bool:
    """Check if an image already exists in prod."""
    try:
        details = prod_client.get_image_details(image_id)
        return len(details.images) > 0
    except Exception:
        return False


def copy_image_entities(
    export_data: Dict[str, Any],
    prod_client: DynamoClient,
    dev_raw_bucket: str,
    prod_raw_bucket: str,
    dev_cdn_bucket: str,
    prod_cdn_bucket: str,
    dry_run: bool = True,
) -> Dict[str, int]:
    """
    Copy all entities for an image from export data to prod.

    Returns statistics about the copy operation.
    """
    stats = {
        "images": 0,
        "receipts": 0,
        "lines": 0,
        "words": 0,
        "letters": 0,
        "receipt_lines": 0,
        "receipt_words": 0,
        "receipt_letters": 0,
        "receipt_word_labels": 0,
        "receipt_metadatas": 0,
        "ocr_jobs": 0,
        "ocr_routing_decisions": 0,
        "errors": [],
    }

    try:
        # Process Images (update bucket names)
        if export_data.get("images"):
            images = [
                Image(
                    **update_bucket_names(
                        img,
                        dev_raw_bucket,
                        prod_raw_bucket,
                        dev_cdn_bucket,
                        prod_cdn_bucket,
                    )
                )
                for img in export_data["images"]
            ]
            if not dry_run:
                prod_client.add_images(images)
            stats["images"] = len(images)

        # Process Receipts (update bucket names)
        if export_data.get("receipts"):
            receipts = [
                Receipt(
                    **update_bucket_names(
                        r,
                        dev_raw_bucket,
                        prod_raw_bucket,
                        dev_cdn_bucket,
                        prod_cdn_bucket,
                    )
                )
                for r in export_data["receipts"]
            ]
            if not dry_run:
                prod_client.add_receipts(receipts)
            stats["receipts"] = len(receipts)

        # Process Lines
        if export_data.get("lines"):
            lines = [Line(**line) for line in export_data["lines"]]
            if not dry_run:
                prod_client.add_lines(lines)
            stats["lines"] = len(lines)

        # Process Words
        if export_data.get("words"):
            words = [Word(**word) for word in export_data["words"]]
            if not dry_run:
                prod_client.add_words(words)
            stats["words"] = len(words)

        # Process Letters
        if export_data.get("letters"):
            letters = [Letter(**letter) for letter in export_data["letters"]]
            if not dry_run:
                prod_client.add_letters(letters)
            stats["letters"] = len(letters)

        # Process ReceiptLines (reset embedding_status)
        if export_data.get("receipt_lines"):
            receipt_lines = [
                ReceiptLine(**reset_embedding_status(line))
                for line in export_data["receipt_lines"]
            ]
            if not dry_run:
                prod_client.add_receipt_lines(receipt_lines)
            stats["receipt_lines"] = len(receipt_lines)

        # Process ReceiptWords (reset embedding_status)
        if export_data.get("receipt_words"):
            receipt_words = [
                ReceiptWord(**reset_embedding_status(word))
                for word in export_data["receipt_words"]
            ]
            if not dry_run:
                prod_client.add_receipt_words(receipt_words)
            stats["receipt_words"] = len(receipt_words)

        # Process ReceiptLetters
        if export_data.get("receipt_letters"):
            receipt_letters = [
                ReceiptLetter(**letter) for letter in export_data["receipt_letters"]
            ]
            if not dry_run:
                prod_client.add_receipt_letters(receipt_letters)
            stats["receipt_letters"] = len(receipt_letters)

        # Process ReceiptWordLabels (batch into chunks of 25 to avoid DynamoDB limits)
        if export_data.get("receipt_word_labels"):
            receipt_word_labels = [
                ReceiptWordLabel(**label)
                for label in export_data["receipt_word_labels"]
            ]
            if not dry_run:
                # Split into batches of 25 (DynamoDB batch_write_item limit)
                batch_size = 25
                for i in range(0, len(receipt_word_labels), batch_size):
                    batch = receipt_word_labels[i : i + batch_size]
                    prod_client.add_receipt_word_labels(batch)
            stats["receipt_word_labels"] = len(receipt_word_labels)

        # Process ReceiptMetadatas
        if export_data.get("receipt_metadatas"):
            receipt_metadatas = [
                ReceiptMetadata(**metadata)
                for metadata in export_data["receipt_metadatas"]
            ]
            if not dry_run:
                prod_client.add_receipt_metadatas(receipt_metadatas)
            stats["receipt_metadatas"] = len(receipt_metadatas)

        # Process OCRJobs
        if export_data.get("ocr_jobs"):
            ocr_jobs = [OCRJob(**job) for job in export_data["ocr_jobs"]]
            if not dry_run:
                prod_client.add_ocr_jobs(ocr_jobs)
            stats["ocr_jobs"] = len(ocr_jobs)

        # Process OCRRoutingDecisions
        if export_data.get("ocr_routing_decisions"):
            ocr_routing_decisions = [
                OCRRoutingDecision(**decision)
                for decision in export_data["ocr_routing_decisions"]
            ]
            if not dry_run:
                prod_client.add_ocr_routing_decisions(ocr_routing_decisions)
            stats["ocr_routing_decisions"] = len(ocr_routing_decisions)

    except Exception as e:
        error_msg = f"Failed to copy entities: {e}"
        stats["errors"].append(error_msg)
        logger.error(error_msg, exc_info=True)

    return stats


def copy_all_images(
    export_dir: Path,
    prod_client: DynamoClient,
    dev_config: Dict[str, str],
    prod_config: Dict[str, str],
    dry_run: bool = True,
    skip_existing: bool = True,
    max_workers: int = 10,
) -> Dict[str, Any]:
    """
    Copy all images from export directory to prod.

    Returns overall statistics.
    """
    # Get all export files
    export_files = list(export_dir.glob("*.json"))
    logger.info(f"Found {len(export_files)} export files")

    overall_stats = {
        "total_images": len(export_files),
        "copied": 0,
        "skipped": 0,
        "failed": 0,
        "entity_stats": {},
        "errors": [],
    }

    def process_one_file(
        export_file: Path,
    ) -> tuple[str, Dict[str, Any], Optional[str]]:
        """Process a single export file. Returns (image_id, stats, error)."""
        try:
            # Load export data
            export_data = load_export_file(export_file)

            # Get image_id from first image
            images = export_data.get("images", [])
            if not images:
                return export_file.stem, {}, "No images in export file"

            image_id = images[0].get("image_id") or export_file.stem

            # Check if image already exists
            if skip_existing and image_exists_in_prod(prod_client, image_id):
                return image_id, {"skipped": True}, None

            # Copy entities
            stats = copy_image_entities(
                export_data,
                prod_client,
                dev_config["raw_bucket"],
                prod_config["raw_bucket"],
                dev_config["cdn_bucket"],
                prod_config["cdn_bucket"],
                dry_run=dry_run,
            )

            return image_id, stats, None

        except Exception as e:
            error_msg = f"Failed to process {export_file}: {e}"
            logger.error(error_msg, exc_info=True)
            return export_file.stem, {}, error_msg

    # Process files in parallel
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(process_one_file, f): f for f in export_files}

        for future in as_completed(future_to_file):
            completed += 1
            image_id, stats, error = future.result()

            if error:
                overall_stats["failed"] += 1
                overall_stats["errors"].append(error)
            elif stats.get("skipped"):
                overall_stats["skipped"] += 1
            else:
                overall_stats["copied"] += 1
                # Merge entity stats
                for key, value in stats.items():
                    if key not in ["errors", "written", "failed"]:
                        overall_stats["entity_stats"][key] = (
                            overall_stats["entity_stats"].get(key, 0) + value
                        )

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
        description="Copy DynamoDB records from dev to prod"
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default="dev.export",
        help="Directory containing exported JSON files (default: dev.export)",
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
        help="Actually copy records (disables dry-run)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip images that already exist in prod (default: True)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Number of parallel workers (default: 10)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY RUN" if args.dry_run else "LIVE COPY"
    logger.info(f"Mode: {mode}")
    if args.dry_run:
        logger.info("No records will be copied. Use --no-dry-run to actually copy.")

    try:
        # Get configurations
        dev_config = get_table_and_bucket_names("dev")
        prod_config = get_table_and_bucket_names("prod")

        # Initialize prod client
        prod_client = DynamoClient(prod_config["table"])

        # Check export directory
        export_dir = Path(args.export_dir)
        if not export_dir.exists():
            logger.error(f"Export directory does not exist: {export_dir}")
            sys.exit(1)

        # Copy all images
        logger.info(f"Starting copy from {export_dir}...")
        stats = copy_all_images(
            export_dir,
            prod_client,
            dev_config,
            prod_config,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
            max_workers=args.max_workers,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("COPY SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total images in export: {stats['total_images']}")
        logger.info(f"{'Would copy' if args.dry_run else 'Copied'}: {stats['copied']}")
        logger.info(f"Skipped (already exist): {stats['skipped']}")
        logger.info(f"Failed: {stats['failed']}")

        if stats["entity_stats"]:
            logger.info("\nEntity counts:")
            for entity_type, count in sorted(stats["entity_stats"].items()):
                logger.info(f"  {entity_type}: {count}")

        if stats["errors"]:
            logger.warning(f"\n{len(stats['errors'])} errors occurred:")
            for error in stats["errors"][:10]:
                logger.warning(f"  - {error}")
            if len(stats["errors"]) > 10:
                logger.warning(f"  ... and {len(stats['errors']) - 10} more errors")

        if args.dry_run:
            logger.info("\n✅ Dry run completed. Use --no-dry-run to actually copy.")
        else:
            if stats["failed"] > 0:
                logger.error("\n❌ Copy completed with errors")
                sys.exit(1)
            else:
                logger.info("\n✅ Copy completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
