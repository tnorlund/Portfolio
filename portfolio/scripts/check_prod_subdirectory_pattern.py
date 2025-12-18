#!/usr/bin/env python3
"""Check PROD table for subdirectory CDN pattern and compare entity counts."""

import boto3
from typing import Dict, List
from collections import defaultdict


def get_table_client(table_name: str):
    """Get DynamoDB table client."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    return dynamodb.Table(table_name)


def count_entities_by_type(table: any) -> Dict[str, int]:
    """Count entities by type."""
    counts = defaultdict(int)

    # Get total item count
    response = table.describe_table()
    total_items = response["Table"]["ItemCount"]
    print(f"  Total items in table: {total_items}")

    # Count specific types
    for entity_type in ["IMAGE", "RECEIPT"]:
        try:
            response = table.query(
                IndexName="GSITYPE",
                KeyConditionExpression="#type = :type",
                ExpressionAttributeNames={"#type": "TYPE"},
                ExpressionAttributeValues={":type": entity_type},
                Select="COUNT",
            )
            counts[entity_type] = response["Count"]

            # If there are more items, keep querying
            while "LastEvaluatedKey" in response:
                response = table.query(
                    IndexName="GSITYPE",
                    KeyConditionExpression="#type = :type",
                    ExpressionAttributeNames={"#type": "TYPE"},
                    ExpressionAttributeValues={":type": entity_type},
                    Select="COUNT",
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                counts[entity_type] += response["Count"]

        except Exception as e:
            print(f"Error counting {entity_type}: {e}")

    return dict(counts)


def find_subdirectory_images(table: any) -> List[Dict]:
    """Find images that use subdirectory pattern."""
    subdirectory_items = []

    # Patterns to check
    patterns = [
        "assets/IMAGE#",  # Possible pattern with TYPE prefix
        "/1_",  # Size indicator
        "/2_",
        "/3_",
        "/original.",
        "/thumbnail.",
    ]

    for pattern in patterns:
        try:
            response = table.scan(
                FilterExpression="contains(cdn_s3_key, :pattern) AND #type = :type",
                ExpressionAttributeNames={"#type": "TYPE"},
                ExpressionAttributeValues={":pattern": pattern, ":type": "IMAGE"},
                Limit=5,
            )

            items = response.get("Items", [])
            if items:
                print(f"\n  Found {len(items)} items with pattern '{pattern}':")
                for item in items:
                    entity_id = item.get("PK", "").replace("IMAGE#", "")
                    print(f"    - {entity_id[:12]}... : {item.get('cdn_s3_key')}")
                    subdirectory_items.append(item)

        except Exception as e:
            print(f"  Error scanning for pattern '{pattern}': {e}")

    return subdirectory_items


def check_specific_ids(table: any) -> None:
    """Check specific entity IDs that might have subdirectory pattern."""
    # IDs from the migration/restore operations that might have subdirectory pattern
    test_ids = [
        "a97f13ac-dda3-4ab4-830b-f3c4c7cf9f9f",
        "03ba9ede-0173-445f-8a7b-2b63cdec2e4f",
        "041f9f16-d19d-42d8-8ea7-2e45e55f10e9",
    ]

    print("\nChecking specific IDs from migration:")
    for entity_id in test_ids:
        try:
            response = table.get_item(Key={"PK": f"IMAGE#{entity_id}", "SK": entity_id})

            if "Item" in response:
                item = response["Item"]
                cdn_key = item.get("cdn_s3_key", "NOT SET")
                print(f"  {entity_id[:12]}... : {cdn_key}")
                if "/" in cdn_key and entity_id in cdn_key:
                    print(f"    ✓ Uses subdirectory pattern!")
            else:
                print(f"  {entity_id[:12]}... : NOT FOUND")

        except Exception as e:
            print(f"  Error checking {entity_id}: {e}")


def main():
    # Table names
    dev_table_name = "ReceiptsTable-dc5be22"
    prod_table_name = "ReceiptsTable-d7ff76a"

    print("=" * 80)
    print("COMPREHENSIVE CDN PATTERN CHECK")
    print("=" * 80)

    # Check DEV
    print(f"\nDEV Table: {dev_table_name}")
    print("-" * 40)
    dev_table = get_table_client(dev_table_name)
    dev_counts = count_entities_by_type(dev_table)
    print(f"  Images: {dev_counts.get('IMAGE', 0)}")
    print(f"  Receipts: {dev_counts.get('RECEIPT', 0)}")

    # Check PROD
    print(f"\nPROD Table: {prod_table_name}")
    print("-" * 40)
    prod_table = get_table_client(prod_table_name)
    prod_counts = count_entities_by_type(prod_table)
    print(f"  Images: {prod_counts.get('IMAGE', 0)}")
    print(f"  Receipts: {prod_counts.get('RECEIPT', 0)}")

    # Look for subdirectory patterns in PROD
    print("\n\nSEARCHING FOR SUBDIRECTORY PATTERNS IN PROD")
    print("=" * 80)
    subdirectory_items = find_subdirectory_images(prod_table)

    # Check specific IDs
    check_specific_ids(prod_table)

    # Summary
    print("\n\nSUMMARY")
    print("=" * 80)

    if subdirectory_items:
        print(
            f"⚠️  Found {len(subdirectory_items)} items with subdirectory pattern in PROD!"
        )
        print("   This means PROD has MIXED patterns (both flat and subdirectory).")
        print("   Copying from DEV (flat only) would NOT be appropriate.")
    else:
        print("✓ PROD appears to use only flat pattern for CDN fields.")
        print("  Both DEV and PROD use the same pattern.")

    print(f"\nEntity counts:")
    print(
        f"  DEV:  {dev_counts.get('IMAGE', 0)} images, {dev_counts.get('RECEIPT', 0)} receipts"
    )
    print(
        f"  PROD: {prod_counts.get('IMAGE', 0)} images, {prod_counts.get('RECEIPT', 0)} receipts"
    )

    if dev_counts.get("IMAGE", 0) != prod_counts.get("IMAGE", 0):
        print(f"\n⚠️  Image counts differ between DEV and PROD!")
        print(
            f"   DEV has {dev_counts.get('IMAGE', 0) - prod_counts.get('IMAGE', 0)} more/fewer images"
        )


if __name__ == "__main__":
    main()
