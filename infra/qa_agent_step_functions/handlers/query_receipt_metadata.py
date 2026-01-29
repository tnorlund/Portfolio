"""Zip Lambda handler: query DynamoDB for receipt metadata.

For each (image_id, receipt_id) pair from the aggregate step,
queries DynamoDB for CDN keys and image dimensions.
Writes receipts-lookup.json to S3 for the Spark job.
"""

import json
import logging
import os
from typing import Any

import boto3
from receipt_dynamo.data import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Query receipt metadata and write lookup JSON to S3.

    Args:
        event: Contains 'receipt_keys', 'execution_id', 'batch_bucket'.

    Returns:
        dict with S3 path to receipts-lookup.json.
    """
    receipt_keys = event["receipt_keys"]
    execution_id = event["execution_id"]
    batch_bucket = event["batch_bucket"]

    dynamo_client = DynamoClient(table_name=TABLE_NAME)
    lookup: dict[str, Any] = {}

    for rk in receipt_keys:
        image_id = rk["image_id"]
        receipt_id = rk["receipt_id"]
        key = f"{image_id}_{receipt_id}"

        try:
            receipt = dynamo_client.get_receipt(
                image_id=image_id, receipt_id=int(receipt_id)
            )
            if receipt:
                lookup[key] = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "cdn_s3_key": getattr(receipt, "cdn_s3_key", None),
                    "cdn_webp_s3_key": getattr(receipt, "cdn_webp_s3_key", None),
                    "cdn_avif_s3_key": getattr(receipt, "cdn_avif_s3_key", None),
                    "cdn_medium_s3_key": getattr(receipt, "cdn_medium_s3_key", None),
                    "cdn_medium_webp_s3_key": getattr(
                        receipt, "cdn_medium_webp_s3_key", None
                    ),
                    "cdn_medium_avif_s3_key": getattr(
                        receipt, "cdn_medium_avif_s3_key", None
                    ),
                    "width": getattr(receipt, "width", None),
                    "height": getattr(receipt, "height", None),
                }
            else:
                logger.warning("Receipt not found: %s/%s", image_id, receipt_id)
        except Exception:
            logger.exception(
                "Error querying receipt %s/%s", image_id, receipt_id
            )

    # Write lookup JSON to S3
    lookup_key = f"qa-runs/{execution_id}/receipts-lookup.json"
    s3_client.put_object(
        Bucket=batch_bucket,
        Key=lookup_key,
        Body=json.dumps(lookup, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        "Wrote %d receipt lookups to s3://%s/%s",
        len(lookup),
        batch_bucket,
        lookup_key,
    )

    return {
        "execution_id": execution_id,
        "batch_bucket": batch_bucket,
        "receipts_lookup_path": f"s3://{batch_bucket}/{lookup_key}",
        "receipts_found": len(lookup),
    }
