#!/usr/bin/env python3
"""
Check if a specific image is a duplicate and provide recommendations.

Usage:
    python scripts/check_image_duplicate.py --stack dev --image-id <image_id>
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


def check_image_duplicate(dynamo_client: DynamoClient, image_id: str):
    """Check if an image is a duplicate and provide recommendations."""
    print(f"\n{'=' * 80}")
    print(f"Checking Image: {image_id}")
    print(f"{'=' * 80}\n")

    try:
        # Get image
        image = dynamo_client.get_image(image_id)
        print(f"Image SHA256: {image.sha256}")
        print(f"Added: {image.timestamp_added}")
        print(f"Dimensions: {image.width}x{image.height}")

        # Get image details
        details = dynamo_client.get_image_details(image_id)
        print(f"\nImage Data:")
        print(f"  Receipts: {len(details.receipts)}")
        print(f"  Lines: {len(details.lines)}")
        print(f"  Words: {len(details.words)}")
        print(f"  Letters: {len(details.letters)}")
        print(f"  Receipt Word Labels: {len(details.receipt_word_labels)}")

        # Check for duplicates by hash
        all_images, _ = dynamo_client.list_images()
        duplicates = [
            img
            for img in all_images
            if img.sha256 == image.sha256 and img.image_id != image_id
        ]

        if duplicates:
            print(f"\n❌ DUPLICATE DETECTED!")
            print(f"Found {len(duplicates)} other image(s) with the same hash:\n")

            for dup in duplicates:
                dup_details = dynamo_client.get_image_details(dup.image_id)
                print(f"  Duplicate: {dup.image_id}")
                print(f"    Added: {dup.timestamp_added}")
                print(f"    Receipts: {len(dup_details.receipts)}")
                print(f"    Lines: {len(dup_details.lines)}")
                print(f"    Words: {len(dup_details.words)}")
                print(f"    Labels: {len(dup_details.receipt_word_labels)}")
                print()

            # Check which has better data
            current_total = (
                len(details.lines)
                + len(details.words)
                + len(details.receipt_word_labels)
            )
            best_dup = max(
                duplicates,
                key=lambda d: len(dynamo_client.get_image_details(d.image_id).lines)
                + len(dynamo_client.get_image_details(d.image_id).words)
                + len(
                    dynamo_client.get_image_details(d.image_id).receipt_word_labels
                ),
            )
            best_dup_details = dynamo_client.get_image_details(best_dup.image_id)
            best_dup_total = (
                len(best_dup_details.lines)
                + len(best_dup_details.words)
                + len(best_dup_details.receipt_word_labels)
            )

            print(f"\n💡 Recommendations:")
            if best_dup_total > current_total:
                print(
                    f"  ✅ Duplicate {best_dup.image_id} has MORE data ({best_dup_total} vs {current_total})"
                )
                print(f"  → Consider: Delete this image and use the duplicate")
            elif current_total > best_dup_total:
                print(
                    f"  ✅ This image has MORE data ({current_total} vs {best_dup_total})"
                )
                print(f"  → Consider: Delete the duplicate(s)")
            else:
                print(f"  ⚠️  Similar data levels")
                print(f"  → Consider: Keep the oldest image, delete others")

            # Check receipt completeness
            print(f"\n📋 Receipt Completeness Check:")
            for receipt in details.receipts:
                receipt_details = dynamo_client.get_receipt_details(
                    image_id, receipt.receipt_id
                )
                has_lines = len(receipt_details.lines) > 0
                has_words = len(receipt_details.words) > 0
                status = "✅" if (has_lines and has_words) else "❌"
                print(
                    f"  {status} Receipt {receipt.receipt_id}: "
                    f"{len(receipt_details.lines)} lines, "
                    f"{len(receipt_details.words)} words"
                )

        else:
            print(f"\n✅ NOT a duplicate (unique hash)")

            # Check if missing OCR data
            missing_ocr = (
                len(details.lines) == 0
                or len(details.words) == 0
                or any(
                    len(
                        dynamo_client.get_receipt_details(
                            image_id, receipt.receipt_id
                        ).lines
                    )
                    == 0
                    for receipt in details.receipts
                )
            )

            if missing_ocr:
                print(f"\n⚠️  Missing OCR Data Detected!")
                print(f"\n💡 Recommendations:")
                print(f"  → Re-uploading could help if:")
                print(f"     - OCR processing failed initially")
                print(f"     - Image was uploaded before OCR pipeline was ready")
                print(f"  → OR manually populate from image data (we just did this)")
                print(f"     - Use populate_missing_receipt_data.py script")
            else:
                print(f"\n✅ All OCR data present")

    except Exception as e:
        logger.error(f"Error checking image: {e}", exc_info=True)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Check if an image is a duplicate"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID to check",
    )

    args = parser.parse_args()

    try:
        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        check_image_duplicate(client, args.image_id)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


