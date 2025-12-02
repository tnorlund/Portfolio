#!/usr/bin/env python3
"""
Rollback script for split receipt operation.

Deletes new receipts and sub-records in correct order.
Note: ChromaDB embeddings will remain orphaned (not automatically deleted).

Usage:
    python scripts/rollback_split_receipt.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --receipt-ids 1 2 \
        --dry-run
"""

import argparse
import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient
from scripts.split_receipt import setup_environment


def rollback_split_receipt(
    client: DynamoClient,
    image_id: str,
    new_receipt_ids: list[int],
    dry_run: bool = True
):
    """Rollback split receipt by deleting new receipts in correct order."""

    print(f"🔄 Rolling back split receipt for image: {image_id}")
    print(f"   Receipt IDs to delete: {new_receipt_ids}")
    print(f"   Dry run: {dry_run}")

    if dry_run:
        print("\n⚠️  DRY RUN - No changes will be made")

    for receipt_id in new_receipt_ids:
        print(f"\n📋 Processing receipt {receipt_id}...")

        # First, list all details to verify what we're deleting
        print(f"   📊 Listing all details for receipt {receipt_id}...")
        try:
            receipt = client.get_receipt(image_id, receipt_id)
            if receipt:
                print(f"      Receipt: {receipt.receipt_id} ({receipt.width}x{receipt.height})")
            else:
                print(f"      ⚠️  Receipt {receipt_id} not found")
        except Exception as e:
            print(f"      ⚠️  Error getting receipt: {e}")

        try:
            lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
            print(f"      Lines: {len(lines)}")
        except Exception as e:
            print(f"      ⚠️  Error listing lines: {e}")
            lines = []

        try:
            words = client.list_receipt_words_from_receipt(image_id, receipt_id)
            print(f"      Words: {len(words)}")
        except Exception as e:
            print(f"      ⚠️  Error listing words: {e}")
            words = []

        try:
            labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
            print(f"      Labels: {len(labels)}")
        except Exception as e:
            print(f"      ⚠️  Error listing labels: {e}")
            labels = []

        try:
            letters = client.list_receipt_letters_from_receipt(image_id, receipt_id)
            print(f"      Letters: {len(letters)}")
        except (AttributeError, Exception) as e:
            letters = []
            # Letters might not be supported

        try:
            metadata = client.get_receipt_metadata(image_id, receipt_id)
            if metadata:
                print(f"      Metadata: Found")
        except Exception:
            pass

        try:
            runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
            print(f"      CompactionRuns: {len(runs)}")
        except Exception as e:
            runs = []

        print(f"   ✅ Total items to delete: {len(lines)} lines, {len(words)} words, {len(labels)} labels, {len(letters)} letters, {len(runs)} compaction runs")

        if dry_run:
            print(f"   ⚠️  DRY RUN - Would delete all items above")
            continue

        # 1. Delete labels (references words)
        try:
            labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
            print(f"   Found {len(labels)} labels")
            if not dry_run and labels:
                # Delete in batches
                for i in range(0, len(labels), 25):
                    batch = labels[i:i+25]
                    client.delete_receipt_word_labels(batch)
                print(f"   ✅ Deleted {len(labels)} labels")
        except Exception as e:
            print(f"   ⚠️  Error listing/deleting labels: {e}")

        # 2. Delete words (references lines)
        try:
            words = client.list_receipt_words_from_receipt(image_id, receipt_id)
            print(f"   Found {len(words)} words")
            if not dry_run and words:
                # Delete in batches
                for i in range(0, len(words), 25):
                    batch = words[i:i+25]
                    client.delete_receipt_words(batch)
                print(f"   ✅ Deleted {len(words)} words")
        except Exception as e:
            print(f"   ⚠️  Error listing/deleting words: {e}")

        # 3. Delete lines (references receipt)
        try:
            lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
            print(f"   Found {len(lines)} lines")
            if not dry_run and lines:
                # Delete in batches
                for i in range(0, len(lines), 25):
                    batch = lines[i:i+25]
                    client.delete_receipt_lines(batch)
                print(f"   ✅ Deleted {len(lines)} lines")
        except Exception as e:
            print(f"   ⚠️  Error listing/deleting lines: {e}")

        # 4. Delete letters (if any, references words)
        try:
            letters = client.list_receipt_letters_from_receipt(image_id, receipt_id)
            print(f"   Found {len(letters)} letters")
            if not dry_run and letters:
                # Delete in batches
                for i in range(0, len(letters), 25):
                    batch = letters[i:i+25]
                    client.delete_receipt_letters(batch)
                print(f"   ✅ Deleted {len(letters)} letters")
        except (AttributeError, Exception) as e:
            # Letters might not be supported or might not exist
            pass

        # 5. Delete metadata (if any, references receipt)
        try:
            metadata = client.get_receipt_metadata(image_id, receipt_id)
            if metadata:
                print(f"   Found metadata")
                if not dry_run:
                    client.delete_receipt_metadata(image_id, receipt_id)
                    print(f"   ✅ Deleted metadata")
        except Exception:
            # Metadata might not exist
            pass

        # 6. Delete CompactionRun (if any, references receipt)
        try:
            runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
            print(f"   Found {len(runs)} compaction runs")
            if not dry_run and runs:
                for run in runs:
                    client.delete_compaction_run(image_id, receipt_id, run.run_id)
                print(f"   ✅ Deleted {len(runs)} compaction runs")
        except Exception as e:
            print(f"   ⚠️  Error listing/deleting compaction runs: {e}")

        # 7. Delete receipt (top-level entity)
        try:
            receipt = client.get_receipt(image_id, receipt_id)
            if receipt:
                print(f"   Found receipt")
                if not dry_run:
                    client.delete_receipt(receipt)
                    print(f"   ✅ Deleted receipt {receipt_id}")
        except Exception as e:
            print(f"   ⚠️  Error getting/deleting receipt: {e}")

    print(f"\n✅ Rollback complete!")
    print(f"⚠️  Note: ChromaDB embeddings remain orphaned (not automatically deleted)")
    print(f"   To clean ChromaDB, manually delete embeddings with IDs matching:")
    print(f"   IMAGE#{image_id}#RECEIPT#{{receipt_id:05d}}#...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rollback split receipt operation")
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument("--receipt-ids", required=True, nargs="+", type=int, help="Receipt IDs to delete")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    args = parser.parse_args()

    # Setup client
    config = setup_environment()
    client = DynamoClient(config["table_name"])

    rollback_split_receipt(client, args.image_id, args.receipt_ids, args.dry_run)


