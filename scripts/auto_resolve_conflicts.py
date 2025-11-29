#!/usr/bin/env python3
"""
Automatically resolve conflicts using intelligent heuristics.

This script makes decisions based on:
1. If duplicate has additional labels, add them (merge strategy)
2. If labels are the same, keep canonical
3. Prefer VALID labels over INVALID
4. Prefer more labels over fewer (more information)

Usage:
    # Generate automated resolutions
    python scripts/auto_resolve_conflicts.py \
      --label-report label_comparison/label_comparison_report.json

    # Review the resolutions
    cat auto_conflict_resolutions.json

    # Apply them
    python scripts/auto_resolve_conflicts.py \
      --label-report label_comparison/label_comparison_report.json \
      --apply --no-dry-run
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

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


def auto_resolve_conflict(conflict: Dict) -> Dict:
    """Automatically resolve a conflict using heuristics.

    Strategy:
    1. Ensure at least one VALID label exists after resolution
    2. Add labels from duplicate that don't exist in canonical
    3. If canonical has no VALID but duplicate has VALID, add VALID labels even if they exist in canonical
    """
    can_labels = {l["label"]: l for l in conflict["canonical_labels"]}
    dup_labels = {l["label"]: l for l in conflict["duplicate_labels"]}

    can_label_set = set(can_labels.keys())
    dup_label_set = set(dup_labels.keys())

    # Check validation status
    can_has_valid = any(l.get("validation_status") == "VALID" for l in can_labels.values())
    dup_has_valid = any(l.get("validation_status") == "VALID" for l in dup_labels.values())

    # Strategy: Merge additional labels from duplicate
    # Only add labels that don't exist in canonical
    labels_to_add = []

    for label_name, label_data in dup_labels.items():
        if label_name not in can_label_set:
            # This label is only in duplicate, add it
            labels_to_add.append(label_data)
        elif label_name in can_label_set:
            # Label exists in both - check if we need to add VALID version
            can_label = can_labels[label_name]
            dup_label = label_data

            can_valid = can_label.get("validation_status") == "VALID"
            dup_valid = dup_label.get("validation_status") == "VALID"

            # If canonical has no VALID labels but duplicate has VALID for this label,
            # add the VALID version from duplicate to ensure we have at least one VALID
            if not can_has_valid and dup_valid:
                labels_to_add.append(dup_label)

    # Ensure we have at least one VALID label if possible
    # If canonical has no VALID and we're not adding any VALID, try to add VALID labels from duplicate
    if not can_has_valid and not any(
        l.get("validation_status") == "VALID" for l in labels_to_add
    ):
        # Add all VALID labels from duplicate
        for label_name, label_data in dup_labels.items():
            if label_data.get("validation_status") == "VALID":
                # Only add if not already in canonical or if canonical version is INVALID
                if label_name not in can_label_set:
                    labels_to_add.append(label_data)
                elif can_labels[label_name].get("validation_status") != "VALID":
                    # Canonical has this label but it's INVALID, add VALID version
                    labels_to_add.append(label_data)

    # Check if we'll have at least one VALID after resolution
    will_have_valid = can_has_valid or any(
        l.get("validation_status") == "VALID" for l in labels_to_add
    )

    # If still no VALID available, at least preserve all labels (both canonical and duplicate)
    # This ensures we don't lose information even if all labels are INVALID
    if not will_have_valid:
        # Add any labels from duplicate that aren't in canonical
        # (This should already be done above, but ensure we have all labels)
        for label_name, label_data in dup_labels.items():
            if label_name not in can_label_set and label_data not in labels_to_add:
                labels_to_add.append(label_data)

    # Decision
    if labels_to_add:
        action = "add_missing"
        added_labels_list = [l["label"] for l in labels_to_add]
        reasoning = f"Adding {len(labels_to_add)} labels from duplicate: {added_labels_list}"
        if not can_has_valid:
            reasoning += " (ensuring at least one VALID label)"
    else:
        action = "keep_canonical"
        reasoning = "Labels are the same or canonical has all labels already"
        if can_has_valid:
            reasoning += " (canonical has VALID labels)"

    return {
        "action": action,
        "labels_to_add": labels_to_add,
        "reasoning": reasoning,
        "canonical_labels": list(can_label_set),
        "duplicate_labels": list(dup_label_set),
        "added_labels": [l["label"] for l in labels_to_add],
        "canonical_has_valid": can_has_valid,
        "duplicate_has_valid": dup_has_valid,
        "will_have_valid": can_has_valid or any(
            l.get("validation_status") == "VALID" for l in labels_to_add
        ),
    }


def auto_resolve_all_conflicts(label_report: Dict) -> Dict:
    """Automatically resolve all conflicts."""
    resolutions = {}
    stats = {
        "total_conflicts": 0,
        "resolved": 0,
        "add_missing": 0,
        "keep_canonical": 0,
        "total_labels_to_add": 0,
    }

    for group in label_report["group_comparisons"]:
        canonical_id = group["canonical_image_id"]
        sha256 = group["sha256"]

        for conflict in group["merge_plan"]["conflicts_to_resolve"]:
            stats["total_conflicts"] += 1

            # Create unique key
            conflict_key = (
                f"{conflict['receipt_id']}_{canonical_id}_"
                f"{conflict['line_id']}_{conflict['word_id']}_"
                f"{conflict['source_image_id']}"
            )

            # Add metadata
            conflict["canonical_image_id"] = canonical_id
            conflict["sha256"] = sha256

            # Auto-resolve
            resolution = auto_resolve_conflict(conflict)

            resolutions[conflict_key] = {
                "receipt_id": conflict["receipt_id"],
                "canonical_image_id": canonical_id,
                "line_id": conflict["line_id"],
                "word_id": conflict["word_id"],
                "source_image_id": conflict["source_image_id"],
                "decision": resolution,
                "conflict_data": conflict,
            }

            stats["resolved"] += 1
            if resolution["action"] == "add_missing":
                stats["add_missing"] += 1
                stats["total_labels_to_add"] += len(resolution["labels_to_add"])
            else:
                stats["keep_canonical"] += 1

    return resolutions, stats


def apply_resolutions(
    dynamo_client: DynamoClient,
    resolutions: Dict,
    dry_run: bool = True,
) -> Dict:
    """Apply conflict resolutions to DynamoDB."""
    results = {
        "dry_run": dry_run,
        "total_resolutions": len(resolutions),
        "labels_added": 0,
        "errors": [],
    }

    for conflict_key, resolution in resolutions.items():
        decision = resolution["decision"]

        if decision["action"] == "keep_canonical":
            continue

        canonical_id = resolution["canonical_image_id"]
        labels_to_add = decision["labels_to_add"]

        for label_data in labels_to_add:
            try:
                new_label = ReceiptWordLabel(
                    image_id=canonical_id,
                    receipt_id=resolution["receipt_id"],
                    line_id=resolution["line_id"],
                    word_id=resolution["word_id"],
                    label=label_data["label"],
                    reasoning=label_data.get("reasoning"),
                    timestamp_added=label_data.get("timestamp_added"),
                    validation_status=label_data.get("validation_status"),
                    label_proposed_by=label_data.get("label_proposed_by"),
                    label_consolidated_from=resolution["source_image_id"],
                )

                if dry_run:
                    logger.debug(
                        f"[DRY RUN] Would add label {label_data['label']} "
                        f"for Receipt {resolution['receipt_id']}, "
                        f"Line {resolution['line_id']}, Word {resolution['word_id']}"
                    )
                else:
                    dynamo_client.add_receipt_word_label(new_label)
                    logger.info(
                        f"✅ Added label {label_data['label']} "
                        f"for Receipt {resolution['receipt_id']}, "
                        f"Line {resolution['line_id']}, Word {resolution['word_id']}"
                    )

                results["labels_added"] += 1

            except Exception as e:
                error_msg = f"Failed to add label for {conflict_key}: {e}"
                logger.error(error_msg, exc_info=True)
                results["errors"].append(error_msg)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Automatically resolve conflicts using heuristics"
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
        "--output",
        default="auto_conflict_resolutions.json",
        help="Output file for resolutions",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply resolved conflicts to DynamoDB",
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
        help="Actually apply (overrides --dry-run)",
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

        # Auto-resolve all conflicts
        logger.info("Automatically resolving conflicts...")
        resolutions, stats = auto_resolve_all_conflicts(label_report)

        # Print summary
        print("\n" + "=" * 80)
        print("AUTOMATED CONFLICT RESOLUTION SUMMARY")
        print("=" * 80)
        print(f"\nTotal conflicts: {stats['total_conflicts']}")
        print(f"Resolved: {stats['resolved']}")
        print(f"\nDecisions:")
        print(f"  Keep canonical (no changes): {stats['keep_canonical']}")
        print(f"  Add missing labels: {stats['add_missing']}")
        print(f"  Total labels to add: {stats['total_labels_to_add']}")

        # Save resolutions
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(resolutions, f, indent=2, default=str)
        logger.info(f"\n💾 Resolutions saved to {output_path}")

        # Show some examples
        print("\n" + "=" * 80)
        print("SAMPLE RESOLUTIONS (first 5):")
        print("=" * 80)
        for i, (key, resolution) in enumerate(list(resolutions.items())[:5], 1):
            decision = resolution["decision"]
            print(f"\n{i}. Receipt {resolution['receipt_id']}, Line {resolution['line_id']}, Word {resolution['word_id']}")
            print(f"   Action: {decision['action']}")
            print(f"   Reasoning: {decision['reasoning']}")
            if decision['added_labels']:
                print(f"   Labels to add: {decision['added_labels']}")

        # Apply if requested
        if args.apply:
            if args.dry_run:
                logger.warning("\n⚠️  DRY RUN MODE - No actual changes will occur")
            else:
                logger.warning("\n⚠️  LIVE MODE - This will ADD labels to DynamoDB!")
                response = input("Are you sure? (yes/no): ")
                if response.lower() != "yes":
                    logger.info("Cancelled")
                    return

            table_name = get_table_name(args.stack)
            client = DynamoClient(table_name)

            logger.info("\nApplying resolutions...")
            results = apply_resolutions(client, resolutions, dry_run=args.dry_run)

            print(f"\n✅ Applied {results['labels_added']} labels")
            if results["errors"]:
                print(f"⚠️  {len(results['errors'])} errors occurred")
                for error in results["errors"][:5]:
                    print(f"  - {error}")
        else:
            logger.info("\n💡 Review the resolutions, then apply with:")
            logger.info(f"   python scripts/auto_resolve_conflicts.py --stack {args.stack} --label-report {args.label_report} --apply --no-dry-run")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

