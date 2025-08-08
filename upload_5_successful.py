#!/usr/bin/env python3
"""Upload the 5 successfully validated receipts to DynamoDB."""

import json
import os
from datetime import datetime
from pathlib import Path

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptMetadata


def get_5_successful_metadata():
    """Get the 5 successful validations from recent results."""
    
    export_dir = Path('dev.local_export')
    successful = []
    
    # Collect all results with modification times
    all_results = []
    for json_file in export_dir.glob('*.json'):
        mtime = json_file.stat().st_mtime
        with open(json_file) as f:
            data = json.load(f)
        
        if 'selected_metadata' in data:
            meta = data['selected_metadata']
            place_id = meta.get('place_id', '')
            
            # Only include if has valid place_id
            if place_id and len(place_id) > 10 and place_id != 'unknown':
                all_results.append({
                    'file': json_file.stem,
                    'mtime': mtime,
                    'metadata': meta,
                    'receipt_count': len(data.get('receipts', []))
                })
    
    # Sort by modification time and get most recent
    all_results.sort(key=lambda x: x['mtime'], reverse=True)
    
    # Filter to get the 5 most recent successful ones
    # Exclude multi-receipt images for safety
    for result in all_results[:12]:  # Check recent 12
        if result['receipt_count'] == 1:  # Single receipt only
            successful.append(result['metadata'])
            if len(successful) >= 5:
                break
    
    return successful


def validate_and_prepare(metadata_list):
    """Convert metadata to ReceiptMetadata entities."""
    entities = []
    
    for meta in metadata_list:
        try:
            # Convert timestamp
            timestamp_str = meta.get('timestamp')
            if timestamp_str and isinstance(timestamp_str, str):
                timestamp_obj = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp_obj = datetime.now()
            
            entity = ReceiptMetadata(
                image_id=meta['image_id'],
                receipt_id=meta['receipt_id'],
                merchant_name=meta['merchant_name'],
                address=meta.get('address'),
                phone_number=meta.get('phone_number'),
                place_id=meta['place_id'],
                merchant_category=meta.get('merchant_category'),
                matched_fields=meta.get('matched_fields', []),
                validated_by=meta.get('validated_by'),
                reasoning=meta.get('reasoning'),
                timestamp=timestamp_obj
            )
            entities.append(entity)
            
        except Exception as e:
            print(f"❌ Failed to validate {meta.get('image_id')}#{meta.get('receipt_id')}: {e}")
    
    return entities


def main():
    """Main function to upload the 5 successful validations."""
    
    print("=" * 70)
    print("UPLOADING 5 SUCCESSFUL VALIDATIONS TO DYNAMODB")
    print("=" * 70)
    
    # Get the 5 successful metadata entries
    metadata_list = get_5_successful_metadata()
    
    if not metadata_list:
        print("❌ No successful validations found")
        return
    
    print(f"\n📋 Found {len(metadata_list)} successful validations to upload:\n")
    
    for i, meta in enumerate(metadata_list, 1):
        print(f"{i}. {meta.get('merchant_name', 'UNKNOWN')}")
        print(f"   Image: {meta.get('image_id', 'N/A')}")
        print(f"   Place ID: {meta.get('place_id', 'N/A')[:30]}...")
        print(f"   Method: {meta.get('validated_by', 'N/A')}")
        print()
    
    # Validate and convert to entities
    entities = validate_and_prepare(metadata_list)
    
    if not entities:
        print("❌ No valid entities to upload")
        return
    
    print(f"✅ Validated {len(entities)} entities")
    
    # Check DynamoDB settings
    table_name = os.environ.get("DYNAMO_TABLE_NAME")
    if not table_name:
        print("\n❌ DYNAMO_TABLE_NAME environment variable not set")
        print("Please set it with: export DYNAMO_TABLE_NAME=your-table-name")
        return
    
    print(f"\n📊 Ready to upload to table: {table_name}")
    
    # Confirm upload
    response = input("\nProceed with upload? (yes/no): ")
    if response.lower() != 'yes':
        print("Upload cancelled")
        return
    
    # Upload to DynamoDB
    try:
        client = DynamoClient(table_name)
        
        # Upload one by one for better error tracking
        success_count = 0
        for entity in entities:
            try:
                client.add_receipt_metadata(entity)
                print(f"✅ Uploaded {entity.image_id}#{entity.receipt_id}: {entity.merchant_name}")
                success_count += 1
            except Exception as e:
                print(f"❌ Failed to upload {entity.image_id}#{entity.receipt_id}: {e}")
        
        print(f"\n📊 Successfully uploaded {success_count}/{len(entities)} metadata entries")
        
    except Exception as e:
        print(f"❌ Error connecting to DynamoDB: {e}")


if __name__ == "__main__":
    main()