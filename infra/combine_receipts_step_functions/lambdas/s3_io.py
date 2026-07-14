"""
S3 I/O utilities for receipt combination.

This module contains functions for saving receipt records to S3.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import boto3

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
