#!/usr/bin/env python3
"""Upload the 5 successful validations from five_successful_metadata.json."""

import os
import json
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptMetadata

# Read the five_successful_metadata.json file
with open("five_successful_metadata.json", "r") as f:
    data = json.load(f)

print(f"📋 Found {data['total_count']} metadata entries to upload")

# Convert to ReceiptMetadata entities
metadatas = []
for m in data["metadata"]:
    # Convert timestamp string to datetime
    timestamp_str = m.get("timestamp", datetime.now().isoformat())
    if isinstance(timestamp_str, str):
        # Handle ISO format with timezone
        timestamp_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    else:
        timestamp_obj = datetime.now()
    
    # Create entity with proper timestamp
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
    metadatas.append(metadata)
    print(f"  - {metadata.merchant_name} ({metadata.image_id}#{metadata.receipt_id})")

# Upload to DynamoDB
table_name = os.environ.get("DYNAMO_TABLE_NAME")
if not table_name:
    print("\n❌ DYNAMO_TABLE_NAME environment variable not set")
    print("Please set it with: export DYNAMO_TABLE_NAME=your-table-name")
    exit(1)

print(f"\n📊 Uploading to DynamoDB table: {table_name}")

client = DynamoClient(table_name)
client.add_receipt_metadatas(metadatas)

print(f"✅ Successfully uploaded {len(metadatas)} metadata entries")