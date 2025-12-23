"""Fetch training data (other merchant receipts) for pattern learning.

This handler fetches other receipts from the same merchant for pattern
learning and uploads the serialized data to S3. Uses caching to avoid
redundant fetches across receipts from the same merchant.
"""

# pylint: disable=import-outside-toplevel,import-error
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError
from serialization import serialize_label, serialize_place, serialize_word

from .utils.s3_helpers import get_merchant_hash

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Fetch other receipts from the same merchant for pattern learning.

    Input:
    {
        "merchant_name": "Sprouts Farmers Market",
        "exclude_image_id": "img1",
        "exclude_receipt_id": 1,
        "max_receipts": 50,
        "execution_id": "abc123",
        "batch_bucket": "bucket-name"
    }

    Output:
    {
        "training_s3_key": "training/{exec}/{merchant_hash}.json",
        "merchant_name": "Sprouts Farmers Market",
        "receipt_count": 47
    }
    """
    merchant_name = event.get("merchant_name")
    exclude_image_id = event.get("exclude_image_id")
    exclude_receipt_id = event.get("exclude_receipt_id")
    max_receipts = event.get("max_receipts", 50)
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    if not merchant_name:
        raise ValueError("merchant_name is required")

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Create cache key from merchant name
    merchant_hash = get_merchant_hash(merchant_name)
    training_key = f"training/{execution_id}/{merchant_hash}.json"

    # Check if training data already exists (cached from another receipt)
    try:
        s3.head_object(Bucket=batch_bucket, Key=training_key)
        logger.info(
            "Training data already cached at s3://%s/%s",
            batch_bucket,
            training_key,
        )

        # Get receipt count from existing file
        response = s3.get_object(Bucket=batch_bucket, Key=training_key)
        cached_data = json.loads(response["Body"].read().decode("utf-8"))
        receipt_count = len(cached_data.get("receipts", []))

        return {
            "training_s3_key": training_key,
            "merchant_name": merchant_name,
            "receipt_count": receipt_count,
            "cached": True,
        }
    except ClientError as e:
        if e.response["Error"]["Code"] not in {"404", "NoSuchKey"}:
            raise
        # Not cached, need to fetch

    logger.info(
        "Fetching training data for merchant '%s' (max_receipts=%s)",
        merchant_name,
        max_receipts,
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Query other receipts from same merchant (using ReceiptPlace)
    other_places, _ = dynamo.get_receipt_places_by_merchant(
        merchant_name, limit=max_receipts + 1
    )

    # Exclude target receipt
    other_places = [
        p
        for p in other_places
        if not (
            p.image_id == exclude_image_id
            and p.receipt_id == exclude_receipt_id
        )
    ][:max_receipts]

    if not other_places:
        logger.info("No other receipts found for merchant '%s'", merchant_name)
        # Upload empty training data
        training_data = {
            "merchant_name": merchant_name,
            "receipts": [],
        }
        s3.put_object(
            Bucket=batch_bucket,
            Key=training_key,
            Body=json.dumps(training_data).encode("utf-8"),
            ContentType="application/json",
        )
        return {
            "training_s3_key": training_key,
            "merchant_name": merchant_name,
            "receipt_count": 0,
            "cached": False,
        }

    logger.info(
        "Fetching words/labels for %s training receipts",
        len(other_places),
    )

    # Fetch and serialize each receipt
    receipts_data = []
    for place in other_places:
        try:
            words_result = dynamo.list_receipt_words_from_receipt(
                place.image_id, place.receipt_id
            )
            words = words_result[0] if isinstance(words_result, tuple) else words_result

            labels_result = dynamo.list_receipt_word_labels_for_receipt(
                place.image_id, place.receipt_id
            )
            labels = labels_result[0] if isinstance(labels_result, tuple) else labels_result

            receipts_data.append(
                {
                    "place": serialize_place(place),
                    "words": [serialize_word(w) for w in words],
                    "labels": [serialize_label(label) for label in labels],
                }
            )
        except Exception:
            logger.exception(
                "Error fetching receipt %s#%s",
                place.image_id,
                place.receipt_id,
            )
            continue

    logger.info(
        "Successfully fetched %s training receipts",
        len(receipts_data),
    )

    # Upload training data to S3
    training_data = {
        "merchant_name": merchant_name,
        "receipts": receipts_data,
    }

    s3.put_object(
        Bucket=batch_bucket,
        Key=training_key,
        Body=json.dumps(training_data).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        "Uploaded training data to s3://%s/%s",
        batch_bucket,
        training_key,
    )

    return {
        "training_s3_key": training_key,
        "merchant_name": merchant_name,
        "receipt_count": len(receipts_data),
        "cached": False,
    }
