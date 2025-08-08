#!/usr/bin/env python3
"""
Prepare legitimate validated metadata for DynamoDB insertion.
Only includes single-receipt images with successful Google Places ID matches.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptMetadata


def get_legitimate_metadata() -> List[Dict[str, Any]]:
    """
    Extract only legitimate metadata from single-receipt images with valid place IDs.
    """
    export_dir = Path("dev.local_export")
    legitimate_metadata = []
    
    for json_file in export_dir.glob("*.json"):
        with open(json_file) as f:
            data = json.load(f)
        
        # Skip if no metadata
        if "selected_metadata" not in data:
            continue
        
        # Check receipt count
        receipt_count = len(data.get("receipts", []))
        
        # ONLY trust single-receipt images
        if receipt_count != 1:
            print(f"⚠️  Skipping {json_file.stem}: Has {receipt_count} receipts (mixed data)")
            continue
        
        metadata = data["selected_metadata"]
        place_id = metadata.get("place_id", "")
        
        # Only include if has valid place_id (real ones are long)
        if not place_id or len(place_id) <= 10 or place_id == "unknown":
            print(f"⚠️  Skipping {json_file.stem}: No valid place_id (method: {metadata.get('validated_by')})")
            continue
        
        # Get the single receipt's ID
        receipt = data["receipts"][0]
        
        # Clean up metadata for DynamoDB
        clean_metadata = {
            "image_id": receipt["image_id"],
            "receipt_id": receipt["receipt_id"],
            "merchant_name": metadata.get("merchant_name", ""),
            "address": metadata.get("address", ""),
            "phone_number": metadata.get("phone_number", ""),
            "place_id": place_id,
            "merchant_category": metadata.get("merchant_category", ""),
            "matched_fields": metadata.get("matched_fields", []),
            "validated_by": metadata.get("validated_by", ""),
            "reasoning": metadata.get("reasoning", ""),
            "timestamp": datetime.now().isoformat()
        }
        
        legitimate_metadata.append(clean_metadata)
        print(f"✅ Including {json_file.stem}: {clean_metadata['merchant_name'][:30]} ({clean_metadata['validated_by']})")
    
    return legitimate_metadata


def validate_metadata(metadata_list: List[Dict[str, Any]]) -> List[ReceiptMetadata]:
    """
    Validate and convert metadata dictionaries to ReceiptMetadata entities.
    """
    validated_entities = []
    
    for meta in metadata_list:
        try:
            # Create ReceiptMetadata entity to validate data
            # Convert timestamp string to datetime object
            from datetime import datetime
            timestamp_str = meta.get("timestamp")
            if timestamp_str and isinstance(timestamp_str, str):
                timestamp_obj = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp_obj = datetime.now()
            
            entity = ReceiptMetadata(
                image_id=meta["image_id"],
                receipt_id=meta["receipt_id"],
                merchant_name=meta["merchant_name"],
                address=meta.get("address"),
                phone_number=meta.get("phone_number"),
                place_id=meta["place_id"],
                merchant_category=meta.get("merchant_category"),
                matched_fields=meta.get("matched_fields", []),
                validated_by=meta.get("validated_by"),
                reasoning=meta.get("reasoning"),
                timestamp=timestamp_obj
            )
            validated_entities.append(entity)
        except Exception as e:
            print(f"❌ Validation failed for {meta['image_id']}#{meta['receipt_id']}: {e}")
    
    return validated_entities


def save_to_json(metadata_list: List[Dict[str, Any]], output_file: str = "legitimate_metadata.json"):
    """Save legitimate metadata to JSON file for review."""
    output_path = Path(output_file)
    
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "total_count": len(metadata_list),
            "metadata": metadata_list
        }, f, indent=2)
    
    print(f"\n💾 Saved {len(metadata_list)} legitimate metadata entries to {output_path}")


def add_to_dynamodb(validated_entities: List[ReceiptMetadata], dry_run: bool = True):
    """
    Add validated metadata to DynamoDB.
    
    Args:
        validated_entities: List of validated ReceiptMetadata entities
        dry_run: If True, only simulate without actually writing to DynamoDB
    """
    if dry_run:
        print("\n🔍 DRY RUN MODE - Not writing to DynamoDB")
        print(f"Would add {len(validated_entities)} metadata entries:")
        for entity in validated_entities[:5]:  # Show first 5
            print(f"  - {entity.image_id}#{entity.receipt_id}: {entity.merchant_name}")
        if len(validated_entities) > 5:
            print(f"  ... and {len(validated_entities) - 5} more")
        return
    
    # Initialize DynamoDB client
    table_name = os.environ.get("DYNAMO_TABLE_NAME")
    if not table_name:
        print("❌ Error: DYNAMO_TABLE_NAME environment variable not set")
        return
    
    dynamo_client = DynamoClient(table_name)
    
    success_count = 0
    for entity in validated_entities:
        try:
            dynamo_client.add_receipt_metadata(entity)
            success_count += 1
            print(f"✅ Added {entity.image_id}#{entity.receipt_id}: {entity.merchant_name}")
        except Exception as e:
            print(f"❌ Failed to add {entity.image_id}#{entity.receipt_id}: {e}")
    
    print(f"\n📊 Successfully added {success_count}/{len(validated_entities)} metadata entries to DynamoDB")


def main():
    """Main function to prepare and optionally insert metadata."""
    print("=" * 60)
    print("PREPARING LEGITIMATE METADATA FOR DYNAMODB")
    print("=" * 60)
    print("\nCriteria for inclusion:")
    print("1. Single-receipt images only (no mixed data)")
    print("2. Valid Google Places ID found")
    print("3. Not using INFERENCE method only")
    print()
    
    # Get legitimate metadata
    print("Analyzing validation results...\n")
    metadata_list = get_legitimate_metadata()
    
    print(f"\n📊 Summary: Found {len(metadata_list)} legitimate metadata entries")
    
    if not metadata_list:
        print("No legitimate metadata found")
        return
    
    # Validate entities
    print("\nValidating metadata entities...")
    validated_entities = validate_metadata(metadata_list)
    print(f"✅ Successfully validated {len(validated_entities)} entities")
    
    # Save to JSON for review
    save_to_json(metadata_list)
    
    # Show summary by validation method
    print("\n📈 Breakdown by validation method:")
    method_counts = {}
    for meta in metadata_list:
        method = meta.get("validated_by", "UNKNOWN")
        method_counts[method] = method_counts.get(method, 0) + 1
    
    for method, count in sorted(method_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {method}: {count}")
    
    # Show merchant distribution
    print("\n🏪 Merchants found:")
    merchants = {}
    for meta in metadata_list:
        merchant = meta.get("merchant_name", "UNKNOWN")
        merchants[merchant] = merchants.get(merchant, 0) + 1
    
    for merchant, count in sorted(merchants.items()):
        print(f"  {merchant}: {count} receipt(s)")
    
    # Offer to add to DynamoDB
    print("\n" + "=" * 60)
    print("Ready to add to DynamoDB:")
    print(f"  - {len(validated_entities)} validated metadata entries")
    print(f"  - Table: {os.environ.get('DYNAMO_TABLE_NAME', 'NOT SET')}")
    print("\nTo add to DynamoDB, run:")
    print("  python prepare_metadata_for_dynamo.py --write")
    print("\nOr import in Python:")
    print("  from prepare_metadata_for_dynamo import add_to_dynamodb, validate_metadata, get_legitimate_metadata")
    print("  entities = validate_metadata(get_legitimate_metadata())")
    print("  add_to_dynamodb(entities, dry_run=False)")


if __name__ == "__main__":
    import sys
    
    # Check for --write flag
    write_to_dynamo = "--write" in sys.argv
    
    if write_to_dynamo:
        print("⚠️  WRITE MODE - Will add to DynamoDB")
        response = input("Are you sure you want to write to DynamoDB? (yes/no): ")
        if response.lower() != "yes":
            print("Aborted")
            sys.exit(0)
    
    main()
    
    if write_to_dynamo:
        # Get metadata and validate
        metadata_list = get_legitimate_metadata()
        validated_entities = validate_metadata(metadata_list)
        
        # Add to DynamoDB
        add_to_dynamodb(validated_entities, dry_run=False)