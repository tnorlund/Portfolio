"""Fetch receipt data (words and labels) for evaluation.

This handler fetches words and labels for a target receipt and uploads
the serialized data to S3 for the evaluate_labels Lambda to process.
"""

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import boto3

# Use shared serialization utilities (included in Lambda zip)
from serialization import serialize_label, serialize_place, serialize_word

if TYPE_CHECKING:
    from evaluator_types import FetchReceiptDataOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "FetchReceiptDataOutput":
    """
    Fetch words and labels for a target receipt.

    Input:
    {
        "receipt": {"image_id": "img1", "receipt_id": 1, "merchant_name": "..."},
        "execution_id": "abc123",
        "batch_bucket": "bucket-name"
    }

    Output:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "image_id": "img1",
        "receipt_id": 1,
        "word_count": 45,
        "label_count": 38,
        "merchant_name": "Sprouts"
    }
    """
    receipt = event.get("receipt", {})
    image_id = receipt.get("image_id")
    receipt_id = receipt.get("receipt_id")
    merchant_name = receipt.get("merchant_name")
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    if not image_id or receipt_id is None:
        raise ValueError("receipt.image_id and receipt.receipt_id are required")

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(f"Fetching data for receipt {image_id}#{receipt_id}")

    # Import DynamoDB client and serialization
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Fetch words
    words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
    if isinstance(words, tuple):
        words = words[0]

    # Fetch labels
    labels, _ = dynamo.list_receipt_word_labels_for_receipt(image_id, receipt_id)

    # Fetch place data (replaces metadata)
    try:
        place = dynamo.get_receipt_place(image_id, receipt_id)
    except Exception as e:
        logger.warning(f"Could not fetch place data: {e}")
        place = None

    logger.info(
        f"Fetched {len(words)} words, {len(labels)} labels "
        f"for {image_id}#{receipt_id}"
    )

    # Create data payload
    data = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "words": [serialize_word(w) for w in words],
        "labels": [serialize_label(label) for label in labels],
        "place": serialize_place(place),
    }

    # Upload to S3
    data_key = f"data/{execution_id}/{image_id}_{receipt_id}.json"
    s3.put_object(
        Bucket=batch_bucket,
        Key=data_key,
        Body=json.dumps(data).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(f"Uploaded receipt data to s3://{batch_bucket}/{data_key}")

    return {
        "data_s3_key": data_key,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "word_count": len(words),
        "label_count": len(labels),
        "merchant_name": merchant_name,
    }
