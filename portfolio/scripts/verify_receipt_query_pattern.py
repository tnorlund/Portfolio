#!/usr/bin/env python3
"""
Verify the query pattern for Receipt entities - test with actual image ID.
"""

import json
import os
import sys
import boto3
from boto3.dynamodb.conditions import Key

# Add parent directories to path
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
from pulumi import automation as auto

# Get table names
work_dir = os.path.join(parent_dir, "infra")

prod_stack = auto.create_or_select_stack(
    stack_name="tnorlund/portfolio/prod",
    work_dir=work_dir,
)
prod_outputs = prod_stack.outputs()
prod_table_name = prod_outputs["dynamodb_table_name"].value

# Create DynamoDB client
dynamodb = boto3.resource("dynamodb")
prod_table = dynamodb.Table(prod_table_name)

print(f"=== Verifying Receipt Query Pattern ===")
print(f"Table: {prod_table_name}\n")

# Test with a known image ID from the previous query
test_image_id = "2c9b770c-9407-4cdc-b0eb-3a5b27f0af15"

print(f"Testing with image_id: {test_image_id}\n")

# Query all items for this image
print("1. Query all items with PK = IMAGE#{image_id}:")
response = prod_table.query(
    KeyConditionExpression=Key("PK").eq(f"IMAGE#{test_image_id}")
)
items = response.get("Items", [])
print(f"   Found {len(items)} items\n")

for item in items:
    print(f"   - SK: {item.get('SK', 'N/A')}")
    print(f"     TYPE: {item.get('TYPE', 'N/A')}")
    if item.get("TYPE") == "RECEIPT":
        print(f"     ✓ This is a Receipt entity")
        # Show some Receipt-specific fields
        if "bounding_box" in item:
            print(f"     Has bounding_box: Yes")
        if "words" in item:
            print(f"     Has words: Yes (count: {len(item['words'])})")
        if "lines" in item:
            print(f"     Has lines: Yes (count: {len(item['lines'])})")
    print()

# Specifically query for Receipt entities
print("\n2. Query for Receipt entities with SK begins_with 'RECEIPT#':")
response = prod_table.query(
    KeyConditionExpression=Key("PK").eq(f"IMAGE#{test_image_id}")
    & Key("SK").begins_with("RECEIPT#")
)
receipts = response.get("Items", [])
print(f"   Found {len(receipts)} Receipt entities")

if receipts:
    print("\n   Receipt details:")
    for i, receipt in enumerate(receipts):
        print(f"   Receipt {i+1}:")
        print(f"     SK: {receipt.get('SK', 'N/A')}")
        print(f"     TYPE: {receipt.get('TYPE', 'N/A')}")
        # Extract receipt number from SK
        sk = receipt.get("SK", "")
        if sk.startswith("RECEIPT#"):
            receipt_num = sk.replace("RECEIPT#", "")
            print(f"     Receipt Number: {receipt_num}")

print("\n=== Summary ===")
print("Receipt entities in PROD DynamoDB table:")
print("- PK format: IMAGE#{image_id}")
print("- SK format: RECEIPT#{receipt_number}")
print("- TYPE: RECEIPT")
print("\nThis means Receipt entities are stored under their associated Image's PK,")
print("not under a separate RECEIPT# partition key.")
