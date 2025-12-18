#!/usr/bin/env python3
"""
Find records in DEV that have CDN fields populated.
"""

import logging
import os
import sys
import json
import boto3

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
    """Find DEV records with CDN fields."""
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
    prod_cdn_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"DEV table: {dev_table}")
    logger.info(f"PROD CDN bucket: {prod_cdn_bucket}")

    # Initialize clients
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    # First check for IMAGE records with cdn field
    logger.info("\n" + "=" * 60)
    logger.info("Checking for IMAGE records with cdn field in DEV...")
    logger.info("=" * 60)

    scan_params = {
        "TableName": dev_table,
        "FilterExpression": "begins_with(PK, :pk_prefix) AND attribute_exists(cdn)",
        "ExpressionAttributeValues": {":pk_prefix": {"S": "IMAGE#"}},
        "Limit": 10,
    }

    response = dynamodb.scan(**scan_params)

    if response.get("Items"):
        logger.info(f"\nFound {len(response['Items'])} IMAGE records with cdn field")

        for item in response["Items"]:
            pk = item["PK"]["S"]
            image_id = pk.replace("IMAGE#", "")

            logger.info(f"\nImage ID: {image_id}")
            logger.info(f"  cdn field: {json.dumps(item['cdn'], indent=2)}")

            # Check if S3 objects exist in PROD
            paginator = s3.get_paginator("list_objects_v2")
            s3_count = 0
            for page in paginator.paginate(
                Bucket=prod_cdn_bucket, Prefix=f"assets/{image_id}/"
            ):
                if "Contents" in page:
                    s3_count += len(page["Contents"])

            logger.info(f"  PROD S3 objects: {s3_count} files")

            # Check for receipts with this image_id
            receipt_scan = {
                "TableName": dev_table,
                "FilterExpression": "begins_with(PK, :pk_prefix) AND image_id = :image_id",
                "ExpressionAttributeValues": {
                    ":pk_prefix": {"S": "RECEIPT#"},
                    ":image_id": {"S": image_id},
                },
                "Limit": 3,
            }

            receipt_response = dynamodb.scan(**receipt_scan)
            if receipt_response.get("Items"):
                logger.info(f"  Associated receipts: {len(receipt_response['Items'])}")
                for r in receipt_response["Items"]:
                    receipt_id = r["PK"]["S"].replace("RECEIPT#", "")
                    logger.info(f"    - {receipt_id}")
    else:
        logger.info("\nNo IMAGE records with cdn field found in DEV")

    # Check for RECEIPT records with cdn field
    logger.info("\n" + "=" * 60)
    logger.info("Checking for RECEIPT records with cdn field in DEV...")
    logger.info("=" * 60)

    scan_params = {
        "TableName": dev_table,
        "FilterExpression": "begins_with(PK, :pk_prefix) AND attribute_exists(cdn)",
        "ExpressionAttributeValues": {":pk_prefix": {"S": "RECEIPT#"}},
        "Limit": 10,
    }

    response = dynamodb.scan(**scan_params)

    if response.get("Items"):
        logger.info(f"\nFound {len(response['Items'])} RECEIPT records with cdn field")

        for item in response["Items"]:
            pk = item["PK"]["S"]
            sk = item["SK"]["S"]
            receipt_id = pk.replace("RECEIPT#", "")
            image_id = item.get("image_id", {}).get("S", "")

            logger.info(f"\nReceipt ID: {receipt_id}")
            logger.info(f"  SK: {sk}")
            logger.info(f"  Image ID: {image_id}")
            logger.info(f"  cdn field: {json.dumps(item['cdn'], indent=2)}")

            # Check if S3 objects exist in PROD for this image
            if image_id:
                paginator = s3.get_paginator("list_objects_v2")
                s3_count = 0
                for page in paginator.paginate(
                    Bucket=prod_cdn_bucket, Prefix=f"assets/{image_id}/"
                ):
                    if "Contents" in page:
                        s3_count += len(page["Contents"])

                logger.info(f"  PROD S3 objects for image: {s3_count} files")
    else:
        logger.info("\nNo RECEIPT records with cdn field found in DEV")

    # Also check for records with sizes field
    logger.info("\n" + "=" * 60)
    logger.info("Checking for records with sizes field in DEV...")
    logger.info("=" * 60)

    for entity_type in ["IMAGE", "RECEIPT"]:
        scan_params = {
            "TableName": dev_table,
            "FilterExpression": "begins_with(PK, :pk_prefix) AND attribute_exists(sizes)",
            "ExpressionAttributeValues": {":pk_prefix": {"S": f"{entity_type}#"}},
            "Limit": 5,
        }

        response = dynamodb.scan(**scan_params)

        if response.get("Items"):
            logger.info(
                f"\nFound {len(response['Items'])} {entity_type} records with sizes field"
            )

            for item in response["Items"]:
                pk = item["PK"]["S"]
                entity_id = pk.replace(f"{entity_type}#", "")

                logger.info(f"\n{entity_type} ID: {entity_id}")
                logger.info(f"  sizes field: {json.dumps(item['sizes'], indent=2)}")


if __name__ == "__main__":
    main()
