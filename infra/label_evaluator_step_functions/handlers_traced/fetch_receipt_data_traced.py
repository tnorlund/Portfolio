"""Fetch receipt data for the traced Step Function.

This is a zip-based Lambda that doesn't have access to langsmith.
Tracing is handled by the container-based Lambdas.
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
from typing import Any

import boto3

# Import serialization from lambdas
import sys
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas"
))
from utils.serialization import serialize_label, serialize_place, serialize_word

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Fetch receipt data from DynamoDB and save to S3.

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
        "word_count": 50,
        "label_count": 12
    }
    """
    receipt = event.get("receipt", {})
    image_id = receipt.get("image_id")
    receipt_id = receipt.get("receipt_id")
    merchant_name = receipt.get("merchant_name", "Unknown")
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    if not image_id or receipt_id is None:
        raise ValueError("receipt.image_id and receipt.receipt_id are required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        "Fetching data for receipt %s#%s",
        image_id,
        receipt_id,
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Fetch receipt data
    try:
        place = dynamo.get_receipt_place(image_id, receipt_id)
    except Exception:
        logger.warning("No ReceiptPlace found for %s#%s", image_id, receipt_id)
        place = None

    words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
    labels, _ = dynamo.list_receipt_word_labels_for_receipt(image_id, receipt_id)

    logger.info(
        "Fetched %s words, %s labels for %s#%s",
        len(words),
        len(labels),
        image_id,
        receipt_id,
    )

    # Serialize and save to S3
    data = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "place": serialize_place(place) if place else None,
        "words": [serialize_word(w) for w in words],
        "labels": [serialize_label(label) for label in labels],
    }

    data_s3_key = f"data/{execution_id}/{image_id}_{receipt_id}.json"

    s3.put_object(
        Bucket=batch_bucket,
        Key=data_s3_key,
        Body=json.dumps(data).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        "Saved receipt data to s3://%s/%s",
        batch_bucket,
        data_s3_key,
    )

    return {
        "data_s3_key": data_s3_key,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "word_count": len(words),
        "label_count": len(labels),
    }
