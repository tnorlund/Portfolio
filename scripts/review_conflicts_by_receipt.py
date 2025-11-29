#!/usr/bin/env python3
"""
Review conflicts for a specific receipt ID.

Usage:
    # Review all receipts
    python scripts/review_conflicts_by_receipt.py

    # Review a specific receipt
    python scripts/review_conflicts_by_receipt.py --receipt-id 1

    # Review with more details
    python scripts/review_conflicts_by_receipt.py --receipt-id 1 --verbose
"""

import argparse
import json
from pathlib import Path


def review_receipt_conflicts(receipt_id: str, conflicts_data: dict, verbose: bool = False):
    """Review conflicts for a specific receipt."""
    if receipt_id not in conflicts_data:
        print(f"No conflicts found for Receipt {receipt_id}")
        return

    receipt_conflicts = conflicts_data[receipt_id]
    total_conflicts = sum(len(confs) for confs in receipt_conflicts.values())

    print("\n" + "=" * 80)
    print(f"RECEIPT {receipt_id} - CONFLICT REVIEW")
    print("=" * 80)
    print(f"\nTotal conflicts: {total_conflicts}")
    print(f"Affected canonical images: {len(receipt_conflicts)}")

    for canonical_id, conflicts in receipt_conflicts.items():
        print(f"\n{'─' * 80}")
        print(f"Canonical Image: {canonical_id}")
        print(f"Conflicts: {len(conflicts)}")
        print(f"{'─' * 80}")

        for i, conflict in enumerate(conflicts, 1):
            print(f"\n  Conflict {i}/{len(conflicts)}: Line {conflict['line_id']}, Word {conflict['word_id']}")
            print(f"    SHA256: {conflict['sha256'][:16]}...")
            print(f"    Source (duplicate image): {conflict['source_image_id']}")

            print(f"\n    Canonical labels ({len(conflict['canonical_labels'])}):")
            for label in conflict['canonical_labels']:
                print(f"      • {label['label']}")
                if verbose and label.get('reasoning'):
                    print(f"        Reasoning: {label['reasoning'][:150]}...")
                if label.get('validation_status'):
                    print(f"        Status: {label['validation_status']}")

            print(f"\n    Duplicate labels ({len(conflict['duplicate_labels'])}):")
            for label in conflict['duplicate_labels']:
                print(f"      • {label['label']}")
                if verbose and label.get('reasoning'):
                    print(f"        Reasoning: {label['reasoning'][:150]}...")
                if label.get('validation_status'):
                    print(f"        Status: {label['validation_status']}")

            # Show difference
            can_labels = set(l['label'] for l in conflict['canonical_labels'])
            dup_labels = set(l['label'] for l in conflict['duplicate_labels'])
            only_in_dup = dup_labels - can_labels
            only_in_can = can_labels - dup_labels

            if only_in_dup:
                print(f"\n    ⚠️  Labels only in duplicate: {sorted(only_in_dup)}")
            if only_in_can:
                print(f"    ⚠️  Labels only in canonical: {sorted(only_in_can)}")
            if can_labels == dup_labels:
                print(f"\n    ℹ️  Same labels, different reasoning/metadata")


def main():
    parser = argparse.ArgumentParser(
        description="Review conflicts organized by receipt"
    )
    parser.add_argument(
        "--receipt-id",
        type=str,
        help="Specific receipt ID to review (default: show all)",
    )
    parser.add_argument(
        "--conflicts-file",
        default="label_merge/conflicts_by_receipt.json",
        help="Path to conflicts_by_receipt.json",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed reasoning for each label",
    )

    args = parser.parse_args()

    # Load conflicts
    conflicts_path = Path(args.conflicts_file)
    if not conflicts_path.exists():
        print(f"Error: Conflicts file not found: {conflicts_path}")
        return

    with open(conflicts_path, "r") as f:
        conflicts_data = json.load(f)

    # Show summary
    print("=" * 80)
    print("CONFLICTS BY RECEIPT - SUMMARY")
    print("=" * 80)
    print(f"\nTotal receipts with conflicts: {len(conflicts_data)}")

    for receipt_id in sorted(conflicts_data.keys(), key=int):
        receipt_conflicts = conflicts_data[receipt_id]
        total_conflicts = sum(len(confs) for confs in receipt_conflicts.values())
        print(f"  Receipt {receipt_id}: {total_conflicts} conflicts across {len(receipt_conflicts)} canonical images")

    # Review specific receipt or all
    if args.receipt_id:
        if args.receipt_id in conflicts_data:
            review_receipt_conflicts(args.receipt_id, conflicts_data, args.verbose)
        else:
            print(f"\nError: Receipt {args.receipt_id} not found in conflicts")
            print(f"Available receipt IDs: {sorted(conflicts_data.keys(), key=int)}")
    else:
        print("\n" + "=" * 80)
        print("To review a specific receipt, use:")
        print("  python scripts/review_conflicts_by_receipt.py --receipt-id 1")
        print("  python scripts/review_conflicts_by_receipt.py --receipt-id 2")
        print("\nFor detailed reasoning, add --verbose")


if __name__ == "__main__":
    main()


