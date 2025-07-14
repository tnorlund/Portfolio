#!/usr/bin/env python3
"""
Download production receipts with validated labels included.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.append("..")

from receipt_dynamo import DynamoClient

from scripts.export_receipt_with_labels_fixed import export_image_with_labels


async def download_receipts_with_labels(
    output_dir: str = "./receipt_data_with_labels", limit: int = None
):
    """Download production receipts including validated labels."""

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    print("📥 DOWNLOADING PRODUCTION RECEIPTS WITH LABELS")
    print("=" * 70)

    table_name = "ReceiptsTable-d7ff76a"

    # Get all receipt IDs from the table
    print(f"🔍 Querying {table_name} for images...")
    client = DynamoClient(table_name)

    # Use list_images with limit
    images, _ = client.list_images(limit=limit)
    receipt_ids = [img.image_id for img in images]

    print(f"📋 Found {len(receipt_ids)} receipts in {table_name}")
    print(f"📁 Output directory: {output_path.absolute()}\n")

    success_count = 0
    failed_ids = []
    receipts_with_labels = 0
    total_labels = 0

    for i, receipt_id in enumerate(receipt_ids, 1):
        print(f"\n[{i}/{len(receipt_ids)}] Processing {receipt_id}...")

        try:
            export_image_with_labels(table_name, receipt_id, str(output_path))

            # Check if this receipt has labels
            output_file = output_path / f"{receipt_id}.json"
            with open(output_file, "r") as f:
                data = json.load(f)
                label_count = len(data.get("receipt_word_labels", []))
                if label_count > 0:
                    receipts_with_labels += 1
                    total_labels += label_count
                    print(
                        f"   ✅ Successfully exported with {label_count} labels"
                    )
                else:
                    print(f"   ⚠️  Successfully exported but no labels found")

            success_count += 1

        except Exception as e:
            print(f"   ❌ Failed: {e}")
            failed_ids.append(receipt_id)

    print(f"\n{'='*70}")
    print(f"📊 DOWNLOAD SUMMARY")
    print(f"{'='*70}")
    print(f"✅ Successfully downloaded: {success_count}/{len(receipt_ids)}")
    print(
        f"🏷️  Receipts with labels: {receipts_with_labels}/{success_count} ({receipts_with_labels/max(1, success_count)*100:.1f}%)"
    )
    print(f"📝 Total labels: {total_labels}")

    if receipts_with_labels > 0:
        print(
            f"📊 Average labels per labeled receipt: {total_labels/receipts_with_labels:.1f}"
        )

    if failed_ids:
        print(f"\n❌ Failed downloads: {len(failed_ids)}")
        for fid in failed_ids[:5]:  # Show first 5
            print(f"   - {fid}")
        if len(failed_ids) > 5:
            print(f"   ... and {len(failed_ids) - 5} more")

    # Create manifest file
    manifest = {
        "total_receipts": len(receipt_ids),
        "successful_downloads": success_count,
        "receipts_with_labels": receipts_with_labels,
        "total_labels": total_labels,
        "failed_downloads": len(failed_ids),
        "receipt_ids": receipt_ids[:100],  # Only store first 100 IDs
        "failed_ids": failed_ids,
        "table_name": table_name,
        "note": f"Downloaded {len(receipt_ids)} receipts with {total_labels} total labels",
    }

    manifest_path = output_path / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n📄 Manifest saved to: {manifest_path}")

    return success_count, failed_ids


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download receipts with labels"
    )
    parser.add_argument(
        "--output-dir",
        default="./receipt_data_with_labels",
        help="Output directory for receipt data",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of receipts to download (default: all)",
    )

    args = parser.parse_args()

    success, failed = asyncio.run(
        download_receipts_with_labels(args.output_dir, args.limit)
    )

    # Exit with error code if any downloads failed
    sys.exit(0 if len(failed) == 0 else 1)
