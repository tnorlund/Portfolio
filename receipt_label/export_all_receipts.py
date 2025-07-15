#!/usr/bin/env python3
"""
Export all receipts from production DynamoDB for comprehensive testing.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set up environment
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.export_image import export_image


def export_all_receipts():
    """Export all receipts for comprehensive testing."""

    table_name = "ReceiptsTable-d7ff76a"
    output_dir = "./receipt_data_with_labels"

    print(f"🔄 Resuming export from {table_name}")
    print(f"📂 Output directory: {output_dir}")

    # Check existing files to avoid re-export
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    existing_files = set()
    if output_path.exists():
        existing_files = {f.stem for f in output_path.glob("*.json")}
        print(f"📁 Found {len(existing_files)} existing JSON files")

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Get all images (paginate through all results)
    print("📋 Getting list of all images...")
    all_images = []
    last_evaluated_key = None

    while True:
        images_result = dynamo_client.list_images(
            limit=100, last_evaluated_key=last_evaluated_key
        )
        images, last_evaluated_key = images_result

        if not images:
            break

        all_images.extend(images)
        print(f"   Retrieved {len(images)} images (total: {len(all_images)})")

        if not last_evaluated_key:
            break

    if not all_images:
        print("❌ No images found in database")
        return

    print(f"🎯 Found {len(all_images)} total images in database")

    # Filter out already exported images
    images_to_export = []
    for image in all_images:
        if image.image_id not in existing_files:
            images_to_export.append(image)

    print(f"⏭️  Need to export {len(images_to_export)} remaining images")
    print(f"✅ Already exported: {len(existing_files)} images")

    if not images_to_export:
        print("🎉 All images already exported! No work needed.")
        return

    # Export remaining images
    success_count = 0
    failed_images = []

    for i, image in enumerate(images_to_export):
        try:
            image_id = image.image_id
            print(f"[{i+1}/{len(images_to_export)}] Exporting {image_id}...")
            export_image(table_name, image_id, output_dir)
            success_count += 1
            print(f"    ✅ Success")
        except Exception as e:
            print(f"    ❌ Error: {str(e)}")
            failed_images.append((image_id, str(e)))

    total_exported = len(existing_files) + success_count
    print(f"\n📊 Export Summary:")
    print(f"   📁 Total images in database: {len(all_images)}")
    print(
        f"   ✅ Total successfully exported: {total_exported}/{len(all_images)}"
    )
    print(f"   🔄 Newly exported this run: {success_count}")
    print(f"   ❌ Failed this run: {len(failed_images)}")
    print(f"   📁 Data saved to: {output_dir}")

    if failed_images:
        print(f"\n❌ Failed exports:")
        for image_id, error in failed_images[:5]:  # Show first 5 failures
            print(f"   {image_id}: {error}")


if __name__ == "__main__":
    export_all_receipts()
