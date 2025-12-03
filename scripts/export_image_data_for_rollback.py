#!/usr/bin/env python3
"""
Export all DynamoDB records for specific images for testing and rollback.

This script exports:
- Image entity
- All Receipt entities
- All ReceiptLine entities
- All ReceiptWord entities
- All ReceiptLetter entities
- All ReceiptWordLabel entities
- All ReceiptMetadata entities
- All CompactionRun entities

Usage:
    python scripts/export_image_data_for_rollback.py \
        --image-ids 13da1048-3888-429f-b2aa-b3e15341da5e 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
        --output-dir ./exported_data
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Image,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordLabel,
    ReceiptMetadata,
    CompactionRun,
)


def setup_environment() -> Dict[str, str]:
    """Load environment variables and return configuration dict."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    # Try loading from Pulumi if not set
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
            raise ValueError("DYNAMODB_TABLE_NAME must be set in environment or available from Pulumi")

    return {
        "table_name": table_name,
    }


def export_image_data(
    client: DynamoClient,
    image_id: str,
    output_dir: Path,
) -> None:
    """Export all data for an image to JSON files."""
    image_dir = output_dir / image_id
    image_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n📥 Exporting data for image: {image_id}")

    # Use get_image_details to get everything in one call
    try:
        details = client.get_image_details(image_id)
        print(f"   ✅ Image: {details.images[0].width}x{details.images[0].height}" if details.images else "   ⚠️  No image found")
        print(f"   📋 Found {len(details.receipts)} receipts")
        print(f"   📏 Found {len(details.lines)} image-level lines")
        print(f"   🔤 Found {len(details.words)} image-level words")
        print(f"   🔠 Found {len(details.letters)} image-level letters")
    except Exception as e:
        print(f"   ⚠️  Error loading image details: {e}")
        return

    if not details.images:
        print(f"   ⚠️  No image found for {image_id}")
        return

    # Export Image entity
    with open(image_dir / "image.json", "w", encoding="utf-8") as f:
        json.dump([dict(img) for img in details.images], f, indent=2, default=str)

    # Export image-level OCR entities
    if details.lines:
        with open(image_dir / "lines.json", "w", encoding="utf-8") as f:
            json.dump([dict(line) for line in details.lines], f, indent=2, default=str)
        print(f"   ✅ Exported {len(details.lines)} image-level lines")

    if details.words:
        with open(image_dir / "words.json", "w", encoding="utf-8") as f:
            json.dump([dict(word) for word in details.words], f, indent=2, default=str)
        print(f"   ✅ Exported {len(details.words)} image-level words")

    if details.letters:
        with open(image_dir / "letters.json", "w", encoding="utf-8") as f:
            json.dump([dict(letter) for letter in details.letters], f, indent=2, default=str)
        print(f"   ✅ Exported {len(details.letters)} image-level letters")

    # Export OCR jobs and routing decisions
    if details.ocr_jobs:
        with open(image_dir / "ocr_jobs.json", "w", encoding="utf-8") as f:
            json.dump([dict(job) for job in details.ocr_jobs], f, indent=2, default=str)
        print(f"   ✅ Exported {len(details.ocr_jobs)} OCR jobs")

    if details.ocr_routing_decisions:
        with open(image_dir / "ocr_routing_decisions.json", "w", encoding="utf-8") as f:
            json.dump([dict(decision) for decision in details.ocr_routing_decisions], f, indent=2, default=str)
        print(f"   ✅ Exported {len(details.ocr_routing_decisions)} OCR routing decisions")

    if not details.receipts:
        print(f"   ⚠️  No receipts found for image {image_id}")
        return

    receipts = details.receipts

    # Export receipts
    receipts_data = []
    for receipt in receipts:
        receipt_id = receipt.receipt_id
        receipts_data.append(dict(receipt))

        receipt_dir = image_dir / f"receipt_{receipt_id:05d}"
        receipt_dir.mkdir(exist_ok=True)

        print(f"\n   📄 Receipt {receipt_id}:")

        # Export receipt
        with open(receipt_dir / "receipt.json", "w", encoding="utf-8") as f:
            json.dump(dict(receipt), f, indent=2, default=str)

        # 3. Export ReceiptLines (filter from details)
        receipt_lines = [line for line in details.receipt_lines if line.receipt_id == receipt_id]
        if receipt_lines:
            with open(receipt_dir / "receipt_lines.json", "w", encoding="utf-8") as f:
                json.dump([dict(line) for line in receipt_lines], f, indent=2, default=str)
            print(f"      ✅ {len(receipt_lines)} lines")

        # 4. Export ReceiptWords (filter from details)
        receipt_words = [word for word in details.receipt_words if word.receipt_id == receipt_id]
        if receipt_words:
            with open(receipt_dir / "receipt_words.json", "w", encoding="utf-8") as f:
                json.dump([dict(word) for word in receipt_words], f, indent=2, default=str)
            print(f"      ✅ {len(receipt_words)} words")

        # 5. Export ReceiptLetters (filter from details)
        receipt_letters = [letter for letter in details.receipt_letters if letter.receipt_id == receipt_id]
        if receipt_letters:
            with open(receipt_dir / "receipt_letters.json", "w", encoding="utf-8") as f:
                json.dump([dict(letter) for letter in receipt_letters], f, indent=2, default=str)
            print(f"      ✅ {len(receipt_letters)} letters")

        # 6. Export ReceiptWordLabels (filter from details)
        receipt_labels = [label for label in details.receipt_word_labels if label.receipt_id == receipt_id]
        if receipt_labels:
            with open(receipt_dir / "receipt_word_labels.json", "w", encoding="utf-8") as f:
                json.dump([dict(label) for label in receipt_labels], f, indent=2, default=str)
            print(f"      ✅ {len(receipt_labels)} labels")

        # 7. Export ReceiptMetadata (filter from details)
        receipt_metadata_list = [meta for meta in details.receipt_metadatas if meta.receipt_id == receipt_id]
        if receipt_metadata_list:
            with open(receipt_dir / "receipt_metadata.json", "w", encoding="utf-8") as f:
                json.dump([dict(meta) for meta in receipt_metadata_list], f, indent=2, default=str)
            print(f"      ✅ Metadata")

        # 8. Export CompactionRun entities (need to query separately)
        try:
            compaction_runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
            if compaction_runs:
                with open(receipt_dir / "compaction_runs.json", "w", encoding="utf-8") as f:
                    json.dump([dict(run) for run in compaction_runs], f, indent=2, default=str)
                print(f"      ✅ {len(compaction_runs)} compaction runs")
        except Exception as e:
            # Compaction runs might not exist
            pass

    # Save summary
    summary = {
        "image_id": image_id,
        "image": dict(details.images[0]) if details.images else None,
        "receipts": receipts_data,
        "receipt_count": len(receipts),
        "image_level_ocr": {
            "lines": len(details.lines),
            "words": len(details.words),
            "letters": len(details.letters),
        },
        "ocr_jobs": len(details.ocr_jobs),
        "ocr_routing_decisions": len(details.ocr_routing_decisions),
    }
    with open(image_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n   ✅ Export complete for image {image_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Export all DynamoDB records for specific images for testing and rollback"
    )
    parser.add_argument(
        "--image-ids",
        required=True,
        nargs="+",
        help="Image IDs to export",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./exported_image_data"),
        help="Directory to save exported data (default: ./exported_image_data)",
    )

    args = parser.parse_args()

    # Setup environment
    config = setup_environment()
    client = DynamoClient(config["table_name"])

    print(f"📊 DynamoDB Table: {config['table_name']}")
    print(f"📁 Output Directory: {args.output_dir}")

    # Export data for each image
    for image_id in args.image_ids:
        export_image_data(client, image_id, args.output_dir)

    print(f"\n✅ Export complete!")
    print(f"   Exported {len(args.image_ids)} image(s)")
    print(f"   Data saved to: {args.output_dir}")
    print(f"\n💡 To rollback, use the JSON files in {args.output_dir} to recreate entities")


if __name__ == "__main__":
    main()

