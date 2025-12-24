"""List receipts for a merchant for the traced Step Function.

This is a zip-based Lambda that doesn't have access to langsmith.
Tracing is handled by the container-based Lambdas.
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from handlers.evaluator_types import ListReceiptsOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "ListReceiptsOutput":
    """
    List receipts for a specific merchant.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "batch_size": 10,
        "merchant_name": "Sprouts Farmers Market",
        "max_training_receipts": 50,
        "limit": null
    }

    Output:
    {
        "receipt_batches": [[{image_id, receipt_id}, ...], ...],
        "total_receipts": 184
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    batch_size = event.get("batch_size", 10)
    merchant_name = event.get("merchant_name")
    # Handle merchant_name from merchant object (Map state)
    if not merchant_name and "merchant" in event:
        merchant_name = event["merchant"].get("merchant_name")
    max_training_receipts = event.get("max_training_receipts", 50)
    limit = event.get("limit")

    if not merchant_name:
        raise ValueError("merchant_name is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        "Listing receipts for merchant '%s' (batch_size=%s, limit=%s)",
        merchant_name,
        batch_size,
        limit,
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Get receipts for this merchant
    all_receipts = []
    last_key = None
    query_limit = min(limit, 1000) if limit else 1000

    while True:
        places, last_key = dynamo.get_receipt_places_by_merchant(
            merchant_name,
            limit=query_limit,
            last_evaluated_key=last_key,
        )

        for place in places:
            all_receipts.append({
                "image_id": place.image_id,
                "receipt_id": place.receipt_id,
                "merchant_name": place.merchant_name,
            })

            if limit and len(all_receipts) >= limit:
                break

        if not last_key or (limit and len(all_receipts) >= limit):
            break

    logger.info("Found %s receipts for merchant '%s'", len(all_receipts), merchant_name)

    # Split into batches
    receipt_batches = []
    for i in range(0, len(all_receipts), batch_size):
        receipt_batches.append(all_receipts[i:i + batch_size])

    # Save batch manifest to S3
    manifest = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "total_receipts": len(all_receipts),
        "batch_count": len(receipt_batches),
        "batch_size": batch_size,
        "max_training_receipts": max_training_receipts,
    }

    # Create safe merchant key
    safe_merchant = "".join(
        c if c.isalnum() else "_" for c in merchant_name
    )[:50]
    manifest_key = f"manifests/{execution_id}/{safe_merchant}_receipts.json"

    manifest_s3_key: str | None = manifest_key
    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        logger.exception("Failed to upload manifest")
        manifest_s3_key = None

    return {
        "receipt_batches": receipt_batches,
        "total_receipts": len(all_receipts),
        "total_batches": len(receipt_batches),
        "merchant_name": merchant_name,
        "max_training_receipts": max_training_receipts,
        "manifest_s3_key": manifest_s3_key,
    }
