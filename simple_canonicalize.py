#!/usr/bin/env python3
"""Simple canonicalization script that directly updates metadata."""

import os
from typing import Dict, List
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptMetadata

# Initialize client
client = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])

def main():
    print("=" * 70)
    print("SIMPLE METADATA CANONICALIZATION")
    print("=" * 70)
    
    # Load all metadata
    print("\nLoading metadata from DynamoDB...")
    all_metadata, _ = client.list_receipt_metadatas()
    print(f"✅ Loaded {len(all_metadata)} records")
    
    # Group by place_id
    by_place_id: Dict[str, List[ReceiptMetadata]] = {}
    for record in all_metadata:
        if record.place_id:
            if record.place_id not in by_place_id:
                by_place_id[record.place_id] = []
            by_place_id[record.place_id].append(record)
    
    print(f"📊 Found {len(by_place_id)} unique place_ids")
    
    # Process each group
    total_updated = 0
    groups_processed = 0
    
    for place_id, records in by_place_id.items():
        if len(records) <= 1:
            continue  # Skip single records
        
        # Find the best canonical record (most complete)
        canonical = max(records, key=lambda r: (
            bool(r.merchant_name) + 
            bool(r.address) + 
            bool(r.phone_number) +
            bool(r.merchant_category)
        ))
        
        # Update all records in the group
        for record in records:
            updated = False
            
            # Set canonical values if not already set
            if not record.canonical_place_id:
                record.canonical_place_id = canonical.place_id
                updated = True
            
            if not record.canonical_merchant_name:
                record.canonical_merchant_name = canonical.merchant_name
                updated = True
            
            if not record.canonical_address:
                record.canonical_address = canonical.address
                updated = True
            
            if not record.canonical_phone_number:
                record.canonical_phone_number = canonical.phone_number
                updated = True
            
            if updated:
                # Update in DynamoDB
                client.update_receipt_metadata(record)
                total_updated += 1
        
        groups_processed += 1
        
        if groups_processed % 10 == 0:
            print(f"  Processed {groups_processed} groups, updated {total_updated} records...")
    
    print("\n" + "=" * 70)
    print("CANONICALIZATION COMPLETE")
    print("=" * 70)
    print(f"✅ Updated {total_updated} records")
    print(f"📊 Processed {groups_processed} place_id groups")
    
    # Show sample results
    print("\n📝 Sample canonicalized merchants:")
    reloaded, _ = client.list_receipt_metadatas()
    
    seen_canonical = set()
    count = 0
    for record in reloaded:
        if record.canonical_merchant_name and record.canonical_place_id:
            key = f"{record.canonical_merchant_name}|{record.canonical_place_id}"
            if key not in seen_canonical and count < 10:
                print(f"  - {record.canonical_merchant_name}")
                print(f"    Place ID: {record.canonical_place_id[:30]}...")
                seen_canonical.add(key)
                count += 1

if __name__ == "__main__":
    main()