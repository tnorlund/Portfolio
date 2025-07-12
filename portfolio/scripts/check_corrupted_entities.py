#!/usr/bin/env python3
"""
Quick script to check which entities that had missing TYPE are actually corrupted
and which ones exist in DEV.
"""

import json
import os
import sys
import boto3

# Get the list of entities that were missing TYPE
with open('missing_type_records_ReceiptsTable-d7ff76a.json', 'r') as f:
    missing_type_records = json.load(f)

if not missing_type_records:
    print("No records to check (file is empty)")
    sys.exit(0)

# Add parent directories to path
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
from pulumi import automation as auto

# Get table names
work_dir = os.path.join(parent_dir, "infra")

dev_stack = auto.create_or_select_stack(
    stack_name="tnorlund/portfolio/dev",
    work_dir=work_dir,
)
dev_outputs = dev_stack.outputs()
dev_table_name = dev_outputs["dynamodb_table_name"].value

prod_stack = auto.create_or_select_stack(
    stack_name="tnorlund/portfolio/prod",
    work_dir=work_dir,
)
prod_outputs = prod_stack.outputs()
prod_table_name = prod_outputs["dynamodb_table_name"].value

# Create DynamoDB clients
dynamodb = boto3.resource('dynamodb')
dev_table = dynamodb.Table(dev_table_name)
prod_table = dynamodb.Table(prod_table_name)

# Check each record
stats = {
    'total': len(missing_type_records),
    'exists_in_dev': 0,
    'missing_in_dev': 0,
    'image_exists': 0,
    'image_missing': 0,
    'receipt_exists': 0,
    'receipt_missing': 0
}

print(f"Checking {stats['total']} entities that were missing TYPE...")

for record in missing_type_records:
    pk = record['PK']
    sk = record['SK']
    entity_type = record['TYPE']
    
    # Check if exists in DEV
    dev_response = dev_table.get_item(Key={'PK': pk, 'SK': sk})
    
    if 'Item' in dev_response:
        stats['exists_in_dev'] += 1
        if entity_type == 'IMAGE':
            stats['image_exists'] += 1
        else:
            stats['receipt_exists'] += 1
    else:
        stats['missing_in_dev'] += 1
        if entity_type == 'IMAGE':
            stats['image_missing'] += 1
        else:
            stats['receipt_missing'] += 1
        print(f"Missing in DEV: {entity_type} {pk}, {sk}")

print("\nSummary:")
print(f"Total entities that were missing TYPE: {stats['total']}")
print(f"  Images: {stats['image_exists'] + stats['image_missing']}")
print(f"  Receipts: {stats['receipt_exists'] + stats['receipt_missing']}")
print(f"\nExists in DEV: {stats['exists_in_dev']}")
print(f"  Images: {stats['image_exists']}")
print(f"  Receipts: {stats['receipt_exists']}")
print(f"\nMissing in DEV: {stats['missing_in_dev']}")
print(f"  Images: {stats['image_missing']}")
print(f"  Receipts: {stats['receipt_missing']}")

# Save the ones that exist in DEV
entities_to_copy = []
for record in missing_type_records:
    pk = record['PK']
    sk = record['SK']
    
    # Check if exists in DEV
    dev_response = dev_table.get_item(Key={'PK': pk, 'SK': sk})
    if 'Item' in dev_response:
        entities_to_copy.append(record)

with open('entities_to_copy_from_dev.json', 'w') as f:
    json.dump(entities_to_copy, f, indent=2)

print(f"\nSaved {len(entities_to_copy)} entities that exist in DEV to: entities_to_copy_from_dev.json")