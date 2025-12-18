#!/usr/bin/env python3
"""
Check image CDN mapping between DEV and PROD databases and S3 buckets.
"""

import logging
import os
import sys
from datetime import datetime
import boto3
from decimal import Decimal

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from pulumi import automation as auto
from receipt_dynamo.entities.image import Image
from receipt_dynamo.entities.receipt import Receipt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_s3_objects(bucket_name, prefix):
    """Check S3 objects in a specific prefix."""
    s3 = boto3.client("s3")

    objects = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        if "Contents" in page:
            for obj in page["Contents"]:
                objects.append(obj["Key"])

    return objects


def main():
    """Check image CDN mapping."""
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

    # Initialize DynamoDB clients
    dynamodb = boto3.client("dynamodb")

    # Sample some image IDs from PROD CDN
    sample_ids = [
        "03fa2d0f-33c6-43be-88b0-dae73ec26c93",
        "04a1dfe7-1858-403a-b74f-6edb23d0deb4",
        "04b16930-7baf-4539-866f-b77d8e73cff8",
        "04ebdb8a-b560-4c00-aa09-f6afa3bda458",
        "05410ae8-bf1c-488b-87e5-1f7b006a442a",
    ]

    logger.info("\n" + "=" * 60)
    logger.info("Checking sample image IDs...")
    logger.info("=" * 60)

    for image_id in sample_ids:
        logger.info(f"\nImage ID: {image_id}")

        # Check S3 objects
        s3_prefix = f"assets/{image_id}/"
        s3_objects = check_s3_objects(prod_cdn_bucket, s3_prefix)
        logger.info(f"  S3 objects found: {len(s3_objects)}")
        for obj in sorted(s3_objects):
            logger.info(f"    - {obj}")

        # Check DEV database
        try:
            dev_response = dynamodb.get_item(
                TableName=dev_table,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"IMAGE#{image_id}"},
                },
            )
            if "Item" in dev_response:
                dev_item = dev_response["Item"]
                logger.info(f"  DEV record found:")
                if "cdn" in dev_item:
                    logger.info(f"    - cdn field: {dev_item['cdn']}")
                else:
                    logger.info(f"    - cdn field: NOT PRESENT")
                if "sizes" in dev_item:
                    logger.info(f"    - sizes field: {dev_item['sizes']}")
                else:
                    logger.info(f"    - sizes field: NOT PRESENT")
            else:
                logger.info(f"  DEV record: NOT FOUND")
        except Exception as e:
            logger.error(f"  Error checking DEV: {e}")

        # Check PROD database
        try:
            prod_response = dynamodb.get_item(
                TableName=prod_table,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"IMAGE#{image_id}"},
                },
            )
            if "Item" in prod_response:
                prod_item = prod_response["Item"]
                logger.info(f"  PROD record found:")
                if "cdn" in prod_item:
                    logger.info(f"    - cdn field: {prod_item['cdn']}")
                else:
                    logger.info(f"    - cdn field: NOT PRESENT")
                if "sizes" in prod_item:
                    logger.info(f"    - sizes field: {prod_item['sizes']}")
                else:
                    logger.info(f"    - sizes field: NOT PRESENT")
            else:
                logger.info(f"  PROD record: NOT FOUND")
        except Exception as e:
            logger.error(f"  Error checking PROD: {e}")

    # Now check for receipts
    logger.info("\n" + "=" * 60)
    logger.info("Checking receipts with CDN assets...")
    logger.info("=" * 60)

    # Query for some receipts in PROD
    scan_params = {
        "TableName": prod_table,
        "FilterExpression": "begins_with(PK, :pk_prefix) AND attribute_exists(image_id)",
        "ExpressionAttributeValues": {":pk_prefix": {"S": "RECEIPT#"}},
        "Limit": 10,
    }

    response = dynamodb.scan(**scan_params)

    for item in response.get("Items", []):
        pk = item["PK"]["S"]
        receipt_id = pk.replace("RECEIPT#", "")
        image_id = item.get("image_id", {}).get("S", "")

        if image_id:
            logger.info(f"\nReceipt: {receipt_id}")
            logger.info(f"  Image ID: {image_id}")

            # Check if this image has CDN assets
            s3_prefix = f"assets/{image_id}/"
            s3_objects = check_s3_objects(prod_cdn_bucket, s3_prefix)
            if s3_objects:
                logger.info(f"  S3 CDN assets found: {len(s3_objects)}")
            else:
                logger.info(f"  No S3 CDN assets found")

            # Check receipt CDN fields
            if "cdn" in item:
                logger.info(f"  Receipt cdn field: {item['cdn']}")
            else:
                logger.info(f"  Receipt cdn field: NOT PRESENT")


if __name__ == "__main__":
    main()
