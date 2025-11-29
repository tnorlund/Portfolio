#!/usr/bin/env python3
"""
Delete specific duplicate images created by re-upload.

This deletes the new duplicate images that were created when re-uploading,
but which have incomplete or missing OCR data compared to the originals.

Usage:
    # Dry-run
    python scripts/delete_specific_duplicate_images.py --stack dev --dry-run

    # Actually delete
    python scripts/delete_specific_duplicate_images.py --stack dev --no-dry-run --yes
"""

import argparse
import logging
import os
import sys

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


def delete_image_and_all_related_data(
    dynamo_client: DynamoClient,
    image_id: str,
    dry_run: bool = True,
) -> dict:
    """Delete an image and all its related data."""
    result = {
        "image_id": image_id,
        "dry_run": dry_run,
        "deleted": {
            "receipt_word_labels": 0,
            "receipt_words": 0,
            "receipt_letters": 0,
            "receipt_lines": 0,
            "receipts": 0,
            "receipt_metadatas": 0,
            "words": 0,
            "letters": 0,
            "lines": 0,
            "ocr_jobs": 0,
            "ocr_routing_decisions": 0,
            "image": False,
        },
        "errors": [],
    }

    try:
        # Get all image details
        details = dynamo_client.get_image_details(image_id)

        if dry_run:
            result["deleted"]["receipt_word_labels"] = len(details.receipt_word_labels)
            result["deleted"]["receipt_words"] = len(details.receipt_words)
            result["deleted"]["receipt_letters"] = len(details.receipt_letters)
            result["deleted"]["receipt_lines"] = len(details.receipt_lines)
            result["deleted"]["receipts"] = len(details.receipts)
            result["deleted"]["receipt_metadatas"] = len(details.receipt_metadatas)
            result["deleted"]["words"] = len(details.words)
            result["deleted"]["letters"] = len(details.letters)
            result["deleted"]["lines"] = len(details.lines)
            result["deleted"]["ocr_jobs"] = len(details.ocr_jobs)
            result["deleted"]["ocr_routing_decisions"] = len(
                details.ocr_routing_decisions
            )
            result["deleted"]["image"] = True
            logger.info(
                f"[DRY RUN] Would delete {image_id} and all related data: "
                f"{result['deleted']['receipt_word_labels']} labels, "
                f"{result['deleted']['receipts']} receipts, "
                f"{result['deleted']['lines']} lines, "
                f"{result['deleted']['words']} words"
            )
        else:
            # Delete in order (child entities first, then parent)
            logger.info(f"Deleting {image_id} and all related data...")

            # 1. Delete receipt word labels
            if details.receipt_word_labels:
                try:
                    dynamo_client.delete_receipt_word_labels(
                        details.receipt_word_labels
                    )
                    result["deleted"]["receipt_word_labels"] = len(
                        details.receipt_word_labels
                    )
                    logger.info(
                        f"  ✅ Deleted {len(details.receipt_word_labels)} receipt word labels"
                    )
                except Exception as e:
                    result["errors"].append(f"Failed to delete receipt word labels: {e}")

            # 2. Delete receipt words
            if details.receipt_words:
                try:
                    dynamo_client.delete_receipt_words(details.receipt_words)
                    result["deleted"]["receipt_words"] = len(details.receipt_words)
                    logger.info(f"  ✅ Deleted {len(details.receipt_words)} receipt words")
                except Exception as e:
                    result["errors"].append(f"Failed to delete receipt words: {e}")

            # 3. Delete receipt letters
            if details.receipt_letters:
                try:
                    dynamo_client.delete_receipt_letters(details.receipt_letters)
                    result["deleted"]["receipt_letters"] = len(
                        details.receipt_letters
                    )
                    logger.info(
                        f"  ✅ Deleted {len(details.receipt_letters)} receipt letters"
                    )
                except Exception as e:
                    result["errors"].append(f"Failed to delete receipt letters: {e}")

            # 4. Delete receipt lines
            if details.receipt_lines:
                try:
                    dynamo_client.delete_receipt_lines(details.receipt_lines)
                    result["deleted"]["receipt_lines"] = len(details.receipt_lines)
                    logger.info(
                        f"  ✅ Deleted {len(details.receipt_lines)} receipt lines"
                    )
                except Exception as e:
                    result["errors"].append(f"Failed to delete receipt lines: {e}")

            # 5. Delete receipts
            if details.receipts:
                try:
                    dynamo_client.delete_receipts(details.receipts)
                    result["deleted"]["receipts"] = len(details.receipts)
                    logger.info(f"  ✅ Deleted {len(details.receipts)} receipts")
                except Exception as e:
                    result["errors"].append(f"Failed to delete receipts: {e}")

            # 6. Delete receipt metadatas
            if details.receipt_metadatas:
                try:
                    dynamo_client.delete_receipt_metadatas(details.receipt_metadatas)
                    result["deleted"]["receipt_metadatas"] = len(
                        details.receipt_metadatas
                    )
                    logger.info(
                        f"  ✅ Deleted {len(details.receipt_metadatas)} receipt metadatas"
                    )
                except Exception as e:
                    result["errors"].append(
                        f"Failed to delete receipt metadatas: {e}"
                    )

            # 7. Delete words
            if details.words:
                try:
                    dynamo_client.delete_words(details.words)
                    result["deleted"]["words"] = len(details.words)
                    logger.info(f"  ✅ Deleted {len(details.words)} words")
                except Exception as e:
                    result["errors"].append(f"Failed to delete words: {e}")

            # 8. Delete letters
            if details.letters:
                try:
                    dynamo_client.delete_letters(details.letters)
                    result["deleted"]["letters"] = len(details.letters)
                    logger.info(f"  ✅ Deleted {len(details.letters)} letters")
                except Exception as e:
                    result["errors"].append(f"Failed to delete letters: {e}")

            # 9. Delete lines
            if details.lines:
                try:
                    dynamo_client.delete_lines(details.lines)
                    result["deleted"]["lines"] = len(details.lines)
                    logger.info(f"  ✅ Deleted {len(details.lines)} lines")
                except Exception as e:
                    result["errors"].append(f"Failed to delete lines: {e}")

            # 10. Delete OCR jobs
            if details.ocr_jobs:
                try:
                    dynamo_client.delete_ocr_jobs(details.ocr_jobs)
                    result["deleted"]["ocr_jobs"] = len(details.ocr_jobs)
                    logger.info(f"  ✅ Deleted {len(details.ocr_jobs)} OCR jobs")
                except Exception as e:
                    result["errors"].append(f"Failed to delete OCR jobs: {e}")

            # 11. Delete OCR routing decisions
            if details.ocr_routing_decisions:
                try:
                    dynamo_client.delete_ocr_routing_decisions(
                        details.ocr_routing_decisions
                    )
                    result["deleted"]["ocr_routing_decisions"] = len(
                        details.ocr_routing_decisions
                    )
                    logger.info(
                        f"  ✅ Deleted {len(details.ocr_routing_decisions)} OCR routing decisions"
                    )
                except Exception as e:
                    result["errors"].append(
                        f"Failed to delete OCR routing decisions: {e}"
                    )

            # 12. Finally, delete the image itself
            try:
                dynamo_client.delete_image(image_id)
                result["deleted"]["image"] = True
                logger.info(f"  ✅ Deleted image {image_id}")
            except Exception as e:
                result["errors"].append(f"Failed to delete image: {e}")

    except Exception as e:
        error_msg = f"Error processing {image_id}: {e}"
        logger.error(error_msg, exc_info=True)
        result["errors"].append(error_msg)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Delete OLD duplicate images with incorrectly copied OCR data"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
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
        help="Actually delete (overrides --dry-run)",
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

    # Images to delete (OLD images with incorrectly copied OCR data from Nov 25)
    images_to_delete = [
        "abaec508-6730-4d75-9d48-76492a26a168",  # OLD - incorrectly copied data
        "cb22100f-44c2-4b7d-b29f-46627a64355a",  # OLD - incorrectly copied data
    ]

    try:
        if args.dry_run:
            logger.warning("\n⚠️  DRY RUN MODE - No actual deletions will occur")
        else:
            logger.warning(
                "\n⚠️  LIVE MODE - This will PERMANENTLY DELETE images and all related data!"
            )
            if not args.yes:
                response = input(
                    f"Are you sure you want to delete {len(images_to_delete)} duplicate images? (yes/no): "
                )
                if response.lower() != "yes":
                    logger.info("Cancelled")
                    return
            else:
                logger.info(
                    f"Proceeding with --yes flag (deleting {len(images_to_delete)} images)"
                )

        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        # Delete each duplicate image
        results = {
            "dry_run": args.dry_run,
            "total_images": len(images_to_delete),
            "processed": 0,
            "deleted": 0,
            "errors": 0,
            "deletions": [],
        }

        for i, image_id in enumerate(images_to_delete, 1):
            logger.info(
                f"\n{'=' * 80}\nProcessing {i}/{len(images_to_delete)}: {image_id}\n{'=' * 80}"
            )
            result = delete_image_and_all_related_data(
                client, image_id, dry_run=args.dry_run
            )
            results["deletions"].append(result)
            results["processed"] += 1

            if result["errors"]:
                results["errors"] += 1
            else:
                results["deleted"] += 1

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("DELETION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total images processed: {results['processed']}")
        logger.info(f"Successfully deleted: {results['deleted']}")
        logger.info(f"Errors: {results['errors']}")

        if results["errors"] > 0:
            logger.warning(f"\n⚠️  {results['errors']} images had errors during deletion")
            for deletion in results["deletions"]:
                if deletion["errors"]:
                    logger.warning(f"  {deletion['image_id']}: {deletion['errors']}")
        else:
            logger.info("\n✅ All duplicate images deleted successfully!")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

