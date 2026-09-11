"""Build receipt visualization caches from S3 and DynamoDB, without Spark."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import boto3
from receipt_cache import (
    MAX_RECEIPTS,
    aggregate_stats,
    build_receipt,
    group_spans,
    read_traces,
    receipt_roots,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Publish the new index only after every receipt object has been written."""
    client = DynamoClient(os.environ["DYNAMODB_TABLE"])
    cache_bucket = os.environ["CACHE_BUCKET"]
    s3 = boto3.client("s3")
    rows = read_traces(s3, os.environ["NATIVE_TRACE_BUCKET"])
    grouped = group_spans(rows)
    receipts = []
    for root in receipt_roots(rows):
        image_id, receipt_id = root["image_id"], root["receipt_id"]
        try:
            receipt = client.get_receipt(image_id, receipt_id)
        except EntityNotFoundError:
            logger.info(
                "Traced receipt was deleted: %s/%s", image_id, receipt_id
            )
            continue
        words = client.list_receipt_words_from_receipt(image_id, receipt_id)
        labels, last_key = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )
        while last_key:
            page, last_key = client.list_receipt_word_labels_for_receipt(
                image_id,
                receipt_id,
                last_evaluated_key=last_key,
            )
            labels.extend(page)
        payload = build_receipt(
            root, grouped[root["trace_id"]], receipt, words, labels
        )
        if payload:
            receipts.append(payload)
        if len(receipts) >= MAX_RECEIPTS:
            break
    if not receipts:
        raise ValueError("No native receipt validation results are available")

    version = str(uuid4())
    prefix = f"cache-runs/{version}/receipts/"
    keys = []
    for receipt in receipts:
        key = f"{prefix}receipt-{receipt['image_id']}-{receipt['receipt_id']}.json"
        s3.put_object(
            Bucket=cache_bucket,
            Key=key,
            Body=json.dumps(receipt).encode(),
            ContentType="application/json",
        )
        keys.append(key)
    metadata = {
        "version": version,
        "receipt_keys": keys,
        "aggregate_stats": aggregate_stats(receipts),
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "trace_source": "native-s3",
        "schema_version": 1,
    }
    s3.put_object(
        Bucket=cache_bucket,
        Key="metadata.json",
        Body=json.dumps(metadata).encode(),
        ContentType="application/json",
    )
    return {
        "receipt_count": len(receipts),
        "version": version,
        "trace_source": "native-s3",
    }
