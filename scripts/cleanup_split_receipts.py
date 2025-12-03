#!/usr/bin/env python3
"""
Delete split receipts and restore original receipt 1.

This script:
1. Deletes split receipts (keeps original receipts 2, 3 if they existed before)
2. Restores receipt 1 from exported data

Usage:
    python scripts/cleanup_split_receipts.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --export-dir ./exported_image_data \
        --split-receipt-ids 4 5 6 7
"""

import argparse
import os
import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordLabel,
    ReceiptMetadata,
)


def setup_environment() -> dict:
    """Load environment variables."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    try:
        from receipt_dynamo.data._pulumi import load_env
        project_root = Path(__file__).parent.parent
        infra_dir = project_root / "infra"
        env = load_env("dev", working_dir=str(infra_dir))

        if not table_name:
            table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    except Exception as e:
        print(f"⚠️  Could not load from Pulumi: {e}")
        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME must be set")

    return {"table_name": table_name}


def delete_receipt_and_subrecords(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    dry_run: bool = False,
) -> None:
    """Delete a receipt and all its subrecords."""
    print(f"\n🗑️  Deleting receipt {receipt_id}...")

    if dry_run:
        print(f"   [DRY RUN] Would delete receipt {receipt_id}")
        return

    try:
        # Get receipt data
        receipt = client.get_receipt(image_id, receipt_id)
        receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)

        # Get labels
        receipt_labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)

        # Get letters (if any)
        try:
            receipt_letters = client.list_receipt_letters_from_image_and_receipt(image_id, receipt_id)
        except Exception:
            receipt_letters = []

        print(f"   Found: {len(receipt_lines)} lines, {len(receipt_words)} words, {len(receipt_labels)} labels")

        # Delete in reverse order of creation
        # 1. Delete labels
        if receipt_labels:
            print(f"   Deleting {len(receipt_labels)} labels...")
            # Use batch delete if available, otherwise delete individually
            try:
                client.delete_receipt_word_labels(receipt_labels)
                print(f"      ✅ Deleted labels")
            except Exception as e:
                # Fallback to individual deletion
                print(f"      ⚠️  Batch delete failed, trying individual: {e}")
                for label in receipt_labels:
                    try:
                        client.delete_receipt_word_label(label)
                    except Exception as e2:
                        print(f"      ⚠️  Error deleting label: {e2}")

        # 2. Delete words
        if receipt_words:
            print(f"   Deleting {len(receipt_words)} words...")
            client.delete_receipt_words(receipt_words)

        # 3. Delete lines
        if receipt_lines:
            print(f"   Deleting {len(receipt_lines)} lines...")
            client.delete_receipt_lines(receipt_lines)

        # 4. Delete letters
        if receipt_letters:
            print(f"   Deleting {len(receipt_letters)} letters...")
            try:
                client.delete_receipt_letters(receipt_letters)
            except AttributeError:
                print(f"      ⚠️  Letter deletion not supported")

        # 5. Delete metadata
        try:
            metadata = client.get_receipt_metadata(image_id, receipt_id)
            if metadata:
                print(f"   Deleting metadata...")
                client.delete_receipt_metadata(image_id, receipt_id)
        except Exception:
            pass

        # 6. Delete compaction runs
        try:
            runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
            if runs:
                print(f"   Deleting {len(runs)} compaction runs...")
                for run in runs:
                    try:
                        client.delete_compaction_run(image_id, receipt_id, run.run_id)
                    except Exception as e:
                        print(f"      ⚠️  Error deleting compaction run: {e}")
        except Exception:
            pass

        # 7. Delete receipt
        print(f"   Deleting receipt...")
        client.delete_receipt(receipt)
        print(f"   ✅ Receipt {receipt_id} deleted")

    except Exception as e:
        print(f"   ❌ Error deleting receipt {receipt_id}: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        description="Delete split receipts and restore original receipt 1"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID",
    )
    parser.add_argument(
        "--split-receipt-ids",
        type=int,
        nargs="+",
        required=True,
        help="Receipt IDs to delete (the split receipts)",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("./exported_image_data"),
        help="Directory containing exported data for rollback",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode - don't make any changes",
    )

    args = parser.parse_args()

    config = setup_environment()
    client = DynamoClient(config["table_name"])

    print(f"🧹 Cleaning up split receipts for image {args.image_id}")
    print("=" * 60)

    if args.dry_run:
        print("\n🔍 DRY RUN MODE - No changes will be made\n")

    # Delete split receipts
    for receipt_id in sorted(args.split_receipt_ids, reverse=True):
        delete_receipt_and_subrecords(
            client,
            args.image_id,
            receipt_id,
            args.dry_run,
        )

    # Check if receipt 1 exists and needs to be deleted first
    if not args.dry_run:
        try:
            existing_receipt = client.get_receipt(args.image_id, 1)
            print(f"\n⚠️  Receipt 1 already exists - deleting it first...")
            delete_receipt_and_subrecords(
                client,
                args.image_id,
                1,
                args.dry_run,
            )
        except Exception:
            print(f"\n✅ Receipt 1 does not exist - will restore from export")

    # Restore receipt 1
    if not args.dry_run:
        print(f"\n🔄 Restoring receipt 1...")
        print("=" * 60)

        # Use the rollback script
        import subprocess
        rollback_cmd = [
            sys.executable,
            "scripts/rollback_split_receipt.py",
            "--image-id", args.image_id,
            "--receipt-id", "1",
            "--export-dir", str(args.export_dir),
        ]

        print(f"Running: {' '.join(rollback_cmd)}")
        result = subprocess.run(rollback_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"❌ Rollback failed!")
            print(result.stderr)
            sys.exit(1)

        print(result.stdout)

    print(f"\n✅ Cleanup complete!")


if __name__ == "__main__":
    main()

