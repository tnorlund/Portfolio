#!/usr/bin/env python3
"""
Download all 47 production receipts for comprehensive local testing.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.append("..")

from receipt_dynamo import DynamoClient

# Import the export functionality directly
sys.path.append("../scripts")
from export_receipt_data import export_image


async def download_all_receipts(
    output_dir: str = "./receipt_data_full", limit: int = None
):
    """Download all production receipts."""

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    print("📥 DOWNLOADING ALL PRODUCTION RECEIPTS")
    print("=" * 70)

    table_name = "ReceiptsTable-d7ff76a"

    # Get all receipt IDs from the table
    print(f"🔍 Querying {table_name} for all images...")
    client = DynamoClient(table_name)

    # Use list_images with no limit to get all receipts
    images, _ = client.list_images(limit=limit)
    receipt_ids = [img.image_id for img in images]

    print(f"📋 Found {len(receipt_ids)} receipts in {table_name}")
    print(f"📁 Output directory: {output_path.absolute()}\n")

    success_count = 0
    failed_ids = []

    for i, receipt_id in enumerate(receipt_ids, 1):
        print(f"\n[{i}/{len(receipt_ids)}] Processing {receipt_id}...")

        try:
            export_image(table_name, receipt_id, str(output_path))
            print(f"   ✅ Successfully exported")
            success_count += 1
        except Exception as e:
            print(f"   ❌ Failed: {e}")
            failed_ids.append(receipt_id)

    print(f"\n{'='*70}")
    print(f"📊 DOWNLOAD SUMMARY")
    print(f"{'='*70}")
    print(f"✅ Successfully downloaded: {success_count}/{len(receipt_ids)}")

    if failed_ids:
        print(f"❌ Failed downloads: {len(failed_ids)}")
        for fid in failed_ids[:5]:  # Show first 5
            print(f"   - {fid}")
        if len(failed_ids) > 5:
            print(f"   ... and {len(failed_ids) - 5} more")

    # Create manifest file
    manifest = {
        "total_receipts": len(receipt_ids),
        "successful_downloads": success_count,
        "failed_downloads": len(failed_ids),
        "receipt_ids": receipt_ids[
            :100
        ],  # Only store first 100 IDs to keep file size manageable
        "failed_ids": failed_ids,
        "table_name": table_name,
        "note": f"Downloaded all {len(receipt_ids)} receipts from production table",
    }

    manifest_path = output_path / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n📄 Manifest saved to: {manifest_path}")

    return success_count, failed_ids


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download all production receipts"
    )
    parser.add_argument(
        "--output-dir",
        default="./receipt_data_full",
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
        download_all_receipts(args.output_dir, args.limit)
    )

    # Exit with error code if any downloads failed
    sys.exit(0 if len(failed) == 0 else 1)
