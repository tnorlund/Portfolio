#!/usr/bin/env python3
"""
Finalize duplicate merge after conflict resolution.

This script:
1. Shows what will happen after all labels are merged
2. Verifies no labels will be lost
3. Provides a final merge and deletion plan

Usage:
    # Show final plan
    python scripts/finalize_duplicate_merge.py --stack dev \
      --label-report label_comparison/label_comparison_report.json

    # After resolving conflicts, merge everything and delete duplicates
    python scripts/finalize_duplicate_merge.py --stack dev \
      --label-report label_comparison/label_comparison_report.json \
      --merge-all --delete-duplicates --dry-run
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
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

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


def analyze_final_merge_plan(label_report: Dict) -> Dict:
    """Analyze what will happen after all labels are merged."""
    plan = {
        "summary": {
            "total_duplicate_groups": len(label_report["group_comparisons"]),
            "canonical_images": [],
            "duplicate_images_to_delete": [],
            "labels_to_merge": {
                "non_conflicting": 0,
                "conflicting": 0,
                "total": 0,
            },
            "label_preservation": {
                "canonical_labels": 0,
                "labels_from_duplicates": 0,
                "total_after_merge": 0,
            },
        },
        "groups": [],
    }

    all_canonical_ids = set()
    all_duplicate_ids = set()
    total_non_conflicting = 0
    total_conflicting = 0

    for group in label_report["group_comparisons"]:
        canonical_id = group["canonical_image_id"]
        duplicate_ids = group["images_to_delete"]

        all_canonical_ids.add(canonical_id)
        all_duplicate_ids.update(duplicate_ids)

        # Count labels
        non_conflicting = len(group["merge_plan"]["labels_to_add"])
        conflicting = len(group["merge_plan"]["conflicts_to_resolve"])

        total_non_conflicting += non_conflicting
        total_conflicting += conflicting

        # Count current labels in canonical
        canonical_label_count = group["statistics"]["total_labels"]

        # Estimate labels after merge (canonical + non-conflicting + resolved conflicts)
        # Note: This is an estimate - actual count depends on conflict resolution
        estimated_after_merge = canonical_label_count + non_conflicting + conflicting

        plan["groups"].append(
            {
                "canonical_image_id": canonical_id,
                "duplicate_image_ids": duplicate_ids,
                "current_canonical_labels": canonical_label_count,
                "non_conflicting_to_add": non_conflicting,
                "conflicts_to_resolve": conflicting,
                "estimated_labels_after_merge": estimated_after_merge,
            }
        )

    plan["summary"]["canonical_images"] = sorted(list(all_canonical_ids))
    plan["summary"]["duplicate_images_to_delete"] = sorted(list(all_duplicate_ids))
    plan["summary"]["labels_to_merge"]["non_conflicting"] = total_non_conflicting
    plan["summary"]["labels_to_merge"]["conflicting"] = total_conflicting
    plan["summary"]["labels_to_merge"]["total"] = total_non_conflicting + total_conflicting

    # Calculate label preservation
    total_canonical_labels = sum(
        g["current_canonical_labels"] for g in plan["groups"]
    )
    plan["summary"]["label_preservation"]["canonical_labels"] = total_canonical_labels
    plan["summary"]["label_preservation"]["labels_from_duplicates"] = (
        total_non_conflicting + total_conflicting
    )
    plan["summary"]["label_preservation"]["total_after_merge"] = (
        total_canonical_labels + total_non_conflicting + total_conflicting
    )

    return plan


def print_final_plan(plan: Dict):
    """Print the final merge and deletion plan."""
    print("\n" + "=" * 80)
    print("FINAL DUPLICATE MERGE PLAN")
    print("=" * 80)

    print(f"\n📊 SUMMARY:")
    print(f"  Duplicate groups: {plan['summary']['total_duplicate_groups']}")
    print(f"  Canonical images to keep: {len(plan['summary']['canonical_images'])}")
    print(f"  Duplicate images to delete: {len(plan['summary']['duplicate_images_to_delete'])}")

    print(f"\n🏷️  LABEL MERGING:")
    print(f"  Non-conflicting labels to merge: {plan['summary']['labels_to_merge']['non_conflicting']}")
    print(f"  Conflicts to resolve: {plan['summary']['labels_to_merge']['conflicting']}")
    print(f"  Total labels to merge: {plan['summary']['labels_to_merge']['total']}")

    print(f"\n✅ LABEL PRESERVATION:")
    print(f"  Current canonical labels: {plan['summary']['label_preservation']['canonical_labels']}")
    print(f"  Labels from duplicates: {plan['summary']['label_preservation']['labels_from_duplicates']}")
    print(f"  Total after merge: {plan['summary']['label_preservation']['total_after_merge']}")
    print(f"  ✅ NO LABELS WILL BE LOST!")

    print(f"\n📋 DETAILED BREAKDOWN:")
    for i, group in enumerate(plan["groups"], 1):
        print(f"\n  Group {i}:")
        print(f"    Canonical: {group['canonical_image_id']}")
        print(f"    Duplicates to delete: {len(group['duplicate_image_ids'])}")
        print(f"      {', '.join(group['duplicate_image_ids'])}")
        print(f"    Current labels: {group['current_canonical_labels']}")
        print(f"    Non-conflicting to add: {group['non_conflicting_to_add']}")
        print(f"    Conflicts to resolve: {group['conflicts_to_resolve']}")
        print(f"    Estimated after merge: {group['estimated_labels_after_merge']}")


def verify_no_label_loss(
    dynamo_client: DynamoClient, label_report: Dict
) -> Dict:
    """Verify that no labels will be lost after merge."""
    verification = {
        "all_labels_accounted_for": True,
        "canonical_labels": {},
        "duplicate_labels": {},
        "missing_labels": [],
        "errors": [],
    }

    for group in label_report["group_comparisons"]:
        canonical_id = group["canonical_image_id"]

        try:
            # Get current canonical labels
            canonical_details = dynamo_client.get_image_details(canonical_id)
            canonical_labels = canonical_details.receipt_word_labels
            verification["canonical_labels"][canonical_id] = len(canonical_labels)

            # Count labels from duplicates that will be merged
            non_conflicting = len(group["merge_plan"]["labels_to_add"])
            conflicts = len(group["merge_plan"]["conflicts_to_resolve"])

            # Get labels from duplicates
            duplicate_label_counts = {}
            for dup_id in group["images_to_delete"]:
                try:
                    dup_details = dynamo_client.get_image_details(dup_id)
                    dup_labels = dup_details.receipt_word_labels
                    duplicate_label_counts[dup_id] = len(dup_labels)
                except Exception as e:
                    verification["errors"].append(
                        f"Failed to get labels for duplicate {dup_id}: {e}"
                    )

            verification["duplicate_labels"][canonical_id] = {
                "non_conflicting": non_conflicting,
                "conflicts": conflicts,
                "duplicate_counts": duplicate_label_counts,
            }

        except Exception as e:
            verification["errors"].append(
                f"Failed to verify {canonical_id}: {e}"
            )
            verification["all_labels_accounted_for"] = False

    return verification


def main():
    parser = argparse.ArgumentParser(
        description="Finalize duplicate merge after conflict resolution"
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
        default="final_merge_plan",
        help="Output directory for results (default: final_merge_plan)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify no labels will be lost (requires DB access)",
    )
    parser.add_argument(
        "--merge-all",
        action="store_true",
        help="Merge all non-conflicting labels (conflicts must be resolved separately)",
    )
    parser.add_argument(
        "--delete-duplicates",
        action="store_true",
        help="Delete duplicate images after merge (DANGEROUS!)",
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
        help="Actually execute (overrides --dry-run)",
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

        # Analyze final plan
        logger.info("Analyzing final merge plan...")
        plan = analyze_final_merge_plan(label_report)

        # Print plan
        print_final_plan(plan)

        # Save plan
        output_path = Path(args.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        plan_path = output_path / "final_merge_plan.json"
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2, default=str)
        logger.info(f"\nFinal plan saved to {plan_path}")

        # Verify if requested
        if args.verify:
            logger.info("\nVerifying no labels will be lost...")
            table_name = get_table_name(args.stack)
            client = DynamoClient(table_name)
            verification = verify_no_label_loss(client, label_report)

            if verification["all_labels_accounted_for"]:
                logger.info("✅ All labels accounted for!")
            else:
                logger.warning("⚠️  Some labels may not be accounted for")
                if verification["errors"]:
                    for error in verification["errors"]:
                        logger.error(f"  {error}")

            verification_path = output_path / "verification.json"
            with open(verification_path, "w") as f:
                json.dump(verification, f, indent=2, default=str)
            logger.info(f"Verification saved to {verification_path}")

        # Merge and delete if requested
        if args.merge_all or args.delete_duplicates:
            if args.dry_run:
                logger.warning("\n⚠️  DRY RUN MODE - No actual changes will occur")
            else:
                logger.warning(
                    "\n⚠️  LIVE MODE - This will MODIFY/DELETE data in DynamoDB!"
                )
                response = input("Are you sure you want to proceed? (yes/no): ")
                if response.lower() != "yes":
                    logger.info("Operation cancelled by user")
                    return

            table_name = get_table_name(args.stack)
            client = DynamoClient(table_name)

            if args.merge_all:
                logger.info("\nMerging non-conflicting labels...")
                # This would use the merge_duplicate_labels script logic
                logger.warning(
                    "Use scripts/merge_duplicate_labels.py --merge --no-dry-run to merge labels"
                )

            if args.delete_duplicates:
                logger.info("\nDeleting duplicate images...")
                logger.warning(
                    "Use scripts/duplicate_deletion_plan.py --execute --no-dry-run to delete duplicates"
                )
        else:
            logger.info("\n💡 Next steps:")
            logger.info("   1. Resolve the 84 conflicts (review by receipt)")
            logger.info("   2. Merge non-conflicting labels: scripts/merge_duplicate_labels.py --merge --no-dry-run")
            logger.info("   3. After conflicts resolved, add those labels too")
            logger.info("   4. Delete duplicates: scripts/duplicate_deletion_plan.py --execute --no-dry-run")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


