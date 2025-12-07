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
from typing import Any, Dict, cast

# Add repo root to path
repo_root = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
)
sys.path.insert(0, repo_root)

# Import the shared combination logic
from combine_receipts_logic import combine_receipts

from receipt_agent.agent import ReceiptCombinationSelector
from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cache environment values at cold start (Lambda best practice)
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
CHROMADB_BUCKET = os.environ.get("CHROMADB_BUCKET")
RAW_BUCKET = os.environ.get("RAW_BUCKET")
SITE_BUCKET = os.environ.get("SITE_BUCKET")
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET")
EMBED_NDJSON_QUEUE_URL = os.environ.get("EMBED_NDJSON_QUEUE_URL")
BATCH_BUCKET_ENV = os.environ.get("BATCH_BUCKET")

if not TABLE_NAME:
    raise ValueError("DYNAMODB_TABLE_NAME is not set")
if not CHROMADB_BUCKET:
    raise ValueError("CHROMADB_BUCKET is not set")
if not RAW_BUCKET:
    raise ValueError("RAW_BUCKET is not set")
if not SITE_BUCKET:
    raise ValueError("SITE_BUCKET is not set")
if not ARTIFACTS_BUCKET:
    raise ValueError("ARTIFACTS_BUCKET is not set")
if not EMBED_NDJSON_QUEUE_URL:
    raise ValueError("EMBED_NDJSON_QUEUE_URL is not set")
if not BATCH_BUCKET_ENV:
    raise ValueError("BATCH_BUCKET_ENV is not set")

# Narrow env vars for type checkers
TABLE_NAME_STR = cast(str, TABLE_NAME)
CHROMADB_BUCKET_STR = cast(str, CHROMADB_BUCKET)
RAW_BUCKET_STR = cast(str, RAW_BUCKET)
SITE_BUCKET_STR = cast(str, SITE_BUCKET)
ARTIFACTS_BUCKET_STR = cast(str, ARTIFACTS_BUCKET)
EMBED_NDJSON_QUEUE_URL_STR = cast(str, EMBED_NDJSON_QUEUE_URL)
BATCH_BUCKET_ENV_STR = cast(str, BATCH_BUCKET_ENV)


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
    receipt_ids = event.get("receipt_ids") or []
    execution_id = event.get("execution_id", "unknown")
    dry_run = event.get("dry_run", True)
    # Always use LLM selection for choosing receipt pairs
    llm_select = True
    batch_bucket = event.get("batch_bucket") or BATCH_BUCKET_ENV_STR

    # Set execution_id and batch_bucket in environment for records JSON saving
    os.environ["EXECUTION_ID"] = execution_id
    if batch_bucket:
        os.environ["BATCH_BUCKET"] = batch_bucket

    logger.info(
        "Combining receipts for image %s, receipt_ids=%s, dry_run=%s",
        image_id,
        receipt_ids,
        dry_run,
    )

    try:
        client = DynamoClient(table_name=TABLE_NAME_STR)

        # If there aren't at least two receipts, we cannot combine.
        if len(receipt_ids) < 2:
            return {
                "image_id": image_id,
                "original_receipt_ids": receipt_ids,
                "status": "no_combination",
                "raw_answer": "Insufficient receipts to combine (need >=2).",
                "candidates": [],
            }

        # Optionally have the LLM pick which receipts to combine.
        chosen_receipts = receipt_ids
        if llm_select:
            if not receipt_ids:
                return {
                    "image_id": image_id,
                    "receipt_ids": receipt_ids,
                    "status": "failed",
                    "error": "llm_select enabled but no receipt_ids provided",
                }
            selector = ReceiptCombinationSelector(client)
            target_id = receipt_ids[0]
            selection = selector.choose(
                image_id=image_id, target_receipt_id=target_id
            )
            chosen_receipts = selection.get("choice") or []
            if not chosen_receipts:
                return {
                    "image_id": image_id,
                    "original_receipt_ids": receipt_ids,
                    "status": "no_combination",
                    "raw_answer": selection.get("raw_answer"),
                    "candidates": selection.get("candidates"),
                }

        # Use the shared combination logic
        result = combine_receipts(
            client=client,
            image_id=image_id,
            receipt_ids=chosen_receipts,
            raw_bucket=RAW_BUCKET_STR,
            site_bucket=SITE_BUCKET_STR,
            chromadb_bucket=CHROMADB_BUCKET_STR,
            artifacts_bucket=ARTIFACTS_BUCKET_STR,
            embed_ndjson_queue_url=EMBED_NDJSON_QUEUE_URL_STR,
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
        logger.error("Error combining receipts: %s", e, exc_info=True)
        return {
            "image_id": image_id,
            "receipt_ids": receipt_ids,
            "status": "failed",
            "error": str(e),
        }
