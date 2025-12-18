#!/usr/bin/env python3
"""
Find Sprouts receipt IDs in DynamoDB for ROC analysis testing.
"""

import os
import sys
from pathlib import Path

# Add repo root to path
repo_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, repo_root)

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient

# Load environment
try:
    infra_dir = os.path.join(repo_root, "infra")
    env = load_env("dev", working_dir=infra_dir)
    table_name = env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(f"Missing table_name in environment")

    os.environ["DYNAMODB_TABLE_NAME"] = table_name
except Exception as e:
    print(f"Failed to load environment: {e}")
    sys.exit(1)

# Create client
try:
    dynamo_client = DynamoClient(table_name)
    print("✓ Created DynamoDB client")
except Exception as e:
    print(f"Failed to create DynamoDB client: {e}")
    sys.exit(1)

# Find Sprouts receipts
print("\nSearching for Sprouts receipts...")

try:
    metadatas, _ = dynamo_client.get_receipt_metadatas_by_merchant(
        merchant_name="Sprouts Farmers Market",
        limit=100
    )

    print(f"\nFound {len(metadatas)} Sprouts receipt(s):\n")

    for i, metadata in enumerate(metadatas[:15], 1):  # Show first 15
        print(f"{i}. {metadata.image_id} (receipt_id={metadata.receipt_id})")
        print(f"   Merchant: {metadata.merchant_name}")
        print()

    if len(metadatas) > 15:
        print(f"... and {len(metadatas) - 15} more")

    print(f"\nTotal: {len(metadatas)} Sprouts receipts")

except Exception as e:
    print(f"Error searching for receipts: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
