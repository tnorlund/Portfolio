"""List unique merchants with sufficient receipts for label evaluation.

This handler queries DynamoDB for all ReceiptPlaces, groups by merchant,
and returns merchants that meet the minimum receipt threshold.
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
from collections import Counter
from typing import TYPE_CHECKING, Any

import boto3

from evaluator_types import MerchantInfo

if TYPE_CHECKING:
    from evaluator_types import ListMerchantsOutput


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

# Default minimum receipts for pattern learning
DEFAULT_MIN_RECEIPTS = 5


def handler(event: dict[str, Any], _context: Any) -> "ListMerchantsOutput":
    """
    List unique merchants that have enough receipts for pattern learning.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "min_receipts": 5,  # Minimum receipts required per merchant
        "max_training_receipts": 50,  # Max training receipts per merchant
        "skip_llm_review": true
    }

    Output:
    {
        "merchants": [
            {"merchant_name": "Sprouts Farmers Market", "receipt_count": 184},
            {"merchant_name": "Costco Wholesale", "receipt_count": 27},
            ...
        ],
        "total_merchants": 18,
        "total_receipts": 373,
        "skipped_merchants": 120,  # Merchants below threshold
        "min_receipts": 5
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    min_receipts = event.get("min_receipts", DEFAULT_MIN_RECEIPTS)
    max_training_receipts = event.get("max_training_receipts", 50)
    skip_llm_review = event.get("skip_llm_review", True)

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info("Listing merchants with min_receipts=%s", min_receipts)

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Get all ReceiptPlaces with pagination
    all_places = []
    result = dynamo.list_receipt_places()
    all_places.extend(result[0])

    while result[1]:  # Continue if there's more data
        result = dynamo.list_receipt_places(last_evaluated_key=result[1])
        all_places.extend(result[0])

    logger.info("Found %s total receipt places", len(all_places))

    # Count receipts per merchant
    merchant_counts: Counter = Counter()
    for place in all_places:
        merchant_counts[place.merchant_name] += 1

    # Filter merchants by minimum receipt threshold
    qualifying_merchants: list[MerchantInfo] = []
    skipped_count = 0

    for merchant_name, count in sorted(
        merchant_counts.items(), key=lambda x: -x[1]
    ):
        if count >= min_receipts:
            qualifying_merchants.append(
                MerchantInfo(merchant_name=merchant_name, receipt_count=count)
            )
        else:
            skipped_count += 1

    total_receipts = sum(m["receipt_count"] for m in qualifying_merchants)

    logger.info(
        "Found %s merchants with >= %s receipts (%s total receipts). "
        "Skipped %s merchants.",
        len(qualifying_merchants),
        min_receipts,
        total_receipts,
        skipped_count,
    )

    # Upload merchant list to S3 for reference
    manifest = {
        "execution_id": execution_id,
        "min_receipts": min_receipts,
        "max_training_receipts": max_training_receipts,
        "skip_llm_review": skip_llm_review,
        "merchants": qualifying_merchants,
        "total_merchants": len(qualifying_merchants),
        "total_receipts": total_receipts,
        "skipped_merchants": skipped_count,
    }

    manifest_key = f"manifests/{execution_id}/merchants.json"
    s3.put_object(
        Bucket=batch_bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        "Created merchant manifest at s3://%s/%s",
        batch_bucket,
        manifest_key,
    )

    return {
        "merchants": qualifying_merchants,
        "total_merchants": len(qualifying_merchants),
        "total_receipts": total_receipts,
        "skipped_merchants": skipped_count,
        "min_receipts": min_receipts,
        "max_training_receipts": max_training_receipts,
        "skip_llm_review": skip_llm_review,
        "manifest_s3_key": manifest_key,
    }
