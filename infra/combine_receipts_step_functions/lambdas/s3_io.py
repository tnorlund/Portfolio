"""
S3 I/O utilities for receipt combination.

This module contains functions for saving receipt records to S3 and exporting
NDJSON files for embedding processing.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import boto3

from receipt_dynamo import DynamoClient

logger = logging.getLogger()


def save_records_json_to_s3(
    records_json: Dict[str, Any],
    image_id: str,
    new_receipt_id: int,
    batch_bucket: Optional[str],
    chromadb_bucket: str,
    execution_id: Optional[str],
) -> Dict[str, Optional[str]]:
    """
    Save receipt records as JSON to S3 for validation.

    This allows validation without committing to DynamoDB.

    Args:
        records_json: Dictionary containing receipt records to save
        image_id: Image ID containing the receipts
        new_receipt_id: ID of the new combined receipt
        batch_bucket: Optional batch bucket name
        chromadb_bucket: ChromaDB bucket name (used as fallback)
        execution_id: Optional execution ID

    Returns:
        Dict with "records_s3_key" and "records_s3_bucket" (may be None if save failed)
    """
    records_key = None
    records_bucket = None
    try:
        s3_client = boto3.client("s3")
        # Get batch bucket from parameter, environment (set by step function),
        # or chromadb_bucket as fallback
        save_bucket = (
            batch_bucket or os.environ.get("BATCH_BUCKET") or chromadb_bucket
        )
        save_execution_id = execution_id or os.environ.get(
            "EXECUTION_ID", "unknown"
        )

        logger.info(
            "Saving records JSON to S3: bucket=%s, execution_id=%s",
            save_bucket,
            save_execution_id,
        )

        records_key = (
            f"receipts/{save_execution_id}/"
            f"{image_id}_receipt_{new_receipt_id:05d}_records.json"
        )
        s3_client.put_object(
            Bucket=save_bucket,
            Key=records_key,
            Body=json.dumps(records_json, default=str, indent=2),
            ContentType="application/json",
        )
        records_bucket = save_bucket
        logger.info(
            "Successfully saved records JSON to s3://%s/%s",
            save_bucket,
            records_key,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Failed to save records JSON to S3: %s", e, exc_info=True)

    return {
        "records_s3_key": records_key,
        "records_s3_bucket": records_bucket,
    }


def export_receipt_ndjson_and_queue(
    client: DynamoClient,
    artifacts_bucket: str,
    embed_ndjson_queue_url: Optional[str],
    image_id: str,
    receipt_id: int,
) -> None:
    """
    Export receipt lines and words to NDJSON files and queue for stream processor.

    This matches the upload workflow pattern from process_ocr_results.py.
    If embed_ndjson_queue_url is None or empty, NDJSON files are still uploaded
    but not queued.

    Args:
        client: DynamoDB client
        artifacts_bucket: S3 bucket for artifacts
        embed_ndjson_queue_url: Optional SQS queue URL for embedding queue
        image_id: Image ID containing the receipt
        receipt_id: Receipt ID to export
    """
    if not embed_ndjson_queue_url:
        logger.info("EMBED_NDJSON_QUEUE_URL not set; skipping embedding queue")
        return

    # Fetch authoritative words/lines from DynamoDB (just saved)
    receipt_words = client.list_receipt_words_from_receipt(
        image_id, receipt_id
    )
    receipt_lines = client.list_receipt_lines_from_receipt(
        image_id, receipt_id
    )

    prefix = f"receipts/{image_id}/receipt-{receipt_id:05d}/"
    lines_key = prefix + "lines.ndjson"
    words_key = prefix + "words.ndjson"

    # Serialize full dataclass objects so the consumer can rehydrate with
    # ReceiptLine(**d)/ReceiptWord(**d) preserving geometry and methods
    line_rows = [dict(line) for line in (receipt_lines or [])]
    word_rows = [dict(word) for word in (receipt_words or [])]

    # Upload NDJSON files to S3
    s3_client = boto3.client("s3")

    # Upload lines NDJSON
    lines_ndjson_content = "\n".join(
        json.dumps(row, default=str) for row in line_rows
    )
    s3_client.put_object(
        Bucket=artifacts_bucket,
        Key=lines_key,
        Body=lines_ndjson_content.encode("utf-8"),
        ContentType="application/x-ndjson",
    )

    # Upload words NDJSON
    words_ndjson_content = "\n".join(
        json.dumps(row, default=str) for row in word_rows
    )
    s3_client.put_object(
        Bucket=artifacts_bucket,
        Key=words_key,
        Body=words_ndjson_content.encode("utf-8"),
        ContentType="application/x-ndjson",
    )

    # Enqueue for batched embedding from NDJSON via SQS
    sqs_client = boto3.client("sqs")
    payload = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "artifacts_bucket": artifacts_bucket,
        "lines_key": lines_key,
        "words_key": words_key,
    }
    sqs_client.send_message(
        QueueUrl=embed_ndjson_queue_url,
        MessageBody=json.dumps(payload),
    )



