"""Look up rendering data for receipts that actually have native traces."""

import heapq
import json
import logging
import os
from typing import Any
from uuid import uuid4

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

logger = logging.getLogger(__name__)
MAX_RECEIPTS = 500
MAX_NATIVE_RECEIPTS = 50  # Matches the cache job's sample size.


def native_receipt_keys(s3: Any, bucket: str) -> list[tuple[str, int]]:
    """Select recent traced receipts, not an unrelated DynamoDB scan sample."""
    pages = s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix="native-traces/"
    )
    recent = heapq.nlargest(
        MAX_RECEIPTS,
        (
            obj
            for page in pages
            for obj in page.get("Contents", [])
            if obj["Key"].endswith(".ndjson")
        ),
        key=lambda obj: (obj["LastModified"], obj["Key"]),
    )
    selected: dict[tuple[str, int], None] = {}
    for obj in recent:
        body = (
            s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"]
            .read()
            .decode()
        )
        for line in body.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("name")
                not in {"receipt_processing", "async_label_validation"}
                or row.get("status") == "error"
                or row.get("capture_status") == "error"
            ):
                continue
            metadata = json.loads(row.get("extra") or "{}").get("metadata", {})
            if (
                metadata.get("image_id")
                and metadata.get("receipt_id") is not None
            ):
                selected[
                    (metadata["image_id"], int(metadata["receipt_id"]))
                ] = None
        if len(selected) >= MAX_NATIVE_RECEIPTS:
            break
    if not selected:
        raise ValueError("No completed native receipt roots are available")
    return list(selected)[:MAX_NATIVE_RECEIPTS]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Write an execution-specific receipt lookup for the Spark cache job."""
    client = DynamoClient(os.environ["DYNAMODB_TABLE"])
    cache_bucket = os.environ["CACHE_BUCKET"]
    trace_bucket = os.environ.get("NATIVE_TRACE_BUCKET")
    s3 = boto3.client("s3")
    if trace_bucket:
        receipts = []
        for image_id, receipt_id in native_receipt_keys(s3, trace_bucket):
            try:
                receipts.append(client.get_receipt(image_id, receipt_id))
            except EntityNotFoundError:
                logger.info(
                    "Traced receipt was deleted: %s/%s", image_id, receipt_id
                )
    else:
        # Retain the legacy standalone lookup path for archived Parquet jobs.
        receipts, last_key = client.list_receipts(limit=MAX_RECEIPTS)
        while last_key and len(receipts) < MAX_RECEIPTS:
            page, last_key = client.list_receipts(
                limit=MAX_RECEIPTS - len(receipts),
                last_evaluated_key=last_key,
            )
            receipts.extend(page)

    lookup = {}
    for receipt in receipts:
        words = client.list_receipt_words_from_receipt(
            receipt.image_id, receipt.receipt_id
        )
        labels, _ = client.list_receipt_word_labels_for_receipt(
            receipt.image_id, receipt.receipt_id
        )
        lookup[f"{receipt.image_id}_{receipt.receipt_id}"] = {
            "cdn_s3_key": receipt.cdn_s3_key or "",
            "cdn_webp_s3_key": receipt.cdn_webp_s3_key,
            "cdn_avif_s3_key": receipt.cdn_avif_s3_key,
            "cdn_medium_s3_key": receipt.cdn_medium_s3_key,
            "cdn_medium_webp_s3_key": receipt.cdn_medium_webp_s3_key,
            "cdn_medium_avif_s3_key": receipt.cdn_medium_avif_s3_key,
            "width": receipt.width or 0,
            "height": receipt.height or 0,
            "words": [
                {
                    "line_id": w.line_id,
                    "word_id": w.word_id,
                    "text": w.text,
                    "bbox": w.bounding_box,
                }
                for w in words
            ],
            "labels": {
                f"{label.line_id}_{label.word_id}": label.label
                for label in labels
            },
        }
    if not lookup:
        raise ValueError("No traced receipts remain available for the cache")
    request_id = getattr(context, "aws_request_id", None) or str(uuid4())
    key = f"receipt-lookups/{request_id}.json"
    s3.put_object(
        Bucket=cache_bucket,
        Key=key,
        Body=json.dumps(lookup).encode(),
        ContentType="application/json",
    )
    return {
        "receipt_count": len(lookup),
        "receipts_s3_path": f"s3://{cache_bucket}/{key}",
    }
