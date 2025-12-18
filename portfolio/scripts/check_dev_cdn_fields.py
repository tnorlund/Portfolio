#!/usr/bin/env python3
"""Check CDN fields in DEV DynamoDB table to see if we can copy values to PROD."""

import boto3
from typing import Dict, List, Optional
import json
from collections import defaultdict


def get_table_client(table_name: str):
    """Get DynamoDB table client."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    return dynamodb.Table(table_name)


def query_entities(table: any, entity_type: str, limit: int = 10) -> List[Dict]:
    """Query entities of a specific type using GSITYPE."""
    try:
        response = table.query(
            IndexName="GSITYPE",
            KeyConditionExpression="#type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={":type": entity_type},
            Limit=limit,
        )
        return response.get("Items", [])
    except Exception as e:
        print(f"Error querying {entity_type}: {e}")
        return []


def analyze_cdn_fields(entity: Dict) -> Dict:
    """Analyze CDN fields in an entity."""
    entity_id = entity.get("PK", "").replace("IMAGE#", "").replace("RECEIPT#", "")

    cdn_info = {
        "id": entity_id,
        "type": entity.get("TYPE"),
        "cdn_s3_key": entity.get("cdn_s3_key"),
        "cdn_thumbnail_s3_key": entity.get("cdn_thumbnail_s3_key"),
        "cdn_url": entity.get("cdn_url"),
        "cdn_thumbnail_url": entity.get("cdn_thumbnail_url"),
        "thumbnail_s3_key": entity.get("thumbnail_s3_key"),
        "s3_key": entity.get("s3_key"),
        "bucket": entity.get("bucket"),
    }

    # Check pattern
    if cdn_info["cdn_s3_key"]:
        if f"assets/{entity_id}/" in cdn_info["cdn_s3_key"]:
            cdn_info["pattern"] = "subdirectory"
        else:
            cdn_info["pattern"] = "flat"
    else:
        cdn_info["pattern"] = "none"

    return cdn_info


def main():
    # DEV table
    dev_table_name = "ReceiptsTable-dc5be22"
    dev_table = get_table_client(dev_table_name)

    print("=" * 80)
    print("CHECKING DEV TABLE CDN FIELDS")
    print("=" * 80)
    print(f"\nTable: {dev_table_name}\n")

    # Check Images
    print("IMAGE ENTITIES:")
    print("-" * 80)
    images = query_entities(dev_table, "IMAGE", limit=5)

    image_patterns = defaultdict(int)
    for img in images:
        cdn_info = analyze_cdn_fields(img)
        image_patterns[cdn_info["pattern"]] += 1

        print(f"\nImage ID: {cdn_info['id']}")
        print(f"  Original S3 Key: {cdn_info['s3_key']}")
        print(f"  CDN S3 Key: {cdn_info['cdn_s3_key']}")
        print(f"  CDN Thumbnail: {cdn_info['cdn_thumbnail_s3_key']}")
        print(f"  Pattern: {cdn_info['pattern']}")

        # Check if subdirectory pattern
        if cdn_info["pattern"] == "subdirectory":
            print(f"  ✓ Uses subdirectory pattern: assets/{cdn_info['id']}/...")

    # Check Receipts
    print("\n\nRECEIPT ENTITIES:")
    print("-" * 80)
    receipts = query_entities(dev_table, "RECEIPT", limit=5)

    receipt_patterns = defaultdict(int)
    receipt_with_images = 0
    for receipt in receipts:
        cdn_info = analyze_cdn_fields(receipt)
        receipt_patterns[cdn_info["pattern"]] += 1

        # Check for image references
        image_refs = []
        for key, value in receipt.items():
            if key.startswith("image_") and key.endswith("_id") and value:
                image_refs.append(value)

        if image_refs:
            receipt_with_images += 1

        print(f"\nReceipt ID: {cdn_info['id']}")
        print(f"  CDN S3 Key: {cdn_info['cdn_s3_key']}")
        print(f"  Pattern: {cdn_info['pattern']}")
        print(f"  Image References: {', '.join(image_refs) if image_refs else 'None'}")

    # Summary
    print("\n\nSUMMARY:")
    print("=" * 80)
    print(f"\nImage CDN Patterns:")
    for pattern, count in image_patterns.items():
        print(f"  {pattern}: {count}")

    print(f"\nReceipt CDN Patterns:")
    for pattern, count in receipt_patterns.items():
        print(f"  {pattern}: {count}")

    print(f"\nReceipts with image references: {receipt_with_images}/{len(receipts)}")

    # Check if we should also compare with PROD
    print("\n\nNOTE: To compare with PROD, run with PROD table name.")
    print(
        "PROD table name can be obtained with: pulumi stack select tnorlund/portfolio/prod"
    )


if __name__ == "__main__":
    main()
