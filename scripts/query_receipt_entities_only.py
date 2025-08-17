#!/usr/bin/env python3
"""
Query only the top-level Receipt entities from PROD DynamoDB table.
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

print(f"=== Querying Top-Level Receipt Entities from PROD ===")
print(f"Table: {prod_table_name}\n")

# Query for Receipt entities using GSITYPE
print("Querying for Receipt entities (TYPE='RECEIPT') using GSITYPE...")
response = prod_table.query(
    IndexName="GSITYPE",
    KeyConditionExpression=Key("TYPE").eq("RECEIPT"),
    Limit=5,  # Get a few examples
)

receipts = response.get("Items", [])
print(f"Found {response.get('Count', 0)} Receipt entities (showing first 5)\n")

print("=== Receipt Entity Structure ===")
for i, receipt in enumerate(receipts):
    print(f"\nReceipt {i+1}:")
    print(f"  PK: {receipt.get('PK', 'N/A')}")
    print(f"  SK: {receipt.get('SK', 'N/A')}")
    print(f"  TYPE: {receipt.get('TYPE', 'N/A')}")

    # Extract image ID from PK
    pk = receipt.get("PK", "")
    if pk.startswith("IMAGE#"):
        image_id = pk.replace("IMAGE#", "")
        print(f"  Image ID: {image_id}")

    # Extract receipt number from SK
    sk = receipt.get("SK", "")
    if (
        sk.startswith("RECEIPT#") and "#" not in sk[8:]
    ):  # Only top-level receipts
        receipt_num = sk.replace("RECEIPT#", "")
        print(f"  Receipt Number: {receipt_num}")

    # Show other important fields
    if "bounding_box" in receipt:
        bb = receipt["bounding_box"]
        print(f"  Bounding Box: {bb}")

    if "words" in receipt and isinstance(receipt["words"], list):
        print(f"  Word Count: {len(receipt['words'])}")

    if "lines" in receipt and isinstance(receipt["lines"], list):
        print(f"  Line Count: {len(receipt['lines'])}")

print("\n=== Key Structure Summary ===")
print("Receipt entities in the PROD DynamoDB table use:")
print("- PK: IMAGE#{image_id} - Receipt is stored under its parent Image")
print(
    "- SK: RECEIPT#{receipt_number} - Where receipt_number is a 5-digit padded number"
)
print("- TYPE: RECEIPT")
print("\nThis confirms that Receipt entities are children of Image entities,")
print("not standalone entities with their own partition key.")

# Let's also check if we can find any receipts with different PK patterns
print("\n=== Checking for Alternative PK Patterns ===")

# Try scanning for receipts with PK starting with RECEIPT#
print("\nScanning for items with PK starting with 'RECEIPT#'...")
try:
    response = prod_table.scan(
        FilterExpression=Key("PK").begins_with("RECEIPT#")
        & Key("TYPE").eq("RECEIPT"),
        Limit=5,
    )
    items = response.get("Items", [])
    if items:
        print(f"  Found {len(items)} items with PK starting with 'RECEIPT#'")
        for item in items[:2]:
            print(f"    PK: {item.get('PK')}, SK: {item.get('SK')}")
    else:
        print("  No Receipt entities found with PK starting with 'RECEIPT#'")
except Exception as e:
    print(f"  Error during scan: {e}")
