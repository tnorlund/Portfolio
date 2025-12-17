"""Handler for listing pending embedding batches.

Pure business logic - no Lambda-specific code.
"""

import os
from typing import Any, Dict, List

import utils.logging

from receipt_dynamo.constants import BatchType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import BatchSummary

get_logger = utils.logging.get_logger

logger = get_logger(__name__)


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


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """List pending embedding batches from DynamoDB.

    Args:
        event: Lambda event containing:
            - batch_type: "line" or "word" (default: "line")
            - execution_id: Step Function execution ID (optional)
        context: Lambda context (unused)

    Returns:
        Dictionary containing:
            - total_batches: Number of pending batches
            - batch_indices: Array of batch indices for Map state
            - pending_batches: Array of batch objects (if small) or None
            - manifest_s3_key: S3 key for batch manifest (if use_s3=True)
            - manifest_s3_bucket: S3 bucket for batch manifest (if use_s3=True)
            - use_s3: Whether batches are stored in S3
            - execution_id: Step Function execution ID

    Raises:
        RuntimeError: If there's an error accessing DynamoDB
    """
    batch_type = event.get("batch_type", "line")
    execution_id = event.get("execution_id", "unknown")

    logger.info(
        "Starting list_pending_batches handler",
        batch_type=batch_type,
        execution_id=execution_id,
    )

    try:
        # Initialize DynamoDB client
        dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))

        # Get pending batches from DynamoDB
        if batch_type == "word":
            pending_batches = _list_pending_batches(
                dynamo_client, BatchType.WORD_EMBEDDING
            )
            logger.info(
                "Found pending word embedding batches",
                count=len(pending_batches),
            )
        else:
            pending_batches = _list_pending_batches(
                dynamo_client, BatchType.LINE_EMBEDDING
            )
            logger.info(
                "Found pending line embedding batches",
                count=len(pending_batches),
            )

        # Format response for Step Function
        # Create batch_indices array for Map state
        batch_indices = list(range(len(pending_batches)))

        # Check if we need to use S3 (if batches array is large)
        # Step Functions has a 256KB limit, so we'll use S3 if batches > 100KB
        import json

        batches_payload = json.dumps([batch.__dict__ for batch in pending_batches])
        batches_size = len(batches_payload.encode("utf-8"))
        use_s3 = batches_size > 100 * 1024  # 100KB threshold

        response = {
            "total_batches": len(pending_batches),
            "batch_indices": batch_indices,
            "execution_id": execution_id,
        }

        if use_s3:
            # Upload batches to S3 manifest
            import tempfile

            import boto3

            bucket = os.environ.get("CHROMADB_BUCKET")
            if not bucket:
                raise ValueError("CHROMADB_BUCKET environment variable not set")

            manifest_s3_key = f"manifests/{execution_id}/batches.json"

            # Serialize batches to JSON
            batches_data = [
                {
                    "batch_id": batch.batch_id,
                    "openai_batch_id": batch.openai_batch_id,
                    "batch_type": batch.batch_type,
                    "status": batch.status,
                    "submitted_at": (
                        batch.submitted_at.isoformat()
                        if hasattr(batch.submitted_at, "isoformat")
                        else str(batch.submitted_at)
                    ),
                }
                for batch in pending_batches
            ]

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tmp_file:
                json.dump(batches_data, tmp_file, indent=2)
                tmp_file_path = tmp_file.name

            try:
                s3_client = boto3.client("s3")
                s3_client.upload_file(tmp_file_path, bucket, manifest_s3_key)
                logger.info(
                    "Uploaded batches manifest to S3",
                    s3_key=manifest_s3_key,
                    bucket=bucket,
                )
            finally:
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

            response.update(
                {
                    "pending_batches": None,  # Not included when using S3
                    "manifest_s3_key": manifest_s3_key,
                    "manifest_s3_bucket": bucket,
                    "use_s3": True,
                }
            )
        else:
            # Include batches inline
            response.update(
                {
                    "pending_batches": [
                        {
                            "batch_id": batch.batch_id,
                            "openai_batch_id": batch.openai_batch_id,
                            "batch_type": batch.batch_type,
                            "status": batch.status,
                            "submitted_at": (
                                batch.submitted_at.isoformat()
                                if hasattr(batch.submitted_at, "isoformat")
                                else str(batch.submitted_at)
                            ),
                        }
                        for batch in pending_batches
                    ],
                    "manifest_s3_key": None,
                    "manifest_s3_bucket": None,
                    "use_s3": False,
                }
            )

        return response

    except AttributeError as e:
        logger.error("Client configuration error", error=str(e))
        raise RuntimeError(f"Configuration error: {str(e)}") from e

    except KeyError as e:
        logger.error(
            "Missing expected field in DynamoDB response", error=str(e)
        )
        raise RuntimeError(f"Data format error: {str(e)}") from e

    except Exception as e:
        logger.error(
            "Unexpected error listing pending batches", error=str(e)
        )
        raise RuntimeError(f"Internal error: {str(e)}") from e
