#!/usr/bin/env python3
"""
Comprehensive check for image data associated with corrupted entities.
Checks multiple buckets, file patterns, and CDN versions.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional
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


def comprehensive_image_check(table_name: str, raw_bucket: str, cdn_bucket: str):
    """Comprehensive check for any image data associated with corrupted entities."""
    dynamodb = boto3.resource("dynamodb")
    s3 = boto3.client("s3")
    table = dynamodb.Table(table_name)

    # Get a sample of corrupted entities to check
    logger.info("Getting sample of corrupted IMAGE entities...")

    scan_kwargs = {
        "FilterExpression": "#type = :image_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {
            ":image_type": "IMAGE",
            ":pk_prefix": "IMAGE#",
            ":sk_prefix": "IMAGE#",
        },
        "Limit": 20,  # Just check first 20 for analysis
    }

    response = table.scan(**scan_kwargs)
    sample_entities = []

    for item in response.get("Items", []):
        # Check if Image entity is corrupted
        required_fields = [
            "width",
            "height",
            "timestamp_added",
            "raw_s3_bucket",
            "raw_s3_key",
            "image_type",
        ]
        missing_fields = [field for field in required_fields if field not in item]

        if missing_fields:
            image_id = item["PK"].replace("IMAGE#", "")
            sample_entities.append(
                {"item": item, "image_id": image_id, "missing_fields": missing_fields}
            )

    logger.info(f"Checking {len(sample_entities)} sample corrupted entities...")

    # Check multiple sources for each entity
    results = {
        "entities_checked": len(sample_entities),
        "raw_images_found": 0,
        "cdn_images_found": 0,
        "processed_images_found": 0,
        "total_with_any_image": 0,
        "no_images_found": 0,
        "detailed_results": [],
    }

    for i, entity in enumerate(sample_entities):
        image_id = entity["image_id"]
        logger.info(f"Checking {i+1}/{len(sample_entities)}: {image_id}")

        entity_result = {
            "image_id": image_id,
            "raw_bucket_files": [],
            "cdn_bucket_files": [],
            "has_any_image": False,
        }

        # 1. Check raw bucket with various extensions and patterns
        raw_extensions = ["jpg", "jpeg", "png", "tiff", "tif", "webp", "pdf", "heic"]
        raw_patterns = [
            f"{image_id}.{ext}",  # Direct filename
            f"raw/{image_id}.{ext}",  # In raw subfolder
            f"images/{image_id}.{ext}",  # In images subfolder
            f"uploads/{image_id}.{ext}",  # In uploads subfolder
        ]

        for pattern in raw_patterns:
            for ext in raw_extensions:
                key = pattern.format(ext=ext) if "{ext}" in pattern else pattern
                try:
                    s3.head_object(Bucket=raw_bucket, Key=key)
                    entity_result["raw_bucket_files"].append(key)
                    results["raw_images_found"] += 1
                    entity_result["has_any_image"] = True
                    logger.info(f"  Found raw image: {key}")
                except s3.exceptions.ClientError:
                    pass

        # 2. Check CDN bucket for processed versions
        cdn_patterns = [
            f"{image_id}.jpg",  # Original
            f"{image_id}_thumbnail.jpg",  # Thumbnail
            f"{image_id}_small.jpg",  # Small
            f"{image_id}_medium.jpg",  # Medium
            f"{image_id}.webp",  # WebP versions
            f"{image_id}_thumbnail.webp",
            f"{image_id}_small.webp",
            f"{image_id}_medium.webp",
            f"{image_id}.avif",  # AVIF versions
            f"{image_id}_thumbnail.avif",
            f"{image_id}_small.avif",
            f"{image_id}_medium.avif",
        ]

        for key in cdn_patterns:
            try:
                s3.head_object(Bucket=cdn_bucket, Key=key)
                entity_result["cdn_bucket_files"].append(key)
                results["cdn_images_found"] += 1
                entity_result["has_any_image"] = True
                logger.info(f"  Found CDN image: {key}")
            except s3.exceptions.ClientError:
                pass

        # 3. Also check if there are any files in either bucket that contain the image_id
        # (in case naming pattern is different)
        try:
            # List objects with image_id as prefix in raw bucket
            raw_objects = s3.list_objects_v2(
                Bucket=raw_bucket, Prefix=image_id, MaxKeys=10
            )
            for obj in raw_objects.get("Contents", []):
                if obj["Key"] not in entity_result["raw_bucket_files"]:
                    entity_result["raw_bucket_files"].append(obj["Key"])
                    entity_result["has_any_image"] = True
                    logger.info(f"  Found raw image (prefix match): {obj['Key']}")
        except Exception as e:
            logger.debug(f"Error listing raw objects for {image_id}: {e}")

        try:
            # List objects with image_id as prefix in CDN bucket
            cdn_objects = s3.list_objects_v2(
                Bucket=cdn_bucket, Prefix=image_id, MaxKeys=10
            )
            for obj in cdn_objects.get("Contents", []):
                if obj["Key"] not in entity_result["cdn_bucket_files"]:
                    entity_result["cdn_bucket_files"].append(obj["Key"])
                    entity_result["has_any_image"] = True
                    logger.info(f"  Found CDN image (prefix match): {obj['Key']}")
        except Exception as e:
            logger.debug(f"Error listing CDN objects for {image_id}: {e}")

        # Update counters
        if entity_result["has_any_image"]:
            results["total_with_any_image"] += 1
        else:
            results["no_images_found"] += 1
            logger.warning(f"  NO images found for {image_id}")

        results["detailed_results"].append(entity_result)

    # Print comprehensive summary
    logger.info("\n" + "=" * 60)
    logger.info("COMPREHENSIVE IMAGE CHECK RESULTS")
    logger.info("=" * 60)
    logger.info(f"Entities checked: {results['entities_checked']}")
    logger.info(f"Entities with ANY image data: {results['total_with_any_image']}")
    logger.info(f"Entities with NO image data: {results['no_images_found']}")
    logger.info(f"Raw images found: {results['raw_images_found']}")
    logger.info(f"CDN images found: {results['cdn_images_found']}")

    # Show detailed breakdown
    logger.info(f"\nDetailed breakdown:")
    for result in results["detailed_results"]:
        image_id = result["image_id"]
        raw_count = len(result["raw_bucket_files"])
        cdn_count = len(result["cdn_bucket_files"])

        if result["has_any_image"]:
            logger.info(f"  {image_id}: {raw_count} raw, {cdn_count} CDN files")
            if raw_count > 0:
                logger.info(f"    Raw files: {result['raw_bucket_files']}")
            if cdn_count > 0:
                logger.info(f"    CDN files: {result['cdn_bucket_files']}")
        else:
            logger.info(f"  {image_id}: NO FILES FOUND")

    # Analysis and recommendations
    logger.info(f"\n" + "=" * 60)
    logger.info("ANALYSIS & RECOMMENDATIONS")
    logger.info("=" * 60)

    if results["total_with_any_image"] > 0:
        logger.info(
            f"✅ {results['total_with_any_image']} entities have image data and can potentially be reconstructed"
        )
        logger.info(
            "   Recommendation: Create reconstruction script for these entities"
        )

    if results["no_images_found"] > 0:
        logger.info(f"❌ {results['no_images_found']} entities have NO image data")
        logger.info(
            "   Recommendation: These are phantom entities and should be deleted"
        )

    if results["cdn_images_found"] > 0 and results["raw_images_found"] == 0:
        logger.info("⚠️  Found CDN images but no raw images")
        logger.info(
            "   This suggests processing occurred but raw files were cleaned up"
        )
        logger.info("   CDN images can still be used to reconstruct entity metadata")

    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Comprehensive check for image data in corrupted entities"
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

    # Run comprehensive check
    comprehensive_image_check(prod_table, raw_bucket, cdn_bucket)


if __name__ == "__main__":
    main()
