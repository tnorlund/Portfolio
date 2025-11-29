#!/usr/bin/env python3
"""
Interactive script to resolve conflicts one by one.

This script:
1. Shows conflicts one at a time
2. Lets you decide what to do with each conflict
3. Records your decisions
4. Can apply the decisions to merge labels

Usage:
    # Start resolving conflicts
    python scripts/resolve_conflicts_interactive.py --stack dev \
      --label-report label_comparison/label_comparison_report.json

    # Resume from a saved state
    python scripts/resolve_conflicts_interactive.py --stack dev \
      --label-report label_comparison/label_comparison_report.json \
      --resume-from conflict_resolutions.json
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

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


def load_conflicts_by_receipt(label_report: Dict) -> Dict:
    """Load conflicts organized by receipt."""
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


def display_conflict(conflict: Dict, conflict_num: int, total: int):
    """Display a conflict for review."""
    print("\n" + "=" * 80)
    print(f"CONFLICT {conflict_num}/{total}")
    print("=" * 80)
    print(f"\nReceipt ID: {conflict.get('receipt_id', 'N/A')}")
    print(f"Line: {conflict['line_id']}, Word: {conflict['word_id']}")
    print(f"Canonical Image: {conflict.get('canonical_image_id', 'N/A')}")
    print(f"Source (duplicate): {conflict['source_image_id']}")

    print(f"\n📋 CANONICAL LABELS ({len(conflict['canonical_labels'])}):")
    for i, label in enumerate(conflict["canonical_labels"], 1):
        print(f"\n  {i}. {label['label']}")
        print(f"     Status: {label.get('validation_status', 'N/A')}")
        if label.get("reasoning"):
            reasoning = label["reasoning"]
            if len(reasoning) > 200:
                reasoning = reasoning[:200] + "..."
            print(f"     Reasoning: {reasoning}")
        if label.get("label_proposed_by"):
            print(f"     Proposed by: {label['label_proposed_by']}")

    print(f"\n📋 DUPLICATE LABELS ({len(conflict['duplicate_labels'])}):")
    for i, label in enumerate(conflict["duplicate_labels"], 1):
        print(f"\n  {i}. {label['label']}")
        print(f"     Status: {label.get('validation_status', 'N/A')}")
        if label.get("reasoning"):
            reasoning = label["reasoning"]
            if len(reasoning) > 200:
                reasoning = reasoning[:200] + "..."
            print(f"     Reasoning: {reasoning}")
        if label.get("label_proposed_by"):
            print(f"     Proposed by: {label['label_proposed_by']}")

    # Show differences
    can_labels = set(l["label"] for l in conflict["canonical_labels"])
    dup_labels = set(l["label"] for l in conflict["duplicate_labels"])
    only_in_dup = dup_labels - can_labels
    only_in_can = can_labels - dup_labels

    print(f"\n🔍 ANALYSIS:")
    if only_in_dup:
        print(f"  Labels only in duplicate: {sorted(only_in_dup)}")
    if only_in_can:
        print(f"  Labels only in canonical: {sorted(only_in_can)}")
    if can_labels == dup_labels:
        print(f"  Same labels, different reasoning/metadata")


def get_user_decision(conflict: Dict) -> Dict:
    """Get user's decision for a conflict."""
    print("\n" + "=" * 80)
    print("DECISION OPTIONS:")
    print("=" * 80)
    print("  1. Keep canonical labels only (do nothing)")
    print("  2. Add missing labels from duplicate to canonical")
    print("  3. Replace canonical with duplicate labels")
    print("  4. Custom: Select specific labels to keep/add")
    print("  5. Skip for now (come back later)")
    print("  6. Show full reasoning for all labels")

    while True:
        choice = input("\nYour choice (1-6): ").strip()

        if choice == "1":
            return {
                "action": "keep_canonical",
                "labels_to_add": [],
            }

        elif choice == "2":
            can_labels = set(l["label"] for l in conflict["canonical_labels"])
            dup_labels = set(l["label"] for l in conflict["duplicate_labels"])
            missing = dup_labels - can_labels

            if not missing:
                print("  No missing labels to add!")
                continue

            labels_to_add = [
                l
                for l in conflict["duplicate_labels"]
                if l["label"] in missing
            ]

            return {
                "action": "add_missing",
                "labels_to_add": labels_to_add,
            }

        elif choice == "3":
            return {
                "action": "replace_with_duplicate",
                "labels_to_add": conflict["duplicate_labels"],
            }

        elif choice == "4":
            print("\nAvailable labels:")
            all_labels = {}
            for i, label in enumerate(conflict["canonical_labels"], 1):
                print(f"  C{i}. {label['label']} (canonical)")
                all_labels[f"C{i}"] = label
            for i, label in enumerate(conflict["duplicate_labels"], 1):
                print(f"  D{i}. {label['label']} (duplicate)")
                all_labels[f"D{i}"] = label

            selected = input(
                "\nEnter labels to keep/add (comma-separated, e.g., C1,D2): "
            ).strip()

            labels_to_add = []
            for sel in selected.split(","):
                sel = sel.strip()
                if sel in all_labels:
                    labels_to_add.append(all_labels[sel])
                else:
                    print(f"  Invalid selection: {sel}")

            return {
                "action": "custom",
                "labels_to_add": labels_to_add,
            }

        elif choice == "5":
            return {
                "action": "skip",
                "labels_to_add": [],
            }

        elif choice == "6":
            print("\n" + "=" * 80)
            print("FULL REASONING:")
            print("=" * 80)
            print("\nCANONICAL:")
            for label in conflict["canonical_labels"]:
                print(f"\n  {label['label']}:")
                print(f"    {label.get('reasoning', 'No reasoning')}")
            print("\nDUPLICATE:")
            for label in conflict["duplicate_labels"]:
                print(f"\n  {label['label']}:")
                print(f"    {label.get('reasoning', 'No reasoning')}")
            continue

        else:
            print("  Invalid choice. Please enter 1-6.")


def resolve_conflicts_interactive(
    label_report: Dict,
    resume_from: Optional[str] = None,
    start_receipt: Optional[str] = None,
) -> Dict:
    """Interactively resolve conflicts."""
    conflicts_by_receipt = load_conflicts_by_receipt(label_report)

    # Load existing resolutions if resuming
    resolutions = {}
    if resume_from and Path(resume_from).exists():
        with open(resume_from, "r") as f:
            resolutions = json.load(f)
        print(f"Resuming from {resume_from} with {len(resolutions)} existing resolutions")

    # Count total conflicts
    total_conflicts = sum(
        sum(len(confs) for confs in receipt_conflicts.values())
        for receipt_conflicts in conflicts_by_receipt.values()
    )

    resolved_count = len(resolutions)
    print(f"\nTotal conflicts: {total_conflicts}")
    print(f"Already resolved: {resolved_count}")
    print(f"Remaining: {total_conflicts - resolved_count}")

    # Process by receipt
    receipt_ids = sorted(conflicts_by_receipt.keys(), key=int)
    if start_receipt:
        try:
            start_idx = receipt_ids.index(start_receipt)
            receipt_ids = receipt_ids[start_idx:]
        except ValueError:
            print(f"Warning: Receipt {start_receipt} not found, starting from beginning")

    conflict_num = resolved_count + 1

    for receipt_id in receipt_ids:
        print(f"\n{'=' * 80}")
        print(f"RECEIPT {receipt_id}")
        print(f"{'=' * 80}")

        receipt_conflicts = conflicts_by_receipt[receipt_id]
        receipt_total = sum(len(confs) for confs in receipt_conflicts.values())

        for canonical_id, conflicts in receipt_conflicts.items():
            for conflict in conflicts:
                # Create unique key for this conflict
                conflict_key = (
                    f"{receipt_id}_{canonical_id}_{conflict['line_id']}_"
                    f"{conflict['word_id']}_{conflict['source_image_id']}"
                )

                # Skip if already resolved
                if conflict_key in resolutions:
                    continue

                # Add metadata
                conflict["receipt_id"] = receipt_id
                conflict["canonical_image_id"] = canonical_id

                # Display and get decision
                display_conflict(conflict, conflict_num, total_conflicts)
                decision = get_user_decision(conflict)

                if decision["action"] == "skip":
                    print("\n⏭️  Skipped - you can come back to this later")
                    continue

                # Save resolution
                resolutions[conflict_key] = {
                    "receipt_id": receipt_id,
                    "canonical_image_id": canonical_id,
                    "line_id": conflict["line_id"],
                    "word_id": conflict["word_id"],
                    "source_image_id": conflict["source_image_id"],
                    "decision": decision,
                    "conflict_data": conflict,
                }

                conflict_num += 1
                resolved_count += 1

                print(f"\n✅ Resolved! ({resolved_count}/{total_conflicts})")

                # Auto-save after each resolution
                save_path = Path("conflict_resolutions.json")
                with open(save_path, "w") as f:
                    json.dump(resolutions, f, indent=2, default=str)
                print(f"💾 Auto-saved to {save_path}")

                # Ask if user wants to continue
                if resolved_count < total_conflicts:
                    cont = input("\nContinue? (y/n): ").strip().lower()
                    if cont != "y":
                        print("\nPaused. Resume with --resume-from conflict_resolutions.json")
                        return resolutions

    print(f"\n🎉 All conflicts resolved! ({resolved_count}/{total_conflicts})")
    return resolutions


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

        if decision["action"] == "skip":
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
                    logger.info(
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
        description="Interactively resolve conflicts"
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
        "--resume-from",
        default="conflict_resolutions.json",
        help="Resume from saved resolutions file",
    )
    parser.add_argument(
        "--start-receipt",
        help="Start from a specific receipt ID",
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

    args = parser.parse_args()

    try:
        # Load label report
        with open(args.label_report, "r") as f:
            label_report = json.load(f)

        # Resolve conflicts
        resolutions = resolve_conflicts_interactive(
            label_report,
            resume_from=args.resume_from if Path(args.resume_from).exists() else None,
            start_receipt=args.start_receipt,
        )

        # Save final resolutions
        save_path = Path("conflict_resolutions.json")
        with open(save_path, "w") as f:
            json.dump(resolutions, f, indent=2, default=str)
        print(f"\n💾 Resolutions saved to {save_path}")

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

            results = apply_resolutions(client, resolutions, dry_run=args.dry_run)

            print(f"\n✅ Applied {results['labels_added']} labels")
            if results["errors"]:
                print(f"⚠️  {len(results['errors'])} errors occurred")

    except KeyboardInterrupt:
        print("\n\n⏸️  Interrupted. Your progress has been saved.")
        print("Resume with: --resume-from conflict_resolutions.json")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


