#!/usr/bin/env python3
"""
Export receipt data with labels from DynamoDB for validation testing.
Fixed version that handles datetime serialization properly.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set up environment
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_dynamo.data.dynamo_client import DynamoClient


class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime and Decimal objects."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def export_image_fixed(
    table_name: str, image_id: str, output_dir: str
) -> None:
    """
    Exports all DynamoDB data related to an image as JSON with proper datetime handling.
    """
    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all data from DynamoDB
    details = dynamo_client.get_image_details(image_id)

    images = details.images
    lines = details.lines
    words = details.words
    letters = details.letters
    receipts = details.receipts
    receipt_lines = details.receipt_lines
    receipt_words = details.receipt_words
    receipt_letters = details.receipt_letters
    receipt_word_labels = details.receipt_word_labels
    receipt_metadatas = details.receipt_metadatas
    ocr_jobs = details.ocr_jobs
    ocr_routing_decisions = details.ocr_routing_decisions

    if not images:
        raise ValueError(f"No image found for image_id {image_id}")

    # Convert to dictionaries
    results = {
        "images": [dict(image) for image in images],
        "lines": [dict(line) for line in lines],
        "words": [dict(word) for word in words],
        "letters": [dict(letter) for letter in letters],
        "receipts": [dict(receipt) for receipt in receipts],
        "receipt_lines": [dict(line) for line in receipt_lines],
        "receipt_words": [dict(word) for word in receipt_words],
        "receipt_letters": [dict(letter) for letter in receipt_letters],
        "receipt_word_labels": [dict(label) for label in receipt_word_labels],
        "receipt_metadatas": [
            dict(metadata) for metadata in receipt_metadatas
        ],
        "ocr_jobs": [dict(job) for job in ocr_jobs],
        "ocr_routing_decisions": [
            {
                "image_id": decision.image_id,
                "job_id": decision.job_id,
                "s3_bucket": decision.s3_bucket,
                "s3_key": decision.s3_key,
                "created_at": decision.created_at,
                "updated_at": decision.updated_at,
                "receipt_count": decision.receipt_count,
                "status": decision.status,
            }
            for decision in ocr_routing_decisions
        ],
    }

    # Write with custom encoder
    with open(os.path.join(output_dir, f"{image_id}.json"), "w") as f:
        json.dump(results, f, indent=4, cls=CustomJSONEncoder)


def get_remaining_receipt_ids():
    """Get list of receipt IDs we haven't exported yet."""
    receipt_dir = Path("./receipt_data_with_labels")
    exported_dir = Path("./receipt_data_with_labels_full")

    if not receipt_dir.exists():
        return []

    all_receipt_ids = []
    exported_ids = set()

    # Get all receipt IDs
    for file_path in receipt_dir.glob("*.json"):
        if file_path.name not in [
            "manifest.json",
            "four_fields_simple_results.json",
            "metadata_impact_analysis.json",
            "pattern_vs_labels_comparison.json",
            "required_fields_analysis.json",
            "spatial_line_item_results_labeled.json",
        ]:
            all_receipt_ids.append(file_path.stem)

    # Get already exported IDs
    if exported_dir.exists():
        for file_path in exported_dir.glob("*.json"):
            exported_ids.add(file_path.stem)

    # Return IDs we haven't exported yet
    return [rid for rid in all_receipt_ids if rid not in exported_ids]


def export_remaining_receipts():
    """Export remaining receipts with proper datetime handling."""

    print("🔄 EXPORTING REMAINING RECEIPTS WITH LABELS")
    print("=" * 60)

    # Get table name from environment or use default
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    output_dir = "./receipt_data_with_labels_full"

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get remaining receipt IDs
    remaining_ids = get_remaining_receipt_ids()

    if not remaining_ids:
        print("✅ All receipts already exported!")
        return

    print(f"📋 Found {len(remaining_ids)} receipts to export")
    print(f"📂 Output directory: {output_dir}")
    print(f"🗄️  DynamoDB table: {table_name}")

    success_count = 0
    error_count = 0

    for i, image_id in enumerate(remaining_ids):
        try:
            print(
                f"\n[{i+1}/{len(remaining_ids)}] Exporting {image_id}...",
                end="",
                flush=True,
            )
            export_image_fixed(table_name, image_id, output_dir)
            print(" ✅")
            success_count += 1

            # Check if we got labels
            output_file = Path(output_dir) / f"{image_id}.json"
            with open(output_file, "r") as f:
                data = json.load(f)

            label_count = len(data.get("receipt_word_labels", []))
            if label_count > 0:
                print(f"    → Found {label_count} word labels")
            else:
                print(f"    ⚠️  No labels found")

        except Exception as e:
            print(f" ❌ Error: {str(e)}")
            error_count += 1

    print(f"\n📊 EXPORT SUMMARY")
    print("=" * 60)
    print(f"✅ Successfully exported: {success_count} receipts")
    print(f"❌ Failed exports: {error_count} receipts")
    print(f"📁 Data saved to: {output_dir}")


if __name__ == "__main__":
    export_remaining_receipts()
