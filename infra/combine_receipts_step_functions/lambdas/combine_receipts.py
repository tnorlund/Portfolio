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

import logging
import os
from typing import Any, Dict, Optional, cast

# Import the shared combination logic
from combine_receipts_logic import combine_receipts

from receipt_agent.utils.combination_selector import ReceiptCombinationSelector
from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REQUIRED_ENV = [
    "DYNAMODB_TABLE_NAME",
    "CHROMADB_BUCKET",
    "RAW_BUCKET",
    "SITE_BUCKET",
    "BATCH_BUCKET",
]

_env = {key: os.environ.get(key) for key in REQUIRED_ENV}
missing = [k for k, v in _env.items() if not v]
if missing:
    raise ValueError(f"Missing required env vars: {', '.join(missing)}")

TABLE_NAME = cast(str, _env["DYNAMODB_TABLE_NAME"])
CHROMADB_BUCKET = cast(str, _env["CHROMADB_BUCKET"])
RAW_BUCKET = cast(str, _env["RAW_BUCKET"])
SITE_BUCKET = cast(str, _env["SITE_BUCKET"])
ARTIFACTS_BUCKET: Optional[str] = os.environ.get("ARTIFACTS_BUCKET")  # Optional for NDJSON export
BATCH_BUCKET_ENV = cast(str, _env["BATCH_BUCKET"])


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Combine receipts for a single image.

    Input:
    {
        "image_id": "image-uuid",
        "receipt_ids": [1, 2],
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true,
        "llm_select": true  // Optional: if false, use all receipt_ids directly
    }

    Output:
    {
        "image_id": "image-uuid",
        "new_receipt_id": 4,
        "original_receipt_ids": [1, 2],
        "status": "success",
        "compaction_run_id": "run-uuid",  // If embeddings were created
        "deleted_receipts": [1, 2]  // If dry_run=false and compaction succeeded
    }
    """
    image_id = event["image_id"]
    receipt_ids = event.get("receipt_ids") or []
    execution_id = event.get("execution_id", "unknown")
    dry_run = event.get("dry_run", True)
    batch_bucket = event.get("batch_bucket") or BATCH_BUCKET_ENV
    llm_select = event.get("llm_select", True)

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
        client = DynamoClient(table_name=TABLE_NAME)

        # If there aren't at least two receipts, we cannot combine.
        if len(receipt_ids) < 2:
            return {
                "image_id": image_id,
                "original_receipt_ids": receipt_ids,
                "status": "no_combination",
                "raw_answer": "Insufficient receipts to combine (need >=2).",
                "candidates": [],
            }

        # Determine which receipts to combine
        if llm_select:
            # Have the LLM pick which receipts to combine.
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
        else:
            # Use all provided receipt_ids directly (deterministic mode)
            chosen_receipts = receipt_ids
            selection = {
                "choice": chosen_receipts,
                "raw_answer": "Deterministic combination without LLM selection",
                "candidates": receipt_ids,
            }

            # Fail if LLM selected more than 2 receipts (should only be pairs)
            if len(chosen_receipts) > 2:
                return {
                    "image_id": image_id,
                    "original_receipt_ids": receipt_ids,
                    "status": "failed",
                    "error": f"LLM selector returned {len(chosen_receipts)} receipt IDs (expected 2): {chosen_receipts}. Rejecting to prevent unexpected deletions.",
                }

        # Use the shared combination logic
        # Note: Deletion of original receipts happens automatically when dry_run=False
        result = combine_receipts(
            client=client,
            image_id=image_id,
            receipt_ids=chosen_receipts,
            raw_bucket=RAW_BUCKET,
            site_bucket=SITE_BUCKET,
            chromadb_bucket=CHROMADB_BUCKET,
            artifacts_bucket=ARTIFACTS_BUCKET,
            batch_bucket=batch_bucket,
            execution_id=execution_id,
            dry_run=dry_run,
        )

        # Format response for Step Function
        response = {
            "image_id": image_id,
            "new_receipt_id": result.get("new_receipt_id"),
            "original_receipt_ids": chosen_receipts,
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

    except Exception as e:  # pylint: disable=broad-except
        logger.error("Error combining receipts: %s", e, exc_info=True)
        return {
            "image_id": image_id,
            "receipt_ids": receipt_ids,
            "status": "failed",
            "error": str(e),
        }
