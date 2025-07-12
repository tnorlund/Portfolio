#!/usr/bin/env python3
"""
Check the actual PK/SK format of Receipt entities in PROD DynamoDB table.
"""

import json
import os
import sys
import boto3
from boto3.dynamodb.conditions import Key, Attr

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
dynamodb = boto3.resource('dynamodb')
prod_table = dynamodb.Table(prod_table_name)

print(f"=== Checking Receipt PK/SK Format in PROD Table ===")
print(f"Table: {prod_table_name}\n")

# Query for Receipt entities using GSITYPE
print("Querying for Receipt entities using GSITYPE...")
response = prod_table.query(
    IndexName='GSITYPE',
    KeyConditionExpression=Key('TYPE').eq('RECEIPT'),
    Limit=10  # Just get a sample
)

receipts = response.get('Items', [])
print(f"Found {len(receipts)} Receipt entities (limited to 10)\n")

if receipts:
    print("=== Receipt Entity Key Format ===")
    for i, receipt in enumerate(receipts):
        print(f"\nReceipt {i+1}:")
        print(f"  PK: {receipt.get('PK', 'N/A')}")
        print(f"  SK: {receipt.get('SK', 'N/A')}")
        print(f"  TYPE: {receipt.get('TYPE', 'N/A')}")
        
        # Check if PK starts with IMAGE# or RECEIPT#
        pk = receipt.get('PK', '')
        if pk.startswith('IMAGE#'):
            print(f"  ⚠️  PK starts with IMAGE# (not RECEIPT#)")
        elif pk.startswith('RECEIPT#'):
            print(f"  ✓ PK starts with RECEIPT#")
        else:
            print(f"  ❌ PK has unexpected format: {pk[:20]}...")
        
        # Show other fields for context
        if 'image_id' in receipt:
            print(f"  image_id: {receipt['image_id']}")
        if 'receipt_id' in receipt:
            print(f"  receipt_id: {receipt['receipt_id']}")

    # Analyze patterns
    print("\n=== Pattern Analysis ===")
    pk_patterns = {}
    for receipt in receipts:
        pk = receipt.get('PK', '')
        if pk.startswith('IMAGE#'):
            pk_patterns['IMAGE#'] = pk_patterns.get('IMAGE#', 0) + 1
        elif pk.startswith('RECEIPT#'):
            pk_patterns['RECEIPT#'] = pk_patterns.get('RECEIPT#', 0) + 1
        else:
            pk_patterns['OTHER'] = pk_patterns.get('OTHER', 0) + 1
    
    print("PK Prefix Distribution:")
    for prefix, count in pk_patterns.items():
        print(f"  {prefix}: {count} entities")

    # Try to query with different patterns
    print("\n=== Testing Different Query Patterns ===")
    
    # Get one receipt's image_id to test queries
    sample_receipt = receipts[0]
    if 'image_id' in sample_receipt:
        image_id = sample_receipt['image_id']
        print(f"\nTesting queries with image_id: {image_id}")
        
        # Try querying with IMAGE# prefix
        print("\n1. Query with PK = IMAGE#{image_id}:")
        try:
            response = prod_table.query(
                KeyConditionExpression=Key('PK').eq(f'IMAGE#{image_id}'),
                Limit=5
            )
            items = response.get('Items', [])
            print(f"   Found {len(items)} items")
            for item in items:
                print(f"   - SK: {item.get('SK', 'N/A')}, TYPE: {item.get('TYPE', 'N/A')}")
        except Exception as e:
            print(f"   Error: {e}")
        
        # Try querying with RECEIPT# prefix (might not exist)
        print("\n2. Query with PK = RECEIPT#{image_id}:")
        try:
            response = prod_table.query(
                KeyConditionExpression=Key('PK').eq(f'RECEIPT#{image_id}'),
                Limit=5
            )
            items = response.get('Items', [])
            print(f"   Found {len(items)} items")
            for item in items:
                print(f"   - SK: {item.get('SK', 'N/A')}, TYPE: {item.get('TYPE', 'N/A')}")
        except Exception as e:
            print(f"   Error: {e}")

else:
    print("No Receipt entities found in the table!")

print("\n=== Conclusion ===")
print("Based on the query results above, we can determine the actual PK format for Receipt entities.")