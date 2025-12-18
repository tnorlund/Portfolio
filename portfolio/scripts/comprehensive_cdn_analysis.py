#!/usr/bin/env python3
"""
Comprehensive analysis of CDN structure and mapping between DEV and PROD.
"""

import logging
import os
import sys
import json
import boto3
from decimal import Decimal
from typing import Dict, Any, List, Optional, Set

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from pulumi import automation as auto

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def analyze_s3_patterns(bucket_name: str, limit: int = 1000) -> Dict[str, Any]:
    """Analyze S3 bucket to understand CDN file patterns."""
    s3 = boto3.client("s3")

    logger.info(f"Analyzing S3 patterns in {bucket_name}...")

    patterns = {
        "subdirectory_pattern": {  # assets/{id}/1_size.ext
            "count": 0,
            "examples": [],
            "sizes": set(),
            "formats": set(),
            "image_ids": set(),
        },
        "flat_pattern": {  # assets/{id}_size.ext
            "count": 0,
            "examples": [],
            "sizes": set(),
            "formats": set(),
            "image_ids": set(),
        },
        "receipt_pattern": {  # assets/{id}_RECEIPT_{num}.ext
            "count": 0,
            "examples": [],
            "formats": set(),
            "image_ids": set(),
        },
        "cluster_pattern": {  # assets/{id}_cluster_{num}.ext
            "count": 0,
            "examples": [],
            "formats": set(),
            "image_ids": set(),
        },
    }

    paginator = s3.get_paginator("list_objects_v2")
    object_count = 0

    for page in paginator.paginate(Bucket=bucket_name, Prefix="assets/"):
        if "Contents" in page:
            for obj in page["Contents"]:
                key = obj["Key"]
                object_count += 1

                if object_count > limit:
                    break

                # Skip if it's just the directory
                if key == "assets/" or key.endswith("/"):
                    continue

                # Remove assets/ prefix
                path = key[7:]

                # Check subdirectory pattern: {id}/1_size.ext or {id}/1.ext
                if "/" in path:
                    parts = path.split("/", 1)
                    image_id = parts[0]
                    filename = parts[1]

                    # Check if it's a sized image
                    if (
                        filename.startswith("1_")
                        or filename.startswith("2_")
                        or filename == "1.avif"
                        or filename == "1.webp"
                    ):
                        patterns["subdirectory_pattern"]["count"] += 1
                        patterns["subdirectory_pattern"]["image_ids"].add(image_id)

                        if len(patterns["subdirectory_pattern"]["examples"]) < 5:
                            patterns["subdirectory_pattern"]["examples"].append(key)

                        # Extract size and format
                        if "_" in filename:
                            _, rest = filename.split("_", 1)
                            if "." in rest:
                                size, ext = rest.rsplit(".", 1)
                                patterns["subdirectory_pattern"]["sizes"].add(size)
                                patterns["subdirectory_pattern"]["formats"].add(ext)
                        elif "." in filename:
                            name, ext = filename.rsplit(".", 1)
                            patterns["subdirectory_pattern"]["formats"].add(ext)

                # Check flat patterns
                elif "_" in path:
                    if "_RECEIPT_" in path:
                        patterns["receipt_pattern"]["count"] += 1
                        if len(patterns["receipt_pattern"]["examples"]) < 5:
                            patterns["receipt_pattern"]["examples"].append(key)

                        # Extract image ID and format
                        image_id = path.split("_RECEIPT_")[0]
                        patterns["receipt_pattern"]["image_ids"].add(image_id)

                        if "." in path:
                            _, ext = path.rsplit(".", 1)
                            patterns["receipt_pattern"]["formats"].add(ext)

                    elif "_cluster_" in path:
                        patterns["cluster_pattern"]["count"] += 1
                        if len(patterns["cluster_pattern"]["examples"]) < 5:
                            patterns["cluster_pattern"]["examples"].append(key)

                        # Extract image ID and format
                        image_id = path.split("_cluster_")[0]
                        patterns["cluster_pattern"]["image_ids"].add(image_id)

                        if "." in path:
                            _, ext = path.rsplit(".", 1)
                            patterns["cluster_pattern"]["formats"].add(ext)

                    else:
                        # Check for size patterns like _thumbnail, _small, _medium
                        for size in ["thumbnail", "small", "medium"]:
                            if f"_{size}." in path:
                                patterns["flat_pattern"]["count"] += 1
                                patterns["flat_pattern"]["sizes"].add(size)

                                if len(patterns["flat_pattern"]["examples"]) < 5:
                                    patterns["flat_pattern"]["examples"].append(key)

                                # Extract image ID and format
                                image_id = path.split(f"_{size}.")[0]
                                patterns["flat_pattern"]["image_ids"].add(image_id)

                                if "." in path:
                                    _, ext = path.rsplit(".", 1)
                                    patterns["flat_pattern"]["formats"].add(ext)
                                break

        if object_count > limit:
            break

    # Convert sets to lists for JSON serialization
    for pattern in patterns.values():
        for key in ["sizes", "formats", "image_ids"]:
            if key in pattern:
                pattern[key] = sorted(list(pattern[key]))

    return patterns


def check_entity_cdn_mapping(
    dynamodb, table_name: str, image_id: str
) -> Dict[str, Any]:
    """Check IMAGE and RECEIPT entities for a specific image ID."""
    result = {
        "image_id": image_id,
        "image_entity": None,
        "receipt_entities": [],
        "has_cdn_fields": False,
    }

    # Check IMAGE entity
    try:
        image_response = dynamodb.get_item(
            TableName=table_name,
            Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": f"IMAGE#{image_id}"}},
        )

        if "Item" in image_response:
            item = image_response["Item"]
            result["image_entity"] = {
                "exists": True,
                "has_cdn": "cdn" in item,
                "has_sizes": "sizes" in item,
                "cdn_fields": [],
            }

            # Check for individual CDN fields
            cdn_field_patterns = ["cdn_", "sizes"]

            for field in item:
                for pattern in cdn_field_patterns:
                    if pattern in field:
                        result["image_entity"]["cdn_fields"].append(field)
                        result["has_cdn_fields"] = True
    except Exception as e:
        logger.error(f"Error checking IMAGE entity: {e}")

    # Check for RECEIPT entities
    try:
        scan_response = dynamodb.scan(
            TableName=table_name,
            FilterExpression="begins_with(PK, :pk_prefix) AND image_id = :image_id",
            ExpressionAttributeValues={
                ":pk_prefix": {"S": "RECEIPT#"},
                ":image_id": {"S": image_id},
            },
            Limit=10,
        )

        for item in scan_response.get("Items", []):
            receipt_id = item["PK"]["S"].replace("RECEIPT#", "")
            receipt_info = {
                "receipt_id": receipt_id,
                "has_cdn": "cdn" in item,
                "cdn_fields": [],
            }

            # Check for CDN fields
            for field in item:
                if "cdn_" in field:
                    receipt_info["cdn_fields"].append(field)
                    result["has_cdn_fields"] = True

            result["receipt_entities"].append(receipt_info)

    except Exception as e:
        logger.error(f"Error checking RECEIPT entities: {e}")

    return result


def main():
    """Main analysis function."""
    work_dir = os.path.join(parent_dir, "infra")

    # Get stack configurations
    logger.info("Getting stack configurations...")

    dev_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/dev",
        work_dir=work_dir,
    )
    dev_outputs = dev_stack.outputs()
    dev_table = dev_outputs["dynamodb_table_name"].value

    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()
    prod_table = prod_outputs["dynamodb_table_name"].value
    prod_cdn_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"DEV table: {dev_table}")
    logger.info(f"PROD table: {prod_table}")
    logger.info(f"PROD CDN bucket: {prod_cdn_bucket}")

    # Initialize clients
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    # Analyze S3 patterns
    logger.info("\n" + "=" * 60)
    logger.info("ANALYZING S3 CDN PATTERNS")
    logger.info("=" * 60)

    patterns = analyze_s3_patterns(prod_cdn_bucket, limit=5000)

    for pattern_name, pattern_data in patterns.items():
        if pattern_data["count"] > 0:
            logger.info(f"\n{pattern_name}:")
            logger.info(f"  Count: {pattern_data['count']}")
            logger.info(f"  Unique images: {len(pattern_data.get('image_ids', []))}")

            if "sizes" in pattern_data and pattern_data["sizes"]:
                logger.info(f"  Sizes: {', '.join(pattern_data['sizes'])}")

            if "formats" in pattern_data and pattern_data["formats"]:
                logger.info(f"  Formats: {', '.join(pattern_data['formats'])}")

            logger.info(f"  Examples:")
            for example in pattern_data["examples"]:
                logger.info(f"    - {example}")

    # Sample some images from the subdirectory pattern
    subdirectory_images = patterns["subdirectory_pattern"]["image_ids"][:10]

    logger.info("\n" + "=" * 60)
    logger.info("CHECKING DATABASE ENTITIES FOR SUBDIRECTORY PATTERN IMAGES")
    logger.info("=" * 60)

    dev_has_cdn = 0
    prod_has_cdn = 0

    for image_id in subdirectory_images:
        logger.info(f"\nImage ID: {image_id}")

        # Check DEV
        dev_result = check_entity_cdn_mapping(dynamodb, dev_table, image_id)
        if dev_result["image_entity"]:
            logger.info(f"  DEV IMAGE entity: EXISTS")
            if dev_result["image_entity"]["cdn_fields"]:
                logger.info(
                    f"    CDN fields: {', '.join(dev_result['image_entity']['cdn_fields'])}"
                )
                dev_has_cdn += 1
        else:
            logger.info(f"  DEV IMAGE entity: NOT FOUND")

        if dev_result["receipt_entities"]:
            logger.info(f"  DEV has {len(dev_result['receipt_entities'])} receipts")

        # Check PROD
        prod_result = check_entity_cdn_mapping(dynamodb, prod_table, image_id)
        if prod_result["image_entity"]:
            logger.info(f"  PROD IMAGE entity: EXISTS")
            if prod_result["image_entity"]["cdn_fields"]:
                logger.info(
                    f"    CDN fields: {', '.join(prod_result['image_entity']['cdn_fields'])}"
                )
                prod_has_cdn += 1
        else:
            logger.info(f"  PROD IMAGE entity: NOT FOUND")

        if prod_result["receipt_entities"]:
            logger.info(f"  PROD has {len(prod_result['receipt_entities'])} receipts")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    logger.info(f"\nS3 CDN Structure:")
    logger.info(
        f"  Subdirectory pattern (new): {patterns['subdirectory_pattern']['count']} files"
    )
    logger.info(f"  Flat pattern (old): {patterns['flat_pattern']['count']} files")
    logger.info(f"  Receipt pattern: {patterns['receipt_pattern']['count']} files")
    logger.info(f"  Cluster pattern: {patterns['cluster_pattern']['count']} files")

    logger.info(f"\nDatabase Analysis (sample of {len(subdirectory_images)} images):")
    logger.info(f"  DEV entities with CDN fields: {dev_has_cdn}")
    logger.info(f"  PROD entities with CDN fields: {prod_has_cdn}")

    logger.info("\nRECOMMENDATION:")
    logger.info(
        "The PROD CDN bucket uses the subdirectory pattern: assets/{image_id}/1_size.ext"
    )
    logger.info(
        "This pattern supports multiple sizes (thumbnail, small, medium) and formats (jpg, webp, avif)"
    )
    logger.info("The CDN fields should be mapped using this subdirectory structure.")


if __name__ == "__main__":
    main()
