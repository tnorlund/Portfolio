#!/usr/bin/env python3
"""
Delete incorrectly populated receipt lines/words/letters.

This deletes the receipt OCR data that was incorrectly copied from image OCR
before re-uploading the images for proper processing.

Usage:
    # Dry-run
    python scripts/delete_incorrect_receipt_data.py --stack dev --dry-run

    # Actually delete
    python scripts/delete_incorrect_receipt_data.py --stack dev --no-dry-run --yes
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


def main():
    parser = argparse.ArgumentParser(
        description="Delete incorrectly populated receipt OCR data"
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

    # Receipts with incorrectly populated data
    receipts_to_clean = [
        ("abaec508-6730-4d75-9d48-76492a26a168", 1),
        ("cb22100f-44c2-4b7d-b29f-46627a64355a", 3),
    ]

    try:
        if args.dry_run:
            logger.warning("\n⚠️  DRY RUN MODE - No actual deletions will occur")
        else:
            logger.warning(
                "\n⚠️  LIVE MODE - This will DELETE receipt lines/words/letters!"
            )
            if not args.yes:
                response = input(
                    f"Are you sure you want to delete OCR data for {len(receipts_to_clean)} receipts? (yes/no): "
                )
                if response.lower() != "yes":
                    logger.info("Cancelled")
                    return
            else:
                logger.info(
                    f"Proceeding with --yes flag (deleting OCR data for {len(receipts_to_clean)} receipts)"
                )

        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        total_deleted = {"lines": 0, "words": 0, "letters": 0}
        errors = []

        for image_id, receipt_id in receipts_to_clean:
            logger.info(
                f"\n{'=' * 80}\nProcessing: Image {image_id}, Receipt {receipt_id}\n{'=' * 80}"
            )

            try:
                # Get receipt details
                details = client.get_receipt_details(image_id, receipt_id)

                if args.dry_run:
                    logger.info(
                        f"[DRY RUN] Would delete: "
                        f"{len(details.lines)} lines, "
                        f"{len(details.words)} words, "
                        f"{len(details.letters)} letters"
                    )
                else:
                    # Delete receipt letters
                    if details.letters:
                        client.delete_receipt_letters(details.letters)
                        total_deleted["letters"] += len(details.letters)
                        logger.info(
                            f"  ✅ Deleted {len(details.letters)} receipt letters"
                        )

                    # Delete receipt words
                    if details.words:
                        client.delete_receipt_words(details.words)
                        total_deleted["words"] += len(details.words)
                        logger.info(
                            f"  ✅ Deleted {len(details.words)} receipt words"
                        )

                    # Delete receipt lines
                    if details.lines:
                        client.delete_receipt_lines(details.lines)
                        total_deleted["lines"] += len(details.lines)
                        logger.info(
                            f"  ✅ Deleted {len(details.lines)} receipt lines"
                        )

            except Exception as e:
                error_msg = f"Error deleting OCR data for ({image_id}, {receipt_id}): {e}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)

        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("DELETION SUMMARY")
        logger.info("=" * 80)
        if args.dry_run:
            logger.info("DRY RUN - No actual deletions occurred")
        else:
            logger.info(f"Receipt lines deleted: {total_deleted['lines']}")
            logger.info(f"Receipt words deleted: {total_deleted['words']}")
            logger.info(f"Receipt letters deleted: {total_deleted['letters']}")
        logger.info(f"Errors: {len(errors)}")

        if errors:
            logger.warning("\n⚠️  Errors occurred:")
            for error in errors:
                logger.warning(f"  - {error}")
        else:
            logger.info("\n✅ All incorrectly populated OCR data deleted!")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


