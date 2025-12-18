#!/usr/bin/env python3
"""
Script to analyze corrupted entities and see what data we have available
to potentially reconstruct them.
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, Any, List
import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def analyze_corrupted_entities(table_name: str, raw_bucket: str, cdn_bucket: str):
    """Analyze corrupted entities to see what data we have."""
    dynamodb = boto3.resource("dynamodb")
    s3 = boto3.client("s3")
    table = dynamodb.Table(table_name)

    corrupted_entities = []
    s3_analysis = {
        "images_with_s3_objects": 0,
        "images_missing_s3": 0,
        "receipts_analyzed": 0,
        "total_corrupted": 0,
    }

    logger.info("Scanning for corrupted entities and analyzing available data...")

    # Scan for entities with TYPE but missing other required fields
    scan_kwargs = {
        "FilterExpression": "attribute_exists(#type)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key

        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])

        for item in items:
            pk = item.get("PK", "")
            sk = item.get("SK", "")
            entity_type = item.get("TYPE", "")

            # Check if this entity is corrupted
            is_corrupted = False
            missing_fields = []

            if (
                entity_type == "IMAGE"
                and pk.startswith("IMAGE#")
                and sk.startswith("IMAGE#")
            ):
                required_fields = [
                    "width",
                    "height",
                    "timestamp_added",
                    "raw_s3_bucket",
                    "raw_s3_key",
                    "image_type",
                ]
                for field in required_fields:
                    if field not in item:
                        missing_fields.append(field)
                        is_corrupted = True

            elif (
                entity_type == "RECEIPT"
                and pk.startswith("IMAGE#")
                and sk.startswith("RECEIPT#")
            ):
                required_fields = [
                    "width",
                    "height",
                    "timestamp_added",
                    "raw_s3_bucket",
                    "raw_s3_key",
                    "top_left",
                    "top_right",
                    "bottom_left",
                    "bottom_right",
                ]
                for field in required_fields:
                    if field not in item:
                        missing_fields.append(field)
                        is_corrupted = True

            if is_corrupted:
                s3_analysis["total_corrupted"] += 1

                # Extract image ID from PK
                image_id = pk.replace("IMAGE#", "")

                entity_info = {
                    "PK": pk,
                    "SK": sk,
                    "TYPE": entity_type,
                    "image_id": image_id,
                    "missing_fields": missing_fields,
                    "available_fields": list(item.keys()),
                    "s3_objects": {"raw": None, "cdn": None},
                }

                # Check if S3 objects exist for this image
                if entity_type == "IMAGE":
                    s3_analysis["receipts_analyzed"] += 1

                    # Try to find S3 objects
                    possible_extensions = ["jpg", "jpeg", "png", "tiff", "tif"]

                    for ext in possible_extensions:
                        raw_key = f"{image_id}.{ext}"
                        try:
                            s3.head_object(Bucket=raw_bucket, Key=raw_key)
                            entity_info["s3_objects"]["raw"] = raw_key
                            s3_analysis["images_with_s3_objects"] += 1
                            break
                        except s3.exceptions.ClientError:
                            continue

                    if entity_info["s3_objects"]["raw"] is None:
                        s3_analysis["images_missing_s3"] += 1
                        logger.debug(f"No S3 object found for image: {image_id}")

                corrupted_entities.append(entity_info)

                # Log sample entities
                if len(corrupted_entities) <= 5:
                    logger.info(
                        f"Sample corrupted {entity_type}: {pk}, missing: {missing_fields}"
                    )

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

        if len(corrupted_entities) % 50 == 0 and len(corrupted_entities) > 0:
            logger.info(f"Found {len(corrupted_entities)} corrupted entities so far...")

    # Save detailed analysis
    output_file = "corrupted_entities_analysis.json"
    with open(output_file, "w") as f:
        json.dump(corrupted_entities, f, indent=2)

    # Print analysis
    logger.info("\n" + "=" * 50)
    logger.info("CORRUPTED ENTITY ANALYSIS")
    logger.info("=" * 50)
    logger.info(f"Total corrupted entities: {s3_analysis['total_corrupted']}")

    # Count by type
    type_counts = {}
    field_missing_counts = {}

    for entity in corrupted_entities:
        entity_type = entity["TYPE"]
        type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

        for field in entity["missing_fields"]:
            field_missing_counts[field] = field_missing_counts.get(field, 0) + 1

    logger.info("\nBy entity type:")
    for entity_type, count in sorted(type_counts.items()):
        logger.info(f"  {entity_type}: {count}")

    logger.info("\nMost commonly missing fields:")
    for field, count in sorted(
        field_missing_counts.items(), key=lambda x: x[1], reverse=True
    ):
        logger.info(f"  {field}: {count} entities missing this")

    logger.info(f"\nS3 Analysis (for Image entities):")
    logger.info(f"  Images with S3 objects: {s3_analysis['images_with_s3_objects']}")
    logger.info(f"  Images missing S3 objects: {s3_analysis['images_missing_s3']}")

    # Check for patterns in available data
    available_patterns = {}
    for entity in corrupted_entities:
        available_key = tuple(sorted(entity["available_fields"]))
        available_patterns[available_key] = available_patterns.get(available_key, 0) + 1

    logger.info(f"\nData patterns in corrupted entities:")
    for pattern, count in sorted(
        available_patterns.items(), key=lambda x: x[1], reverse=True
    )[:5]:
        logger.info(f"  {count} entities have fields: {list(pattern)}")

    logger.info(f"\nDetailed analysis saved to: {output_file}")

    return corrupted_entities


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze corrupted entities to understand available data"
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    from pulumi import automation as auto

    work_dir = os.path.join(parent_dir, "infra")

    logger.info("Getting PROD configuration...")
    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()
    prod_table = prod_outputs["dynamodb_table_name"].value
    raw_bucket = prod_outputs["raw_bucket_name"].value
    cdn_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"PROD table: {prod_table}")
    logger.info(f"Raw bucket: {raw_bucket}")
    logger.info(f"CDN bucket: {cdn_bucket}")

    # Analyze corrupted entities
    analyze_corrupted_entities(prod_table, raw_bucket, cdn_bucket)


if __name__ == "__main__":
    main()
