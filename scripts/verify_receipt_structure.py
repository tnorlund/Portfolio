#!/usr/bin/env python3
"""
Verify that each receipt has receipt lines and receipt words.

This script:
1. Lists all receipts from DynamoDB
2. For each receipt, checks if it has receipt_lines and receipt_words
3. Reports which receipts are missing lines or words
4. Optionally uses exported data to help identify issues

Usage:
    # Check all receipts
    python scripts/verify_receipt_structure.py --stack dev

    # Check specific receipts from export
    python scripts/verify_receipt_structure.py --stack dev \
      --export duplicate_analysis/duplicate_analysis_report.json
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

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


def load_receipts_from_export(export_path: str) -> Set[Tuple[str, int]]:
    """Load receipt (image_id, receipt_id) pairs from export file."""
    receipts = set()
    try:
        with open(export_path, "r") as f:
            data = json.load(f)

        # Try different export formats
        if "duplicate_groups" in data:
            # duplicate_analysis_report.json format
            for group in data["duplicate_groups"]:
                for image_id, image_data in group["images"].items():
                    for receipt_id in image_data.get("receipt_ids", []):
                        receipts.add((image_id, receipt_id))
        elif "images" in data:
            # Other format
            for image_id, image_data in data["images"].items():
                for receipt_id in image_data.get("receipt_ids", []):
                    receipts.add((image_id, receipt_id))

    except Exception as e:
        logger.warning(f"Could not load receipts from export {export_path}: {e}")

    return receipts


def verify_receipt_structure(
    dynamo_client: DynamoClient,
    receipts_to_check: Set[Tuple[str, int]] | None = None,
) -> Dict:
    """Verify that each receipt has receipt lines and receipt words."""
    results = {
        "total_receipts": 0,
        "checked": 0,
        "has_lines_and_words": 0,
        "missing_lines": [],
        "missing_words": [],
        "missing_both": [],
        "errors": [],
    }

    # List all receipts
    logger.info("Listing all receipts...")
    all_receipts = []
    last_evaluated_key = None

    while True:
        receipts, last_evaluated_key = dynamo_client.list_receipts(
            limit=100, last_evaluated_key=last_evaluated_key
        )
        all_receipts.extend(receipts)
        results["total_receipts"] += len(receipts)

        if not last_evaluated_key:
            break

    logger.info(f"Found {len(all_receipts)} total receipts")

    # Filter receipts if export provided
    if receipts_to_check:
        logger.info(
            f"Filtering to {len(receipts_to_check)} receipts from export..."
        )
        all_receipts = [
            r
            for r in all_receipts
            if (r.image_id, r.receipt_id) in receipts_to_check
        ]
        logger.info(f"Found {len(all_receipts)} matching receipts")

    # Check each receipt
    for i, receipt in enumerate(all_receipts, 1):
        if i % 100 == 0:
            logger.info(f"Checking receipt {i}/{len(all_receipts)}...")

        try:
            details = dynamo_client.get_receipt_details(
                receipt.image_id, receipt.receipt_id
            )

            results["checked"] += 1

            has_lines = len(details.lines) > 0
            has_words = len(details.words) > 0

            receipt_key = (receipt.image_id, receipt.receipt_id)

            if has_lines and has_words:
                results["has_lines_and_words"] += 1
            elif not has_lines and not has_words:
                results["missing_both"].append(
                    {
                        "image_id": receipt.image_id,
                        "receipt_id": receipt.receipt_id,
                        "lines_count": 0,
                        "words_count": 0,
                    }
                )
                logger.warning(
                    f"⚠️  Receipt {receipt_key} missing both lines and words"
                )
            elif not has_lines:
                results["missing_lines"].append(
                    {
                        "image_id": receipt.image_id,
                        "receipt_id": receipt.receipt_id,
                        "lines_count": 0,
                        "words_count": len(details.words),
                    }
                )
                logger.warning(
                    f"⚠️  Receipt {receipt_key} missing lines (has {len(details.words)} words)"
                )
            elif not has_words:
                results["missing_words"].append(
                    {
                        "image_id": receipt.image_id,
                        "receipt_id": receipt.receipt_id,
                        "lines_count": len(details.lines),
                        "words_count": 0,
                    }
                )
                logger.warning(
                    f"⚠️  Receipt {receipt_key} missing words (has {len(details.lines)} lines)"
                )

        except Exception as e:
            error_msg = (
                f"Error checking receipt ({receipt.image_id}, {receipt.receipt_id}): {e}"
            )
            logger.error(error_msg, exc_info=True)
            results["errors"].append(
                {
                    "image_id": receipt.image_id,
                    "receipt_id": receipt.receipt_id,
                    "error": str(e),
                }
            )

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Verify that each receipt has receipt lines and receipt words"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--export",
        help="Path to export file to filter receipts (optional)",
    )
    parser.add_argument(
        "--output-dir",
        default="receipt_verification_results",
        help="Output directory for results",
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
        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        # Load receipts from export if provided
        receipts_to_check = None
        if args.export:
            receipts_to_check = load_receipts_from_export(args.export)
            logger.info(
                f"Loaded {len(receipts_to_check)} receipts from export"
            )

        # Verify receipt structure
        logger.info("\nVerifying receipt structure...")
        results = verify_receipt_structure(client, receipts_to_check)

        # Save results
        output_path = Path(args.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        results_path = output_path / "receipt_verification_results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("RECEIPT VERIFICATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total receipts in database: {results['total_receipts']}")
        logger.info(f"Receipts checked: {results['checked']}")
        logger.info(
            f"✅ Receipts with lines and words: {results['has_lines_and_words']}"
        )
        logger.info(
            f"⚠️  Receipts missing lines: {len(results['missing_lines'])}"
        )
        logger.info(
            f"⚠️  Receipts missing words: {len(results['missing_words'])}"
        )
        logger.info(
            f"❌ Receipts missing both: {len(results['missing_both'])}"
        )
        logger.info(f"Errors: {len(results['errors'])}")
        logger.info(f"Results saved to {results_path}")

        if results["missing_lines"]:
            logger.warning("\nReceipts missing lines:")
            for receipt in results["missing_lines"][:10]:
                logger.warning(
                    f"  - Image {receipt['image_id']}, Receipt {receipt['receipt_id']} "
                    f"(has {receipt['words_count']} words)"
                )
            if len(results["missing_lines"]) > 10:
                logger.warning(
                    f"  ... and {len(results['missing_lines']) - 10} more"
                )

        if results["missing_words"]:
            logger.warning("\nReceipts missing words:")
            for receipt in results["missing_words"][:10]:
                logger.warning(
                    f"  - Image {receipt['image_id']}, Receipt {receipt['receipt_id']} "
                    f"(has {receipt['lines_count']} lines)"
                )
            if len(results["missing_words"]) > 10:
                logger.warning(
                    f"  ... and {len(results['missing_words']) - 10} more"
                )

        if results["missing_both"]:
            logger.error("\nReceipts missing both lines and words:")
            for receipt in results["missing_both"][:10]:
                logger.error(
                    f"  - Image {receipt['image_id']}, Receipt {receipt['receipt_id']}"
                )
            if len(results["missing_both"]) > 10:
                logger.error(
                    f"  ... and {len(results['missing_both']) - 10} more"
                )

        if results["errors"]:
            logger.error(f"\n{len(results['errors'])} errors occurred:")
            for error in results["errors"][:5]:
                logger.error(f"  - {error}")
            if len(results["errors"]) > 5:
                logger.error(f"  ... and {len(results['errors']) - 5} more")

        if (
            not results["missing_lines"]
            and not results["missing_words"]
            and not results["missing_both"]
            and not results["errors"]
        ):
            logger.info("\n✅ All receipts have lines and words!")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


