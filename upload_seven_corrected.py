#!/usr/bin/env python3
"""Upload the 7 corrected validations (6 after excluding multi-receipt)."""

import os
import json
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptMetadata

# Read the seven_corrected_metadata.json file
with open("seven_corrected_metadata.json", "r") as f:
    data = json.load(f)

print(f"📋 Found {data['total_count']} metadata entries to process")
print(f"📝 Note: {data.get('notes', '')}\n")

# Filter out entries that need place ID lookup
ready_to_upload = []
needs_lookup = []

for m in data["metadata"]:
    if m.get("place_id") == "NEEDS_LOOKUP":
        needs_lookup.append(m)
    else:
        # Convert timestamp string to datetime
        timestamp_str = m.get("timestamp", datetime.now().isoformat())
        if isinstance(timestamp_str, str):
            timestamp_obj = datetime.fromisoformat(
                timestamp_str.replace("Z", "+00:00")
            )
        else:
            timestamp_obj = datetime.now()
        
        # Create entity
        metadata = ReceiptMetadata(
            image_id=m["image_id"],
            receipt_id=m["receipt_id"],
            merchant_name=m["merchant_name"],
            address=m.get("address"),
            phone_number=m.get("phone_number"),
            place_id=m["place_id"],
            merchant_category=m.get("merchant_category"),
            matched_fields=m.get("matched_fields", []),
            validated_by=m.get("validated_by"),
            reasoning=m.get("reasoning"),
            timestamp=timestamp_obj
        )
        ready_to_upload.append(metadata)

print(f"✅ Ready to upload: {len(ready_to_upload)}")
for meta in ready_to_upload:
    print(f"  - {meta.merchant_name} ({meta.image_id})")

if needs_lookup:
    print(f"\n⚠️  Need place ID lookup: {len(needs_lookup)}")
    for m in needs_lookup:
        print(f"  - {m['merchant_name']} at {m['address']}")
    print("\nSkipping these for now - need to find place IDs first")

if not ready_to_upload:
    print("\n❌ No entries ready to upload")
    exit(1)

# Check for duplicates (Ranch Hand BBQ already uploaded)
print("\n🔍 Checking for duplicates...")
duplicate_check = {
    "0a345fdd-ecc3-4577-b3d3-869140451f43": "Ranch Hand BBQ (already in additional_metadata.json)"
}

final_upload = []
for meta in ready_to_upload:
    if meta.image_id in duplicate_check:
        print(f"  ⚠️  Skipping {duplicate_check[meta.image_id]}")
    else:
        final_upload.append(meta)

if not final_upload:
    print("\n✅ All entries already uploaded or skipped")
    exit(0)

# Upload to DynamoDB
table_name = os.environ.get("DYNAMO_TABLE_NAME")
if not table_name:
    print("\n❌ DYNAMO_TABLE_NAME environment variable not set")
    print("Please set it with: export DYNAMO_TABLE_NAME=your-table-name")
    exit(1)

print(f"\n📊 Uploading {len(final_upload)} entries to DynamoDB table: {table_name}")

client = DynamoClient(table_name)
client.add_receipt_metadatas(final_upload)

print(f"✅ Successfully uploaded {len(final_upload)} metadata entries")