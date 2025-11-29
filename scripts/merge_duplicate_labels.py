#!/usr/bin/env python3
"""
Merge receipt word labels from duplicate images into canonical images.

This script:
1. Loads the label comparison report
2. Groups conflicts by receipt for easier review
3. Automatically merges non-conflicting labels
4. Generates a conflict report organized by receipt
5. Can execute the merges (with dry-run option)

Usage:
    # Review conflicts by receipt
    python scripts/merge_duplicate_labels.py --stack dev --label-report label_comparison/label_comparison_report.json

    # Merge non-conflicting labels (dry-run)
    python scripts/merge_duplicate_labels.py --stack dev --label-report ... --merge --dry-run

    # Actually merge labels
    python scripts/merge_duplicate_labels.py --stack dev --label-report ... --merge --no-dry-run
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Set

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")

    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )

    logger.info(f"{stack.upper()} table: {table_name}")
    return table_name


def organize_conflicts_by_receipt(label_report: Dict) -> Dict:
    """Organize conflicts by receipt for easier review."""
    conflicts_by_receipt = defaultdict(lambda: defaultdict(list))

    for group in label_report["group_comparisons"]:
        canonical_id = group["canonical_image_id"]
        sha256 = group["sha256"]

        for conflict in group["merge_plan"]["conflicts_to_resolve"]:
            receipt_id = conflict["receipt_id"]
            conflicts_by_receipt[receipt_id][canonical_id].append(
                {
                    "sha256": sha256,
                    "line_id": conflict["line_id"],
                    "word_id": conflict["word_id"],
                    "canonical_labels": conflict["canonical_labels"],
                    "duplicate_labels": conflict["duplicate_labels"],
                    "source_image_id": conflict["source_image_id"],
                }
            )

    return dict(conflicts_by_receipt)


def print_conflicts_by_receipt(conflicts_by_receipt: Dict):
    """Print conflicts organized by receipt."""
    print("\n" + "=" * 80)
    print("CONFLICTS ORGANIZED BY RECEIPT")
    print("=" * 80)

    for receipt_id in sorted(conflicts_by_receipt.keys()):
        print(f"\n{'=' * 80}")
        print(f"RECEIPT ID: {receipt_id}")
        print(f"{'=' * 80}")

        receipt_conflicts = conflicts_by_receipt[receipt_id]
        total_conflicts = sum(len(confs) for confs in receipt_conflicts.values())

        print(f"\nTotal conflicts in this receipt: {total_conflicts}")
        print(f"Affected canonical images: {len(receipt_conflicts)}")

        for canonical_id, conflicts in receipt_conflicts.items():
            print(f"\n  Canonical Image: {canonical_id}")
            print(f"  Conflicts: {len(conflicts)}")

            for i, conflict in enumerate(conflicts, 1):
                print(f"\n    Conflict {i}: Line {conflict['line_id']}, Word {conflict['word_id']}")
                print(f"      SHA256: {conflict['sha256']}")
                print(f"      Source (duplicate): {conflict['source_image_id']}")
                print(f"      Canonical labels: {[l['label'] for l in conflict['canonical_labels']]}")
                print(f"      Duplicate labels: {[l['label'] for l in conflict['duplicate_labels']]}")

                # Show reasoning if available
                if conflict["canonical_labels"]:
                    can_label = conflict["canonical_labels"][0]
                    if can_label.get("reasoning"):
                        print(f"      Canonical reasoning: {can_label['reasoning'][:100]}...")
                if conflict["duplicate_labels"]:
                    dup_label = conflict["duplicate_labels"][0]
                    if dup_label.get("reasoning"):
                        print(f"      Duplicate reasoning: {dup_label['reasoning'][:100]}...")


def merge_non_conflicting_labels(
    dynamo_client: DynamoClient,
    label_report: Dict,
    dry_run: bool = True,
) -> Dict:
    """Merge non-conflicting labels from duplicates into canonical images."""
    results = {
        "dry_run": dry_run,
        "total_groups_processed": 0,
        "total_labels_added": 0,
        "total_errors": 0,
        "merges": [],
    }

    for group in label_report["group_comparisons"]:
        canonical_id = group["canonical_image_id"]
        group_result = {
            "canonical_image_id": canonical_id,
            "sha256": group["sha256"],
            "labels_added": 0,
            "errors": [],
        }

        # Get labels to add (non-conflicting)
        labels_to_add = group["merge_plan"]["labels_to_add"]

        logger.info(
            f"\nProcessing canonical {canonical_id}: {len(labels_to_add)} labels to add"
        )

        for label_info in labels_to_add:
            try:
                # Create ReceiptWordLabel from the label data
                label_data = label_info["label_data"]
                new_label = ReceiptWordLabel(
                    image_id=canonical_id,  # Use canonical image ID
                    receipt_id=label_info["receipt_id"],
                    line_id=label_info["line_id"],
                    word_id=label_info["word_id"],
                    label=label_data["label"],
                    reasoning=label_data.get("reasoning"),
                    timestamp_added=label_data.get("timestamp_added"),
                    validation_status=label_data.get("validation_status"),
                    label_proposed_by=label_data.get("label_proposed_by"),
                    label_consolidated_from=label_data.get(
                        "label_consolidated_from"
                    )
                    or label_info["source_image_id"],
                )

                if dry_run:
                    logger.info(
                        f"  [DRY RUN] Would add label: {label_data['label']} "
                        f"for Receipt {label_info['receipt_id']}, "
                        f"Line {label_info['line_id']}, Word {label_info['word_id']}"
                    )
                    group_result["labels_added"] += 1
                else:
                    # Actually add the label
                    dynamo_client.add_receipt_word_label(new_label)
                    logger.info(
                        f"  ✅ Added label: {label_data['label']} "
                        f"for Receipt {label_info['receipt_id']}, "
                        f"Line {label_info['line_id']}, Word {label_info['word_id']}"
                    )
                    group_result["labels_added"] += 1

            except Exception as e:
                error_msg = f"Failed to add label for {label_info}: {e}"
                logger.error(error_msg, exc_info=True)
                group_result["errors"].append(error_msg)
                results["total_errors"] += 1

        results["merges"].append(group_result)
        results["total_labels_added"] += group_result["labels_added"]
        results["total_groups_processed"] += 1

    return results


def generate_conflict_report(
    conflicts_by_receipt: Dict, output_path: Path
) -> None:
    """Generate a detailed conflict report organized by receipt."""
    report_path = output_path / "conflicts_by_receipt.json"
    with open(report_path, "w") as f:
        json.dump(conflicts_by_receipt, f, indent=2, default=str)
    logger.info(f"Conflict report saved to {report_path}")

    # Also create a text summary
    summary_path = output_path / "conflicts_by_receipt_summary.txt"
    with open(summary_path, "w") as f:
        f.write("=" * 80 + "\n")
        f.write("CONFLICTS ORGANIZED BY RECEIPT\n")
        f.write("=" * 80 + "\n\n")

        for receipt_id in sorted(conflicts_by_receipt.keys()):
            f.write(f"\n{'=' * 80}\n")
            f.write(f"RECEIPT ID: {receipt_id}\n")
            f.write(f"{'=' * 80}\n\n")

            receipt_conflicts = conflicts_by_receipt[receipt_id]
            total_conflicts = sum(len(confs) for confs in receipt_conflicts.values())

            f.write(f"Total conflicts: {total_conflicts}\n")
            f.write(f"Affected canonical images: {len(receipt_conflicts)}\n\n")

            for canonical_id, conflicts in receipt_conflicts.items():
                f.write(f"Canonical Image: {canonical_id}\n")
                f.write(f"  Conflicts: {len(conflicts)}\n\n")

                for i, conflict in enumerate(conflicts, 1):
                    f.write(f"  Conflict {i}: Line {conflict['line_id']}, Word {conflict['word_id']}\n")
                    f.write(f"    SHA256: {conflict['sha256']}\n")
                    f.write(f"    Source (duplicate): {conflict['source_image_id']}\n")
                    f.write(
                        f"    Canonical labels: {[l['label'] for l in conflict['canonical_labels']]}\n"
                    )
                    f.write(
                        f"    Duplicate labels: {[l['label'] for l in conflict['duplicate_labels']]}\n"
                    )

                    # Show full reasoning
                    if conflict["canonical_labels"]:
                        can_label = conflict["canonical_labels"][0]
                        if can_label.get("reasoning"):
                            f.write(f"    Canonical reasoning: {can_label['reasoning']}\n")
                    if conflict["duplicate_labels"]:
                        dup_label = conflict["duplicate_labels"][0]
                        if dup_label.get("reasoning"):
                            f.write(f"    Duplicate reasoning: {dup_label['reasoning']}\n")
                    f.write("\n")

    logger.info(f"Conflict summary saved to {summary_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Merge receipt word labels from duplicate images"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--label-report",
        required=True,
        help="Path to label_comparison_report.json",
    )
    parser.add_argument(
        "--output-dir",
        default="label_merge",
        help="Output directory for results (default: label_merge)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Actually merge non-conflicting labels",
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
        help="Actually execute merges (overrides --dry-run)",
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
        # Load label report
        with open(args.label_report, "r") as f:
            label_report = json.load(f)

        # Organize conflicts by receipt
        logger.info("Organizing conflicts by receipt...")
        conflicts_by_receipt = organize_conflicts_by_receipt(label_report)

        # Print conflicts
        print_conflicts_by_receipt(conflicts_by_receipt)

        # Save conflict report
        output_path = Path(args.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        generate_conflict_report(conflicts_by_receipt, output_path)

        # Print summary
        total_conflicts = sum(
            sum(len(confs) for confs in receipt_conflicts.values())
            for receipt_conflicts in conflicts_by_receipt.values()
        )
        print(f"\n{'=' * 80}")
        print("SUMMARY")
        print(f"{'=' * 80}")
        print(f"Total receipts with conflicts: {len(conflicts_by_receipt)}")
        print(f"Total conflicts: {total_conflicts}")

        # Count non-conflicting labels to merge
        total_labels_to_add = sum(
            len(group["merge_plan"]["labels_to_add"])
            for group in label_report["group_comparisons"]
        )
        print(f"Non-conflicting labels to merge: {total_labels_to_add}")

        # Merge if requested
        if args.merge:
            if args.dry_run:
                logger.warning("\n⚠️  DRY RUN MODE - No actual merges will occur")
            else:
                logger.warning(
                    "\n⚠️  LIVE MODE - This will ADD labels to DynamoDB!"
                )
                response = input("Are you sure you want to proceed? (yes/no): ")
                if response.lower() != "yes":
                    logger.info("Merge cancelled by user")
                    return

            table_name = get_table_name(args.stack)
            client = DynamoClient(table_name)

            logger.info("\nMerging non-conflicting labels...")
            results = merge_non_conflicting_labels(
                client, label_report, dry_run=args.dry_run
            )

            # Save results
            results_path = output_path / "merge_results.json"
            with open(results_path, "w") as f:
                json.dump(results, f, indent=2, default=str)

            logger.info(f"\nMerge results saved to {results_path}")
            logger.info(
                f"Groups processed: {results['total_groups_processed']}, "
                f"Labels added: {results['total_labels_added']}, "
                f"Errors: {results['total_errors']}"
            )
        else:
            logger.info("\n💡 Use --merge to actually merge non-conflicting labels")
            logger.info("   Use --dry-run (default) to see what would be merged")
            logger.info("   Use --no-dry-run to actually merge")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


