#!/usr/bin/env python3
"""
Populate missing receipt lines and words from image data.

This script:
1. Finds receipts missing lines or words
2. Gets the image's lines, words, and letters
3. Creates receipt_lines, receipt_words, and receipt_letters from them
4. Adds them to DynamoDB

Usage:
    # Dry-run
    python scripts/populate_missing_receipt_data.py --stack dev --dry-run

    # Actually populate
    python scripts/populate_missing_receipt_data.py --stack dev --no-dry-run --yes
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_letter import ReceiptLetter

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


def is_noise_text(text: str) -> bool:
    """Simple check if text is noise (very short or mostly special chars)."""
    if not text or len(text.strip()) < 2:
        return True
    # Count alphanumeric characters
    alnum_count = sum(1 for c in text if c.isalnum())
    if alnum_count < len(text) * 0.3:  # Less than 30% alphanumeric
        return True
    return False


def create_receipt_lines_from_image_lines(
    image_lines, receipt_id: int
) -> List[ReceiptLine]:
    """Create receipt lines from image lines."""
    receipt_lines = []
    for line in image_lines:
        receipt_line = ReceiptLine(
            image_id=line.image_id,
            line_id=line.line_id,
            text=line.text,
            bounding_box=line.bounding_box,
            top_right=line.top_right,
            top_left=line.top_left,
            bottom_right=line.bottom_right,
            bottom_left=line.bottom_left,
            angle_degrees=line.angle_degrees,
            angle_radians=line.angle_radians,
            confidence=line.confidence,
            receipt_id=receipt_id,
        )
        receipt_lines.append(receipt_line)
    return receipt_lines


def create_receipt_words_from_image_words(
    image_words, receipt_id: int
) -> List[ReceiptWord]:
    """Create receipt words from image words."""
    receipt_words = []
    for word in image_words:
        receipt_word = ReceiptWord(
            image_id=word.image_id,
            line_id=word.line_id,
            word_id=word.word_id,
            text=word.text,
            bounding_box=word.bounding_box,
            top_right=word.top_right,
            top_left=word.top_left,
            bottom_right=word.bottom_right,
            bottom_left=word.bottom_left,
            angle_degrees=word.angle_degrees,
            angle_radians=word.angle_radians,
            confidence=word.confidence,
            receipt_id=receipt_id,
            extracted_data=getattr(word, "extracted_data", None),
            is_noise=is_noise_text(word.text),
        )
        receipt_words.append(receipt_word)
    return receipt_words


def create_receipt_letters_from_image_letters(
    image_letters, receipt_id: int
) -> List[ReceiptLetter]:
    """Create receipt letters from image letters."""
    receipt_letters = []
    for letter in image_letters:
        receipt_letter = ReceiptLetter(
            image_id=letter.image_id,
            line_id=letter.line_id,
            word_id=letter.word_id,
            letter_id=letter.letter_id,
            text=letter.text,
            bounding_box=letter.bounding_box,
            top_right=letter.top_right,
            top_left=letter.top_left,
            bottom_right=letter.bottom_right,
            bottom_left=letter.bottom_left,
            angle_degrees=letter.angle_degrees,
            angle_radians=letter.angle_radians,
            confidence=letter.confidence,
            receipt_id=receipt_id,
        )
        receipt_letters.append(receipt_letter)
    return receipt_letters


def populate_missing_receipt_data(
    dynamo_client: DynamoClient,
    receipts_to_fix: List[Dict],
    dry_run: bool = True,
) -> Dict:
    """Populate missing receipt lines and words from image data."""
    results = {
        "dry_run": dry_run,
        "total_receipts": len(receipts_to_fix),
        "processed": 0,
        "populated": 0,
        "errors": [],
        "actions": [],
    }

    for receipt_info in receipts_to_fix:
        image_id = receipt_info["image_id"]
        receipt_id = receipt_info["receipt_id"]

        try:
            # Get receipt details to check what's missing
            details = dynamo_client.get_receipt_details(image_id, receipt_id)

            needs_lines = len(details.lines) == 0
            needs_words = len(details.words) == 0
            needs_letters = len(details.letters) == 0

            if not needs_lines and not needs_words and not needs_letters:
                logger.debug(
                    f"Receipt ({image_id}, {receipt_id}) already has all data - skipping"
                )
                continue

            # Get image details
            image_details = dynamo_client.get_image_details(image_id)

            action = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "needs_lines": needs_lines,
                "needs_words": needs_words,
                "needs_letters": needs_letters,
                "image_lines": len(image_details.lines),
                "image_words": len(image_details.words),
                "image_letters": len(image_details.letters),
            }

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would populate receipt ({image_id}, {receipt_id}): "
                    f"{'lines' if needs_lines else ''} "
                    f"{'words' if needs_words else ''} "
                    f"{'letters' if needs_letters else ''} "
                    f"from {len(image_details.lines)} lines, "
                    f"{len(image_details.words)} words, "
                    f"{len(image_details.letters)} letters"
                )
                action["dry_run"] = True
            else:
                logger.info(
                    f"Populating receipt ({image_id}, {receipt_id})..."
                )

                lines_added = 0
                words_added = 0
                letters_added = 0

                # Create and add receipt lines
                if needs_lines and image_details.lines:
                    receipt_lines = create_receipt_lines_from_image_lines(
                        image_details.lines, receipt_id
                    )
                    dynamo_client.add_receipt_lines(receipt_lines)
                    lines_added = len(receipt_lines)
                    logger.info(f"  ✅ Added {lines_added} receipt lines")

                # Create and add receipt words
                if needs_words and image_details.words:
                    receipt_words = create_receipt_words_from_image_words(
                        image_details.words, receipt_id
                    )
                    dynamo_client.add_receipt_words(receipt_words)
                    words_added = len(receipt_words)
                    logger.info(f"  ✅ Added {words_added} receipt words")

                # Create and add receipt letters
                if needs_letters and image_details.letters:
                    receipt_letters = create_receipt_letters_from_image_letters(
                        image_details.letters, receipt_id
                    )
                    dynamo_client.add_receipt_letters(receipt_letters)
                    letters_added = len(receipt_letters)
                    logger.info(f"  ✅ Added {letters_added} receipt letters")

                action["lines_added"] = lines_added
                action["words_added"] = words_added
                action["letters_added"] = letters_added

                if lines_added > 0 or words_added > 0 or letters_added > 0:
                    results["populated"] += 1

            results["actions"].append(action)
            results["processed"] += 1

        except Exception as e:
            error_msg = (
                f"Error populating receipt ({image_id}, {receipt_id}): {e}"
            )
            logger.error(error_msg, exc_info=True)
            results["errors"].append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "error": str(e),
                }
            )

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Populate missing receipt lines and words from image data"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--verification-results",
        default="receipt_verification_results/receipt_verification_results.json",
        help="Path to receipt verification results JSON",
    )
    parser.add_argument(
        "--output-dir",
        default="populate_results",
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
        help="Actually populate (overrides --dry-run)",
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
        # Load verification results
        with open(args.verification_results, "r") as f:
            verification_results = json.load(f)

        # Get receipts that need fixing
        receipts_to_fix = (
            verification_results.get("missing_both", [])
            + verification_results.get("missing_lines", [])
            + verification_results.get("missing_words", [])
        )

        logger.info(f"Found {len(receipts_to_fix)} receipts needing data")

        if not receipts_to_fix:
            logger.info("No receipts need data - all receipts are complete!")
            return

        if args.dry_run:
            logger.warning("\n⚠️  DRY RUN MODE - No actual changes will occur")
        else:
            logger.warning(
                "\n⚠️  LIVE MODE - This will ADD receipt lines/words/letters to DynamoDB!"
            )
            if not args.yes:
                response = input(
                    f"Are you sure you want to populate {len(receipts_to_fix)} receipts? (yes/no): "
                )
                if response.lower() != "yes":
                    logger.info("Cancelled")
                    return
            else:
                logger.info(
                    f"Proceeding with --yes flag (populating {len(receipts_to_fix)} receipts)"
                )

        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        # Populate missing data
        logger.info("\nPopulating missing receipt data...")
        results = populate_missing_receipt_data(
            client, receipts_to_fix, dry_run=args.dry_run
        )

        # Save results
        output_path = Path(args.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        results_path = output_path / "populate_results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("POPULATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total receipts to fix: {results['total_receipts']}")
        logger.info(f"Receipts processed: {results['processed']}")
        logger.info(f"Receipts populated: {results['populated']}")
        logger.info(f"Errors: {len(results['errors'])}")
        logger.info(f"Results saved to {results_path}")

        if results["errors"]:
            logger.warning(f"\n⚠️  {len(results['errors'])} errors occurred:")
            for error in results["errors"][:5]:
                logger.warning(f"  - {error}")
            if len(results["errors"]) > 5:
                logger.warning(
                    f"  ... and {len(results['errors']) - 5} more"
                )
        else:
            logger.info("\n✅ All receipts populated successfully!")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


