#!/usr/bin/env python3
"""Upload the final 5 metadata entries (excluding Ranch Hand BBQ which is already uploaded)."""

import os
import json
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptMetadata

# Read the seven_corrected_metadata.json file
with open("seven_corrected_metadata.json", "r") as f:
    data = json.load(f)

print(f"📋 Processing {data['total_count']} metadata entries")
print(f"📝 Note: {data.get('notes', '')}\n")

# Already uploaded items to skip
already_uploaded = {
    "0a345fdd-ecc3-4577-b3d3-869140451f43": "Ranch Hand BBQ"
}

# Prepare metadata for upload
to_upload = []
for m in data["metadata"]:
    if m["image_id"] in already_uploaded:
        print(f"⏭️  Skipping {already_uploaded[m['image_id']]} (already uploaded)")
        continue
    
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
    to_upload.append(metadata)

print(f"\n✅ Ready to upload {len(to_upload)} entries:")
for meta in to_upload:
    print(f"  - {meta.merchant_name}: {meta.image_id}")

# Upload to DynamoDB
table_name = os.environ.get("DYNAMO_TABLE_NAME")
if not table_name:
    print("\n❌ DYNAMO_TABLE_NAME environment variable not set")
    print("Please set it with: export DYNAMO_TABLE_NAME=your-table-name")
    exit(1)

print(f"\n📊 Uploading to DynamoDB table: {table_name}")

client = DynamoClient(table_name)
client.add_receipt_metadatas(to_upload)

print(f"✅ Successfully uploaded {len(to_upload)} metadata entries!")