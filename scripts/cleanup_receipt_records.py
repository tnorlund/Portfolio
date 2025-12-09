#!/usr/bin/env python3
"""
Delete specific receipt records (and subrecords) from DynamoDB.

This removes, per receipt ID:
- ReceiptWordLabels
- ReceiptWords
- ReceiptLines
- ReceiptLetters (best effort)
- ReceiptMetadata (best effort)
- CompactionRuns (best effort)
- The Receipt itself (last)

Usage example:
  python scripts/cleanup_receipt_records.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --receipt-ids 2 3 \
    --table-name ReceiptsTable-dc5be22

Flags:
- --dry-run : print actions but do not delete
"""

import argparse
import sys
from pathlib import Path
from typing import List

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient  # noqa: E402


def delete_receipt_and_children(
    client: DynamoClient, image_id: str, receipt_id: int, dry_run: bool
) -> None:
    print(f"\n🗑️  Deleting receipt {receipt_id} for image {image_id}...")

    # Fetch details
    try:
        receipt = client.get_receipt(image_id, receipt_id)
    except Exception as e:
        print(f"   ⚠️  Receipt not found: {e}")
        return

    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id) or []
    words = client.list_receipt_words_from_receipt(image_id, receipt_id) or []
    try:
        labels, _ = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )
    except Exception:
        labels = []
    try:
        letters = client.list_receipt_letters_from_image_and_receipt(
            image_id, receipt_id
        )
    except Exception:
        letters = []

    print(
        f"   Found: {len(lines)} lines, {len(words)} words, "
        f"{len(labels)} labels, {len(letters)} letters"
    )

    if dry_run:
        print("   [DRY RUN] Skipping deletions")
        return

    # Delete labels
    if labels:
        try:
            client.delete_receipt_word_labels(labels)
            print("   ✅ Deleted labels")
        except Exception as e:
            print(f"   ⚠️  Batch delete labels failed: {e}")
            for lbl in labels:
                try:
                    client.delete_receipt_word_label(lbl)
                except Exception as e2:
                    print(f"      ⚠️  Failed deleting label: {e2}")

    # Delete words
    if words:
        client.delete_receipt_words(words)
        print("   ✅ Deleted words")

    # Delete lines
    if lines:
        client.delete_receipt_lines(lines)
        print("   ✅ Deleted lines")

    # Delete letters (best effort)
    if letters:
        try:
            client.delete_receipt_letters(letters)
            print("   ✅ Deleted letters")
        except Exception as e:
            print(f"   ⚠️  Failed deleting letters: {e}")

    # Delete metadata (best effort)
    try:
        md = client.get_receipt_metadata(image_id, receipt_id)
        if md:
            client.delete_receipt_metadata(image_id, receipt_id)
            print("   ✅ Deleted metadata")
    except Exception:
        pass

    # Delete compaction runs (best effort)
    try:
        runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
        for r in runs:
            client.delete_compaction_run(r)
        if runs:
            print(f"   ✅ Deleted {len(runs)} compaction runs")
    except Exception:
        pass

    # Finally, delete receipt
    try:
        client.delete_receipt(receipt)
        print(f"   ✅ Deleted receipt {receipt_id}")
    except Exception as e:
        print(f"   ❌ Error deleting receipt {receipt_id}: {e}")


def main():
    ap = argparse.ArgumentParser(
        description="Delete receipt records (and subrecords) from DynamoDB."
    )
    ap.add_argument("--image-id", required=True)
    ap.add_argument(
        "--receipt-ids",
        type=int,
        nargs="+",
        required=True,
        help="Receipt IDs to delete",
    )
    ap.add_argument(
        "--table-name",
        default="ReceiptsTable-dc5be22",
        help="DynamoDB table name (default: ReceiptsTable-dc5be22)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without deleting",
    )
    args = ap.parse_args()

    client = DynamoClient(args.table_name)

    # Process receipts in descending order (delete highest IDs first)
    for rid in sorted(args.receipt_ids, reverse=True):
        delete_receipt_and_children(client, args.image_id, rid, args.dry_run)


if __name__ == "__main__":
    main()
