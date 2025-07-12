#!/usr/bin/env python3
"""
Analyze PROD CDN structure and find corresponding database records.
"""

import logging
import os
import sys
from datetime import datetime
import boto3
from decimal import Decimal
import re

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

def extract_image_id_from_s3_key(key):
    """Extract image ID from S3 key."""
    # Pattern: assets/{image_id}/... or assets/{image_id}.png
    match = re.match(r'assets/([a-f0-9-]+)(?:/|\.)', key)
    if match:
        return match.group(1)
    return None

def main():
    """Analyze PROD CDN structure."""
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
    
    # Get a sample of S3 objects with sized images
    logger.info("\nAnalyzing S3 CDN structure...")
    
    paginator = s3.get_paginator("list_objects_v2")
    
    # Collect unique image IDs that have sized images
    image_ids_with_sizes = set()
    
    for page in paginator.paginate(Bucket=prod_cdn_bucket, Prefix="assets/"):
        if "Contents" in page:
            for obj in page["Contents"]:
                key = obj["Key"]
                # Look for sized images pattern
                if any(size in key for size in ["_thumbnail", "_small", "_medium"]):
                    image_id = extract_image_id_from_s3_key(key)
                    if image_id:
                        image_ids_with_sizes.add(image_id)
        
        # Stop after collecting enough samples
        if len(image_ids_with_sizes) >= 20:
            break
    
    logger.info(f"\nFound {len(image_ids_with_sizes)} unique image IDs with sized images")
    
    # Check first 10 image IDs in detail
    for i, image_id in enumerate(list(image_ids_with_sizes)[:10]):
        logger.info(f"\n{'='*60}")
        logger.info(f"Image ID {i+1}: {image_id}")
        logger.info(f"{'='*60}")
        
        # List all S3 objects for this image
        objects = []
        for page in paginator.paginate(Bucket=prod_cdn_bucket, Prefix=f"assets/{image_id}/"):
            if "Contents" in page:
                for obj in page["Contents"]:
                    objects.append(obj["Key"])
        
        logger.info(f"\nS3 objects ({len(objects)} total):")
        for obj in sorted(objects)[:20]:  # Show first 20
            logger.info(f"  - {obj}")
        
        # Check DEV database
        logger.info("\nDEV Database:")
        
        # Check for IMAGE record
        try:
            image_response = dynamodb.get_item(
                TableName=dev_table,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"IMAGE#{image_id}"}
                }
            )
            if "Item" in image_response:
                logger.info("  IMAGE record: FOUND")
                if "cdn" in image_response["Item"]:
                    logger.info(f"  cdn field: {image_response['Item']['cdn']}")
                if "sizes" in image_response["Item"]:
                    logger.info(f"  sizes field: Present")
            else:
                logger.info("  IMAGE record: NOT FOUND")
        except Exception as e:
            logger.error(f"  Error checking IMAGE: {e}")
        
        # Check for RECEIPT records with this image_id
        try:
            scan_response = dynamodb.scan(
                TableName=dev_table,
                FilterExpression="begins_with(PK, :pk_prefix) AND image_id = :image_id",
                ExpressionAttributeValues={
                    ":pk_prefix": {"S": "RECEIPT#"},
                    ":image_id": {"S": image_id}
                },
                Limit=1
            )
            
            if scan_response.get("Items"):
                logger.info("  RECEIPT with this image_id: FOUND")
                receipt = scan_response["Items"][0]
                receipt_id = receipt["PK"]["S"].replace("RECEIPT#", "")
                logger.info(f"  Receipt ID: {receipt_id}")
                if "cdn" in receipt:
                    logger.info(f"  Receipt cdn field: {receipt['cdn']}")
            else:
                logger.info("  RECEIPT with this image_id: NOT FOUND")
        except Exception as e:
            logger.error(f"  Error checking RECEIPT: {e}")
        
        # Check PROD database
        logger.info("\nPROD Database:")
        
        # Check for IMAGE record
        try:
            image_response = dynamodb.get_item(
                TableName=prod_table,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"IMAGE#{image_id}"}
                }
            )
            if "Item" in image_response:
                logger.info("  IMAGE record: FOUND")
                if "cdn" in image_response["Item"]:
                    logger.info(f"  cdn field: {image_response['Item']['cdn']}")
                if "sizes" in image_response["Item"]:
                    logger.info(f"  sizes field: Present")
            else:
                logger.info("  IMAGE record: NOT FOUND")
        except Exception as e:
            logger.error(f"  Error checking IMAGE: {e}")
        
        # Check for RECEIPT records with this image_id
        try:
            scan_response = dynamodb.scan(
                TableName=prod_table,
                FilterExpression="begins_with(PK, :pk_prefix) AND image_id = :image_id",
                ExpressionAttributeValues={
                    ":pk_prefix": {"S": "RECEIPT#"},
                    ":image_id": {"S": image_id}
                },
                Limit=1
            )
            
            if scan_response.get("Items"):
                logger.info("  RECEIPT with this image_id: FOUND")
                receipt = scan_response["Items"][0]
                receipt_id = receipt["PK"]["S"].replace("RECEIPT#", "")
                logger.info(f"  Receipt ID: {receipt_id}")
                if "cdn" in receipt:
                    logger.info(f"  Receipt cdn field: {receipt['cdn']}")
            else:
                logger.info("  RECEIPT with this image_id: NOT FOUND")
        except Exception as e:
            logger.error(f"  Error checking RECEIPT: {e}")

if __name__ == "__main__":
    main()