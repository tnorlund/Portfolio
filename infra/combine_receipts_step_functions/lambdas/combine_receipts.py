"""
Combine Receipts Handler (Container Lambda)

Combines multiple receipts into a single new receipt.
This is the main processing Lambda that:
1. Fetches receipt data from DynamoDB
2. Combines words, lines, and letters
3. Creates new receipt image
4. Creates embeddings and ChromaDB deltas
5. Saves to DynamoDB (unless dry_run)
"""

import json
import logging
import os
import sys
from typing import Any, Dict

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, repo_root)

from receipt_dynamo import DynamoClient

# Import the shared combination logic
from combine_receipts_logic import combine_receipts

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Combine receipts for a single image.

    Input:
    {
        "image_id": "image-uuid",
        "receipt_ids": [1, 2],
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true
    }

    Output:
    {
        "image_id": "image-uuid",
        "new_receipt_id": 4,
        "original_receipt_ids": [1, 2],
        "status": "success",
        "compaction_run_id": "run-uuid"  // If embeddings were created
    }
    """
    image_id = event["image_id"]
    receipt_ids = event["receipt_ids"]
    execution_id = event.get("execution_id", "unknown")
    dry_run = event.get("dry_run", True)
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
    raw_bucket = os.environ.get("RAW_BUCKET")
    site_bucket = os.environ.get("SITE_BUCKET")
    artifacts_bucket = os.environ.get("ARTIFACTS_BUCKET")  # May be None, will fallback to site_bucket
    embed_ndjson_queue_url = os.environ.get("EMBED_NDJSON_QUEUE_URL")  # May be None/empty

    # Set execution_id and batch_bucket in environment for records JSON saving
    os.environ["EXECUTION_ID"] = execution_id
    if batch_bucket:
        os.environ["BATCH_BUCKET"] = batch_bucket

    logger.info(
        f"Combining receipts for image {image_id}, "
        f"receipt_ids={receipt_ids}, dry_run={dry_run}"
    )

    try:
        client = DynamoClient(table_name)

        # Use the shared combination logic
        result = combine_receipts(
            client=client,
            image_id=image_id,
            receipt_ids=receipt_ids,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            chromadb_bucket=chromadb_bucket,
            artifacts_bucket=artifacts_bucket,
            embed_ndjson_queue_url=embed_ndjson_queue_url,
            batch_bucket=batch_bucket,
            execution_id=execution_id,
            dry_run=dry_run,
        )

        # Format response for Step Function
        response = {
            "image_id": image_id,
            "new_receipt_id": result.get("new_receipt_id"),
            "original_receipt_ids": receipt_ids,
            "status": result.get("status", "success"),
        }

        if result.get("compaction_run"):
            response["compaction_run_id"] = result["compaction_run"].run_id

        if result.get("error"):
            response["error"] = result["error"]

        # Include S3 key for records JSON (for validation)
        if result.get("records_s3_key"):
            response["records_s3_key"] = result["records_s3_key"]
            response["records_s3_bucket"] = result.get("records_s3_bucket")

        return response

    except Exception as e:
        logger.error(f"Error combining receipts: {e}", exc_info=True)
        return {
            "image_id": image_id,
            "receipt_ids": receipt_ids,
            "status": "failed",
            "error": str(e),
        }

