"""Fetch training data (other merchant receipts) for pattern learning.

This handler fetches other receipts from the same merchant for pattern
learning and uploads the serialized data to S3. Uses caching to avoid
redundant fetches across receipts from the same merchant.
"""

import hashlib
import json
import logging
import os
from typing import Any

import boto3

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
    merchant_hash = hashlib.md5(merchant_name.encode()).hexdigest()[:12]
    training_key = f"training/{execution_id}/{merchant_hash}.json"

    # Check if training data already exists (cached from another receipt)
    try:
        s3.head_object(Bucket=batch_bucket, Key=training_key)
        logger.info(
            f"Training data already cached at s3://{batch_bucket}/{training_key}"
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
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] != "404":
            raise
        # Not cached, need to fetch

    logger.info(
        f"Fetching training data for merchant '{merchant_name}' "
        f"(max_receipts={max_receipts})"
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
        logger.info(f"No other receipts found for merchant '{merchant_name}'")
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
        f"Fetching words/labels for {len(other_places)} training receipts"
    )

    # Serialization helpers
    def serialize_word(w):
        return {
            "image_id": w.image_id,
            "receipt_id": w.receipt_id,
            "line_id": w.line_id,
            "word_id": w.word_id,
            "text": w.text,
            "bounding_box": w.bounding_box,
            "top_right": w.top_right,
            "top_left": w.top_left,
            "bottom_right": w.bottom_right,
            "bottom_left": w.bottom_left,
            "angle_degrees": w.angle_degrees,
            "angle_radians": w.angle_radians,
            "confidence": w.confidence,
            "extracted_data": w.extracted_data,
            "embedding_status": (
                str(w.embedding_status) if w.embedding_status else None
            ),
            "is_noise": w.is_noise,
        }

    def serialize_label(label):
        ts = label.timestamp_added
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        return {
            "image_id": label.image_id,
            "receipt_id": label.receipt_id,
            "line_id": label.line_id,
            "word_id": label.word_id,
            "label": label.label,
            "reasoning": label.reasoning,
            "timestamp_added": ts_str,
            "validation_status": label.validation_status,
            "label_proposed_by": label.label_proposed_by,
            "label_consolidated_from": label.label_consolidated_from,
        }

    def serialize_place(p):
        """Serialize ReceiptPlace to JSON-compatible dict."""
        return {
            "image_id": p.image_id,
            "receipt_id": p.receipt_id,
            "merchant_name": p.merchant_name,
            "place_id": p.place_id,
            "formatted_address": p.formatted_address,
            "validation_status": p.validation_status,
        }

    # Fetch and serialize each receipt
    receipts_data = []
    for place in other_places:
        try:
            words = dynamo.list_receipt_words_from_receipt(
                place.image_id, place.receipt_id
            )
            if isinstance(words, tuple):
                words = words[0]

            labels, _ = dynamo.list_receipt_word_labels_for_receipt(
                place.image_id, place.receipt_id
            )

            receipts_data.append(
                {
                    "place": serialize_place(place),
                    "words": [serialize_word(w) for w in words],
                    "labels": [serialize_label(label) for label in labels],
                }
            )
        except Exception as e:
            logger.warning(
                f"Error fetching receipt {place.image_id}#{place.receipt_id}: {e}"
            )
            continue

    logger.info(f"Successfully fetched {len(receipts_data)} training receipts")

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
        f"Uploaded training data to s3://{batch_bucket}/{training_key}"
    )

    return {
        "training_s3_key": training_key,
        "merchant_name": merchant_name,
        "receipt_count": len(receipts_data),
        "cached": False,
    }
