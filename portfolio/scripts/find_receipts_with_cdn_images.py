#!/usr/bin/env python3
"""
Find receipts that reference images with CDN assets.
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Find receipts with CDN images."""
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

    # Sample image IDs that have CDN assets
    cdn_image_ids = [
        "03fa2d0f-33c6-43be-88b0-dae73ec26c93",
        "04a1dfe7-1858-403a-b74f-6edb23d0deb4",
        "04b16930-7baf-4539-866f-b77d8e73cff8",
        "04ebdb8a-b560-4c00-aa09-f6afa3bda458",
        "05410ae8-bf1c-488b-87e5-1f7b006a442a",
    ]

    # Check both databases for receipts with these image IDs
    for db_name, table_name in [("DEV", dev_table), ("PROD", prod_table)]:
        logger.info(f"\n{'='*60}")
        logger.info(f"Checking {db_name} database for receipts with CDN images...")
        logger.info(f"{'='*60}")

        for image_id in cdn_image_ids:
            # Query for receipts with this image_id
            scan_params = {
                "TableName": table_name,
                "FilterExpression": "begins_with(PK, :pk_prefix) AND image_id = :image_id",
                "ExpressionAttributeValues": {
                    ":pk_prefix": {"S": "RECEIPT#"},
                    ":image_id": {"S": image_id},
                },
                "Limit": 5,
            }

            try:
                response = dynamodb.scan(**scan_params)

                if response.get("Items"):
                    logger.info(f"\nImage ID: {image_id}")
                    logger.info(
                        f"  Found {len(response['Items'])} receipt(s) with this image"
                    )

                    for item in response["Items"]:
                        pk = item["PK"]["S"]
                        sk = item["SK"]["S"]
                        receipt_id = pk.replace("RECEIPT#", "")

                        logger.info(f"\n  Receipt ID: {receipt_id}")
                        logger.info(f"    PK: {pk}")
                        logger.info(f"    SK: {sk}")

                        # Check for cdn field
                        if "cdn" in item:
                            cdn_data = item["cdn"]
                            logger.info(f"    cdn field: {cdn_data}")
                        else:
                            logger.info(f"    cdn field: NOT PRESENT")

                        # Check for sizes field
                        if "sizes" in item:
                            sizes_data = item["sizes"]
                            logger.info(f"    sizes field: {sizes_data}")
                        else:
                            logger.info(f"    sizes field: NOT PRESENT")

                        # Also check for the corresponding IMAGE record
                        image_response = dynamodb.get_item(
                            TableName=table_name,
                            Key={
                                "PK": {"S": f"IMAGE#{image_id}"},
                                "SK": {"S": f"IMAGE#{image_id}"},
                            },
                        )

                        if "Item" in image_response:
                            logger.info(f"    Corresponding IMAGE record: EXISTS")
                            if "cdn" in image_response["Item"]:
                                logger.info(
                                    f"    IMAGE cdn field: {image_response['Item']['cdn']}"
                                )
                        else:
                            logger.info(f"    Corresponding IMAGE record: NOT FOUND")

            except Exception as e:
                logger.error(f"Error scanning for image {image_id}: {e}")

    # Now let's check DEV for receipts with cdn fields
    logger.info(f"\n{'='*60}")
    logger.info("Checking DEV for receipts WITH cdn fields...")
    logger.info(f"{'='*60}")

    scan_params = {
        "TableName": dev_table,
        "FilterExpression": "begins_with(PK, :pk_prefix) AND attribute_exists(cdn)",
        "ExpressionAttributeValues": {":pk_prefix": {"S": "RECEIPT#"}},
        "Limit": 10,
    }

    try:
        response = dynamodb.scan(**scan_params)

        if response.get("Items"):
            logger.info(
                f"\nFound {len(response['Items'])} receipts with cdn field in DEV"
            )

            for item in response["Items"]:
                pk = item["PK"]["S"]
                receipt_id = pk.replace("RECEIPT#", "")
                image_id = item.get("image_id", {}).get("S", "")

                logger.info(f"\nReceipt: {receipt_id}")
                logger.info(f"  Image ID: {image_id}")
                logger.info(f"  cdn field: {item['cdn']}")

                # Check if this image_id has S3 objects in PROD CDN
                if image_id:
                    paginator = s3.get_paginator("list_objects_v2")
                    s3_count = 0
                    for page in paginator.paginate(
                        Bucket=prod_cdn_bucket, Prefix=f"assets/{image_id}/"
                    ):
                        if "Contents" in page:
                            s3_count += len(page["Contents"])

                    if s3_count > 0:
                        logger.info(f"  PROD S3 CDN assets: {s3_count} files found")
                    else:
                        logger.info(f"  PROD S3 CDN assets: NOT FOUND")
        else:
            logger.info("\nNo receipts with cdn field found in DEV")

    except Exception as e:
        logger.error(f"Error scanning DEV: {e}")


if __name__ == "__main__":
    main()
