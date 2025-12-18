#!/usr/bin/env python3
"""Final check to determine if DEV CDN values can be copied to PROD."""

import boto3
from typing import Dict, List, Set
import re


def get_table_client(table_name: str):
    """Get DynamoDB table client."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    return dynamodb.Table(table_name)


def get_all_entities(table: any, entity_type: str, max_items: int = 200) -> List[Dict]:
    """Get all entities of a specific type."""
    items = []

    try:
        response = table.query(
            IndexName="GSITYPE",
            KeyConditionExpression="#type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={":type": entity_type},
        )

        items.extend(response.get("Items", []))

        # Continue if there are more items
        while "LastEvaluatedKey" in response and len(items) < max_items:
            response = table.query(
                IndexName="GSITYPE",
                KeyConditionExpression="#type = :type",
                ExpressionAttributeNames={"#type": "TYPE"},
                ExpressionAttributeValues={":type": entity_type},
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

    except Exception as e:
        print(f"Error querying {entity_type}: {e}")

    return items[:max_items]


def analyze_cdn_pattern(cdn_key: str, entity_id: str) -> str:
    """Determine CDN pattern from key."""
    if not cdn_key:
        return "none"

    # Check for subdirectory pattern: assets/{id}/1_size.ext
    if f"assets/{entity_id}/" in cdn_key:
        return "subdirectory"
    # Check for subdirectory with prefix: assets/IMAGE#{id}/...
    elif f"assets/IMAGE#{entity_id}/" in cdn_key:
        return "subdirectory_with_prefix"
    # Flat pattern: assets/{id}.ext or assets/{id}_suffix.ext
    elif entity_id in cdn_key and "/" not in cdn_key.replace("assets/", ""):
        return "flat"
    else:
        return "unknown"


def main():
    # Table names
    dev_table_name = "ReceiptsTable-dc5be22"
    prod_table_name = "ReceiptsTable-d7ff76a"

    dev_table = get_table_client(dev_table_name)
    prod_table = get_table_client(prod_table_name)

    print("=" * 80)
    print("FINAL CDN COMPATIBILITY CHECK")
    print("=" * 80)

    # Get all images from both tables
    print("\nFetching IMAGE entities...")
    dev_images = get_all_entities(dev_table, "IMAGE", 100)
    prod_images = get_all_entities(prod_table, "IMAGE", 100)

    print(f"  DEV: Found {len(dev_images)} images")
    print(f"  PROD: Found {len(prod_images)} images")

    # Analyze patterns
    dev_patterns = {
        "flat": 0,
        "subdirectory": 0,
        "subdirectory_with_prefix": 0,
        "none": 0,
        "unknown": 0,
    }
    prod_patterns = {
        "flat": 0,
        "subdirectory": 0,
        "subdirectory_with_prefix": 0,
        "none": 0,
        "unknown": 0,
    }

    # Build ID sets and analyze patterns
    dev_ids = set()
    prod_ids = set()

    print("\nAnalyzing CDN patterns...")

    # Analyze DEV
    for img in dev_images:
        entity_id = img.get("PK", "").replace("IMAGE#", "")
        dev_ids.add(entity_id)
        cdn_key = img.get("cdn_s3_key", "")
        pattern = analyze_cdn_pattern(cdn_key, entity_id)
        dev_patterns[pattern] += 1

    # Analyze PROD
    prod_subdirectory_examples = []
    for img in prod_images:
        entity_id = img.get("PK", "").replace("IMAGE#", "")
        prod_ids.add(entity_id)
        cdn_key = img.get("cdn_s3_key", "")
        pattern = analyze_cdn_pattern(cdn_key, entity_id)
        prod_patterns[pattern] += 1

        # Collect subdirectory examples
        if (
            pattern in ["subdirectory", "subdirectory_with_prefix"]
            and len(prod_subdirectory_examples) < 5
        ):
            prod_subdirectory_examples.append(
                {"id": entity_id, "cdn_key": cdn_key, "pattern": pattern}
            )

    # Find common IDs
    common_ids = dev_ids & prod_ids

    # Print results
    print("\nDEV CDN Patterns:")
    for pattern, count in dev_patterns.items():
        if count > 0:
            print(f"  {pattern}: {count}")

    print("\nPROD CDN Patterns:")
    for pattern, count in prod_patterns.items():
        if count > 0:
            print(f"  {pattern}: {count}")

    if prod_subdirectory_examples:
        print("\nPROD Subdirectory Examples:")
        for ex in prod_subdirectory_examples:
            print(f"  {ex['id'][:12]}... : {ex['cdn_key']}")

    # Check a few common IDs
    if common_ids:
        print(f"\n\nComparing {min(5, len(common_ids))} common entities:")
        for entity_id in sorted(list(common_ids))[:5]:
            # Find in lists
            dev_img = next(
                (img for img in dev_images if img.get("PK") == f"IMAGE#{entity_id}"),
                None,
            )
            prod_img = next(
                (img for img in prod_images if img.get("PK") == f"IMAGE#{entity_id}"),
                None,
            )

            if dev_img and prod_img:
                dev_cdn = dev_img.get("cdn_s3_key", "NOT SET")
                prod_cdn = prod_img.get("cdn_s3_key", "NOT SET")
                print(f"\n  ID: {entity_id}")
                print(f"    DEV:  {dev_cdn}")
                print(f"    PROD: {prod_cdn}")
                if dev_cdn == prod_cdn:
                    print(f"    Status: ✓ MATCH")
                else:
                    print(f"    Status: ✗ DIFFERENT")

    # Final recommendation
    print("\n" + "=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)

    if (
        prod_patterns["subdirectory"] > 0
        or prod_patterns["subdirectory_with_prefix"] > 0
    ):
        print("\n⚠️  CRITICAL: PROD has subdirectory pattern CDN entries!")
        print(
            f"   - {prod_patterns['subdirectory'] + prod_patterns['subdirectory_with_prefix']} entities use subdirectory pattern"
        )
        print(f"   - {prod_patterns['flat']} entities use flat pattern")
        print("\n   PROD has MIXED CDN patterns. DO NOT copy from DEV!")
        print("   DEV uses only flat pattern and would corrupt subdirectory entries.")
    elif prod_patterns["none"] > 0:
        print(f"\n✓ POTENTIAL: {prod_patterns['none']} PROD images have no CDN fields.")
        print("   These could potentially be populated from DEV if IDs match.")
        print(f"   Common IDs between DEV and PROD: {len(common_ids)}")
    else:
        print("\nℹ️  Both DEV and PROD have CDN fields populated with flat pattern.")
        print(f"   Common IDs: {len(common_ids)}")
        print("   No clear benefit to copying from DEV to PROD.")


if __name__ == "__main__":
    main()
