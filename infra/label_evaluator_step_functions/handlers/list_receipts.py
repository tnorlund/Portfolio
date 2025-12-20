"""List receipts by merchant for label evaluation.

This handler queries DynamoDB for receipts matching the specified merchant
and creates a manifest file in S3 for the distributed map to process.
"""

import json
import logging
import os
from typing import Any, Dict, List

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    List receipts by merchant name and create processing manifest.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "batch_size": 10,
        "merchant_name": "Sprouts Farmers Market",
        "limit": 100,
        "max_training_receipts": 50
    }

    Output:
    {
        "manifest_s3_key": "manifests/{exec}/receipts.json",
        "total_receipts": 150,
        "total_batches": 15,
        "merchant_name": "Sprouts Farmers Market",
        "max_training_receipts": 50
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    batch_size = event.get("batch_size", 10)
    merchant_name = event.get("merchant_name")
    limit = event.get("limit", 100)
    max_training_receipts = event.get("max_training_receipts", 50)

    if not merchant_name:
        raise ValueError("merchant_name is required")

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        f"Listing receipts for merchant '{merchant_name}' "
        f"(limit={limit}, batch_size={batch_size})"
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Query receipts by merchant
    metadatas, _ = dynamo.get_receipt_metadatas_by_merchant(
        merchant_name, limit=limit
    )

    if not metadatas:
        logger.info(f"No receipts found for merchant '{merchant_name}'")
        return {
            "manifest_s3_key": None,
            "total_receipts": 0,
            "total_batches": 0,
            "merchant_name": merchant_name,
            "max_training_receipts": max_training_receipts,
            "receipt_batches": [],
        }

    # Create receipt list
    receipts = [
        {
            "image_id": m.image_id,
            "receipt_id": m.receipt_id,
            "merchant_name": m.canonical_merchant_name or m.merchant_name,
        }
        for m in metadatas
    ]

    logger.info(f"Found {len(receipts)} receipts for merchant '{merchant_name}'")

    # Create batches for distributed map
    batches: List[List[Dict[str, Any]]] = []
    for i in range(0, len(receipts), batch_size):
        batches.append(receipts[i : i + batch_size])

    # Upload manifest to S3
    manifest = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "total_receipts": len(receipts),
        "batch_size": batch_size,
        "max_training_receipts": max_training_receipts,
        "receipts": receipts,
    }

    manifest_key = f"manifests/{execution_id}/receipts.json"
    s3.put_object(
        Bucket=batch_bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        f"Created manifest at s3://{batch_bucket}/{manifest_key} "
        f"with {len(batches)} batches"
    )

    return {
        "manifest_s3_key": manifest_key,
        "total_receipts": len(receipts),
        "total_batches": len(batches),
        "merchant_name": merchant_name,
        "max_training_receipts": max_training_receipts,
        "receipt_batches": batches,
    }
