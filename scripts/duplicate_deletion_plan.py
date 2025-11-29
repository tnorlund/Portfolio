#!/usr/bin/env python3
"""
Create a deletion plan for duplicate images and show receipt counts.

This script:
1. Analyzes the duplicate report to count receipts
2. Shows which images will be kept vs deleted
3. Creates a detailed deletion plan
4. Can execute deletions one-by-one or all at once

Usage:
    # Show plan only
    python scripts/duplicate_deletion_plan.py --stack dev --report duplicate_analysis/duplicate_analysis_report.json --label-report label_comparison/label_comparison_report.json

    # Execute deletions (dry-run)
    python scripts/duplicate_deletion_plan.py --stack dev --report ... --label-report ... --execute --dry-run

    # Execute deletions for real
    python scripts/duplicate_deletion_plan.py --stack dev --report ... --label-report ... --execute
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

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
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")

    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )

    logger.info(f"{stack.upper()} table: {table_name}")
    return table_name


def analyze_deletion_plan(
    duplicate_report_path: str, label_report_path: str
) -> Dict:
    """Analyze duplicates and create a deletion plan."""
    # Load reports
    with open(duplicate_report_path, "r") as f:
        duplicate_report = json.load(f)

    with open(label_report_path, "r") as f:
        label_report = json.load(f)

    # Build mapping from label report
    canonical_by_group = {}
    for group in label_report["group_comparisons"]:
        sha256 = group["sha256"]
        canonical_by_group[sha256] = {
            "canonical": group["canonical_image_id"],
            "to_delete": group["images_to_delete"],
        }

    # Analyze each duplicate group
    deletion_plan = {
        "summary": {
            "total_duplicate_groups": len(duplicate_report["duplicate_groups"]),
            "total_images_to_delete": 0,
            "total_images_to_keep": 0,
            "total_receipts_affected": 0,
            "unique_receipt_ids": set(),
        },
        "groups": [],
    }

    all_receipt_ids = set()

    for group in duplicate_report["duplicate_groups"]:
        sha256 = group["sha256"]
        group_info = canonical_by_group.get(sha256, {})

        canonical_id = group_info.get("canonical", group["image_ids"][0])
        to_delete = group_info.get("to_delete", group["image_ids"][1:])

        # Get receipt IDs for this group
        receipt_ids = set(group["unique_receipt_ids"])
        all_receipt_ids.update(receipt_ids)

        # Find canonical and duplicate image details
        canonical_image = next(
            (img for img in group["images"] if img["image_id"] == canonical_id), None
        )
        duplicate_images = [
            img for img in group["images"] if img["image_id"] in to_delete
        ]

        deletion_plan["groups"].append(
            {
                "sha256": sha256,
                "canonical_image": {
                    "image_id": canonical_id,
                    "receipt_ids": canonical_image["receipt_ids"]
                    if canonical_image
                    else [],
                    "label_count": canonical_image["receipt_word_label_count"]
                    if canonical_image
                    else 0,
                    "timestamp": canonical_image["timestamp_added"]
                    if canonical_image
                    else None,
                },
                "duplicate_images": [
                    {
                        "image_id": img["image_id"],
                        "receipt_ids": img["receipt_ids"],
                        "label_count": img["receipt_word_label_count"],
                        "timestamp": img["timestamp_added"],
                    }
                    for img in duplicate_images
                ],
                "receipt_ids": sorted(list(receipt_ids)),
                "total_receipts": len(receipt_ids),
            }
        )

        deletion_plan["summary"]["total_images_to_delete"] += len(to_delete)
        deletion_plan["summary"]["total_images_to_keep"] += 1

    deletion_plan["summary"]["total_receipts_affected"] = len(all_receipt_ids)
    deletion_plan["summary"]["unique_receipt_ids"] = sorted(list(all_receipt_ids))

    return deletion_plan


def print_deletion_plan(plan: Dict):
    """Print a human-readable deletion plan."""
    print("\n" + "=" * 80)
    print("DUPLICATE IMAGE DELETION PLAN")
    print("=" * 80)
    print(f"\nSummary:")
    print(f"  Total duplicate groups: {plan['summary']['total_duplicate_groups']}")
    print(f"  Images to KEEP: {plan['summary']['total_images_to_keep']}")
    print(f"  Images to DELETE: {plan['summary']['total_images_to_delete']}")
    print(f"  Total receipts affected: {plan['summary']['total_receipts_affected']}")
    print(
        f"  Unique receipt IDs: {len(plan['summary']['unique_receipt_ids'])} receipts"
    )

    print("\n" + "=" * 80)
    print("DETAILED GROUP BREAKDOWN")
    print("=" * 80)

    for i, group in enumerate(plan["groups"], 1):
        print(f"\nGroup {i}: {len(group['duplicate_images'])} duplicates")
        print(f"  SHA256: {group['sha256']}")
        print(f"  Receipts: {group['total_receipts']} (IDs: {group['receipt_ids']})")
        print(f"\n  ✅ KEEP (Canonical):")
        print(f"    Image ID: {group['canonical_image']['image_id']}")
        print(
            f"    Receipt IDs: {group['canonical_image']['receipt_ids']} ({len(group['canonical_image']['receipt_ids'])} receipts)"
        )
        print(f"    Labels: {group['canonical_image']['label_count']}")
        print(f"    Added: {group['canonical_image']['timestamp']}")

        print(f"\n  ❌ DELETE ({len(group['duplicate_images'])} images):")
        for dup in group["duplicate_images"]:
            print(f"    Image ID: {dup['image_id']}")
            print(
                f"      Receipt IDs: {dup['receipt_ids']} ({len(dup['receipt_ids'])} receipts)"
            )
            print(f"      Labels: {dup['label_count']}")
            print(f"      Added: {dup['timestamp']}")


def delete_image_and_related_data(
    dynamo_client: DynamoClient, image_id: str, dry_run: bool = True
) -> Dict:
    """Delete an image and all its related data."""
    result = {
        "image_id": image_id,
        "dry_run": dry_run,
        "deleted": {
            "image": False,
            "lines": 0,
            "words": 0,
            "letters": 0,
            "receipts": 0,
            "receipt_lines": 0,
            "receipt_words": 0,
            "receipt_letters": 0,
            "receipt_word_labels": 0,
            "receipt_metadatas": 0,
            "ocr_jobs": 0,
            "ocr_routing_decisions": 0,
        },
        "errors": [],
    }

    try:
        # Get all image details first
        details = dynamo_client.get_image_details(image_id)

        if dry_run:
            result["deleted"]["image"] = True
            result["deleted"]["lines"] = len(details.lines)
            result["deleted"]["words"] = len(details.words)
            result["deleted"]["letters"] = len(details.letters)
            result["deleted"]["receipts"] = len(details.receipts)
            result["deleted"]["receipt_lines"] = len(details.receipt_lines)
            result["deleted"]["receipt_words"] = len(details.receipt_words)
            result["deleted"]["receipt_letters"] = len(details.receipt_letters)
            result["deleted"]["receipt_word_labels"] = len(
                details.receipt_word_labels
            )
            result["deleted"]["receipt_metadatas"] = len(details.receipt_metadatas)
            result["deleted"]["ocr_jobs"] = len(details.ocr_jobs)
            result["deleted"]["ocr_routing_decisions"] = len(
                details.ocr_routing_decisions
            )
            logger.info(f"[DRY RUN] Would delete {image_id} and all related data")
        else:
            # Actually delete (this is a simplified version - you may need to
            # delete entities in the right order)
            # Note: This is a placeholder - actual deletion would need to handle
            # all entity types properly
            logger.warning(
                f"Actual deletion not fully implemented - this would delete {image_id}"
            )
            result["errors"].append("Actual deletion not implemented in this script")

    except Exception as e:
        result["errors"].append(str(e))
        logger.error(f"Error processing {image_id}: {e}")

    return result


def execute_deletion(
    table_name: str,
    plan: Dict,
    dry_run: bool = True,
    one_by_one: bool = False,
) -> Dict:
    """Execute the deletion plan."""
    client = DynamoClient(table_name)

    results = {
        "dry_run": dry_run,
        "total_processed": 0,
        "total_deleted": 0,
        "total_errors": 0,
        "deletions": [],
    }

    # Collect all images to delete
    images_to_delete = []
    for group in plan["groups"]:
        for dup in group["duplicate_images"]:
            images_to_delete.append(
                {
                    "image_id": dup["image_id"],
                    "group": group["sha256"],
                    "canonical": group["canonical_image"]["image_id"],
                }
            )

    logger.info(
        f"{'[DRY RUN] Would delete' if dry_run else 'Deleting'} {len(images_to_delete)} duplicate images..."
    )

    for i, img_info in enumerate(images_to_delete, 1):
        logger.info(
            f"\nProcessing {i}/{len(images_to_delete)}: {img_info['image_id']}"
        )
        result = delete_image_and_related_data(
            client, img_info["image_id"], dry_run=dry_run
        )
        result["group"] = img_info["group"]
        result["canonical"] = img_info["canonical"]
        results["deletions"].append(result)
        results["total_processed"] += 1

        if result["errors"]:
            results["total_errors"] += 1
        else:
            results["total_deleted"] += 1

        if one_by_one:
            input(f"Press Enter to continue to next deletion...")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Create and execute deletion plan for duplicate images"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to duplicate_analysis_report.json",
    )
    parser.add_argument(
        "--label-report",
        required=True,
        help="Path to label_comparison_report.json",
    )
    parser.add_argument(
        "--output-dir",
        default="deletion_plan",
        help="Output directory for deletion plan (default: deletion_plan)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute deletions (default: show plan only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True, set --no-dry-run to actually delete)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually execute deletions (overrides --dry-run)",
    )
    parser.add_argument(
        "--one-by-one",
        action="store_true",
        help="Process deletions one-by-one with confirmation",
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
        # Create deletion plan
        logger.info("Analyzing duplicate reports...")
        plan = analyze_deletion_plan(args.report, args.label_report)

        # Print plan
        print_deletion_plan(plan)

        # Save plan to file
        output_path = Path(args.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        plan_path = output_path / "deletion_plan.json"
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2, default=str)
        logger.info(f"\nDeletion plan saved to {plan_path}")

        # Execute if requested
        if args.execute:
            if args.dry_run:
                logger.warning("\n⚠️  DRY RUN MODE - No actual deletions will occur")
            else:
                logger.warning(
                    "\n⚠️  LIVE MODE - This will DELETE images from DynamoDB!"
                )
                response = input("Are you sure you want to proceed? (yes/no): ")
                if response.lower() != "yes":
                    logger.info("Deletion cancelled by user")
                    return

            table_name = get_table_name(args.stack)
            results = execute_deletion(
                table_name, plan, dry_run=args.dry_run, one_by_one=args.one_by_one
            )

            results_path = output_path / "deletion_results.json"
            with open(results_path, "w") as f:
                json.dump(results, f, indent=2, default=str)

            logger.info(f"\nDeletion results saved to {results_path}")
            logger.info(
                f"Processed: {results['total_processed']}, "
                f"Deleted: {results['total_deleted']}, "
                f"Errors: {results['total_errors']}"
            )
        else:
            logger.info("\n💡 Use --execute to actually delete duplicates")
            logger.info("   Use --dry-run (default) to see what would be deleted")
            logger.info("   Use --no-dry-run to actually delete")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


