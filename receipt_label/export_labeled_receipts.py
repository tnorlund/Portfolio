#!/usr/bin/env python3
"""
Export receipt data with labels from DynamoDB for validation testing.
Uses the export_image function to get complete data including labels.
"""

import os
import sys
import asyncio
from pathlib import Path
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set up environment
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_dynamo.data.export_image import export_image
from receipt_dynamo.data.dynamo_client import DynamoClient


def get_existing_receipt_ids():
    """Get list of receipt IDs we already have locally."""
    receipt_dir = Path("./receipt_data_with_labels")
    if not receipt_dir.exists():
        return []
    
    receipt_ids = []
    for file_path in receipt_dir.glob("*.json"):
        if file_path.name not in ["manifest.json", "four_fields_simple_results.json", 
                                   "metadata_impact_analysis.json", "pattern_vs_labels_comparison.json",
                                   "required_fields_analysis.json", "spatial_line_item_results_labeled.json"]:
            receipt_ids.append(file_path.stem)
    
    return receipt_ids


def export_receipts_with_labels():
    """Export receipt data with labels from DynamoDB."""
    
    print("🔄 EXPORTING RECEIPT DATA WITH LABELS FROM DYNAMODB")
    print("=" * 60)
    
    # Get table name from environment or use default
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    output_dir = "./receipt_data_with_labels_full"
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Get list of receipt IDs we have
    receipt_ids = get_existing_receipt_ids()
    
    if not receipt_ids:
        print("❌ No receipt IDs found in ./receipt_data_with_labels")
        return
    
    print(f"📋 Found {len(receipt_ids)} receipts to export")
    print(f"📂 Output directory: {output_dir}")
    print(f"🗄️  DynamoDB table: {table_name}")
    
    # Export a larger subset for testing (first 50)
    test_receipt_ids = receipt_ids[:50]
    
    success_count = 0
    error_count = 0
    
    for i, image_id in enumerate(test_receipt_ids):
        try:
            print(f"\n[{i+1}/{len(test_receipt_ids)}] Exporting {image_id}...", end="", flush=True)
            export_image(table_name, image_id, output_dir)
            print(" ✅")
            success_count += 1
            
            # Check if we got labels
            output_file = Path(output_dir) / f"{image_id}.json"
            with open(output_file, 'r') as f:
                data = json.load(f)
            
            label_count = len(data.get('receipt_word_labels', []))
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
    
    # Check one exported file to see the structure
    if success_count > 0:
        sample_file = list(Path(output_dir).glob("*.json"))[0]
        with open(sample_file, 'r') as f:
            sample_data = json.load(f)
        
        print(f"\n🔍 SAMPLE DATA STRUCTURE ({sample_file.name}):")
        for key, value in sample_data.items():
            if isinstance(value, list):
                print(f"  {key}: {len(value)} items")
                if key == "receipt_word_labels" and value:
                    print(f"    → Sample label: {value[0]}")


if __name__ == "__main__":
    export_receipts_with_labels()