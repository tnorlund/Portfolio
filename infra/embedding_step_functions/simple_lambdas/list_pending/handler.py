"""Simple Lambda handler for listing pending embedding batches.

This is a lightweight, zip-based Lambda function that only depends on
receipt_dynamo and boto3. No container overhead needed.

When batch count is large, creates an S3 manifest to avoid Step Functions
payload size limits.
"""

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Union

import boto3

from receipt_dynamo.constants import BatchType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import BatchSummary

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Step Functions payload limit is 256KB (262,144 bytes)
# We'll use S3 if the response would exceed 150KB to leave a large buffer
MAX_PAYLOAD_SIZE = 150 * 1024  # 150KB (conservative)

s3_client = boto3.client("s3")


def _list_pending_batches(
    dynamo_client: DynamoClient, batch_type: BatchType
) -> List[BatchSummary]:
    """
    List pending embedding batches from DynamoDB with pagination.

    Args:
        dynamo_client: DynamoDB client instance
        batch_type: Type of batches to list (LINE_EMBEDDING or WORD_EMBEDDING)

    Returns:
        List of pending batch summaries
    """
    summaries, lek = dynamo_client.get_batch_summaries_by_status(
        status="PENDING",
        batch_type=batch_type,
        limit=25,
        last_evaluated_key=None,
    )
    while lek:
        next_summaries, lek = dynamo_client.get_batch_summaries_by_status(
            status="PENDING",
            batch_type=batch_type,
            limit=25,
            last_evaluated_key=lek,
        )
        summaries.extend(next_summaries)
    return summaries


def lambda_handler(
    event: Dict[str, Any], context: Any
) -> Union[List[Dict[str, str]], Dict[str, Any]]:
    """List pending embedding batches from DynamoDB.

    Supports both line and word embedding batches based on the
    batch_type parameter in the event.

    When batch count is large, creates an S3 manifest to avoid Step Functions
    payload size limits.

    Args:
        event: Lambda event with:
            - optional 'batch_type' field ('line' or 'word')
            - optional 'execution_id' field (Step Functions execution name)
        context: Lambda context (used for execution_id if not in event)

    Returns:
        If small payload:
            List of pending batches with batch_id and openai_batch_id
        If large payload (S3 manifest):
            {
                "manifest_s3_key": "...",
                "manifest_s3_bucket": "...",
                "execution_id": "...",
                "total_batches": 100,
                "batch_indices": [0, 1, 2, ...],
                "use_s3": true,
                "pending_batches": null
            }
    """
    # Determine batch type from event (default to 'line' for backward
    # compatibility)
    batch_type = event.get("batch_type", "line")
    execution_id = event.get("execution_id")

    # Use execution_id from event, or generate UUID if not provided
    if not execution_id and context:
        execution_id = getattr(context, "aws_request_id", None)
    if not execution_id:
        execution_id = str(uuid.uuid4())

    logger.info(
        "Starting list_pending_batches handler for batch_type: %s, execution_id: %s",
        batch_type,
        execution_id,
    )

    try:
        # Initialize DynamoDB client
        dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))

        # Get pending batches from DynamoDB based on type
        if batch_type == "word":
            pending_batches = _list_pending_batches(
                dynamo_client, BatchType.WORD_EMBEDDING
            )
        else:
            pending_batches = _list_pending_batches(
                dynamo_client, BatchType.LINE_EMBEDDING
            )

        logger.info(
            "Found %d pending %s embedding batches",
            len(pending_batches),
            batch_type,
        )

        # Format response for Step Function
        batch_list = [
            {
                "batch_id": batch.batch_id,
                "openai_batch_id": batch.openai_batch_id,
            }
            for batch in pending_batches
        ]

        # Estimate payload size
        # Each batch entry is ~100 bytes (batch_id + openai_batch_id)
        # JSON overhead adds ~10% (conservative)
        estimated_size = len(json.dumps(batch_list).encode("utf-8"))

        logger.info(
            "Estimated payload size: %d bytes (limit: %d bytes)",
            estimated_size,
            MAX_PAYLOAD_SIZE,
        )

        # If payload is too large, create S3 manifest
        if estimated_size > MAX_PAYLOAD_SIZE or len(batch_list) > 50:
            logger.info(
                "Payload exceeds limit or batch count is large, creating S3 manifest: "
                "batch_count=%d, estimated_size=%d",
                len(batch_list),
                estimated_size,
            )

            # Get S3 bucket from environment (use S3_BUCKET like other lambdas)
            bucket = os.environ.get("S3_BUCKET")
            if not bucket:
                raise ValueError("S3_BUCKET environment variable not set")

            # Create manifest with batch info
            manifest_batches = []
            for index, batch_info in enumerate(batch_list):
                manifest_batches.append({
                    "index": index,
                    "batch_id": batch_info["batch_id"],
                    "openai_batch_id": batch_info["openai_batch_id"],
                })

            manifest = {
                "execution_id": execution_id,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "total_batches": len(manifest_batches),
                "batches": manifest_batches,
                "batch_type": batch_type,
            }

            # Upload manifest to S3
            manifest_key = f"poll_manifests/{execution_id}/manifest.json"

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
                json.dump(manifest, tmp_file, indent=2)
                tmp_file_path = tmp_file.name

            try:
                s3_client.upload_file(
                    tmp_file_path,
                    bucket,
                    manifest_key,
                )
                logger.info(
                    "Uploaded manifest to S3: s3_key=%s, bucket=%s, batch_count=%d",
                    manifest_key,
                    bucket,
                    len(manifest_batches),
                )
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

            # Create batch_indices array for Map state
            batch_indices = list(range(len(manifest_batches)))

            # Return S3 manifest info
            return {
                "manifest_s3_key": manifest_key,
                "manifest_s3_bucket": bucket,
                "execution_id": execution_id,
                "total_batches": len(manifest_batches),
                "batch_indices": batch_indices,
                "use_s3": True,
                "pending_batches": None,  # Explicitly null for JSONPath compatibility
            }
        else:
            # Small payload - return inline with consistent structure
            logger.info("Payload within limit, returning batches inline")
            # Return consistent structure even for inline mode
            return {
                "pending_batches": batch_list,
                "batch_indices": list(range(len(batch_list))),
                "use_s3": False,
                "manifest_s3_key": None,
                "manifest_s3_bucket": None,
                "execution_id": execution_id,
                "total_batches": len(batch_list),
            }

    except AttributeError as e:
        logger.error("Client configuration error: %s", str(e))
        raise RuntimeError(f"Configuration error: {str(e)}") from e

    except KeyError as e:
        logger.error("Missing expected field in DynamoDB response: %s", str(e))
        raise RuntimeError(f"Data format error: {str(e)}") from e

    except Exception as e:
        logger.error(
            "Unexpected error listing pending batches: %s", str(e)
        )
        raise RuntimeError(f"Internal error: {str(e)}") from e
