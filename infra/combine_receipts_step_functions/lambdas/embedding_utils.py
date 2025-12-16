"""
Embedding utilities for receipt combination.

This module provides backward-compatible wrappers around the receipt_chroma
embedding orchestration functions for the combine_receipts workflow.

For new code, prefer using receipt_chroma.create_embeddings_and_compaction_run
directly to get access to EmbeddingResult with local ChromaClients for
immediate querying.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

from receipt_chroma.s3.helpers import upload_delta_tarball
from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
)

logger = logging.getLogger(__name__)


class _NoOpDynamoClient:
    """No-op client for combine_receipts workflow.

    The combine_receipts workflow persists the CompactionRun separately
    after the function returns, so we use a no-op client here.
    """

    def add_compaction_run(self, run: CompactionRun) -> None:
        """No-op - don't persist."""
        pass


def _upload_bundled_delta_to_s3(
    local_delta_dir: str,
    bucket: str,
    delta_prefix: str,
    collection_name: str,
    database_name: str,
    sqs_queue_url: Optional[str],
    batch_id: str,
    vector_count: int,
) -> Dict[str, Any]:
    """
    Upload delta directory as tarball to S3, optionally notify SQS.

    Uses receipt_chroma.s3.helpers.upload_delta_tarball() for standardized
    tarball creation and upload.

    Note: This function is kept for backward compatibility with existing tests.
    The receipt_chroma.create_embeddings_and_compaction_run handles this
    internally.
    """
    logger.info(
        "Bundling and uploading delta tarball: dir=%s bucket=%s prefix=%s",
        local_delta_dir,
        bucket,
        delta_prefix,
    )

    # Use standardized upload function
    upload_result = upload_delta_tarball(
        local_delta_dir=local_delta_dir,
        bucket=bucket,
        delta_prefix=delta_prefix,
        metadata={"delta_key": delta_prefix},  # Match old metadata format
        region=None,  # Use default boto3 region
        s3_client=None,  # Create new S3 client
    )

    # Check upload status
    if upload_result.get("status") != "uploaded":
        error_msg = upload_result.get("error", "Unknown error")
        logger.error("Failed to upload delta tarball: %s", error_msg)
        return {
            "status": "failed",
            "error": error_msg,
            "delta_key": delta_prefix,
        }

    s3_key = upload_result["object_key"]

    # Publish SQS message if queue URL provided (maintain existing behavior)
    if sqs_queue_url:
        try:
            sqs = boto3.client("sqs")
            message_body = {
                "delta_key": delta_prefix,
                "collection": collection_name,
                "database": database_name,
                "vector_count": vector_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "batch_id": batch_id,
            }
            message_group_id = f"{collection_name}:{batch_id or 'default'}"
            message_dedup_id = f"{collection_name}:{batch_id}:{s3_key}"

            sqs.send_message(
                QueueUrl=sqs_queue_url,
                MessageBody=json.dumps(message_body),
                MessageGroupId=message_group_id,
                MessageDeduplicationId=message_dedup_id,
                MessageAttributes={
                    "collection": {
                        "StringValue": collection_name,
                        "DataType": "String",
                    },
                    "batch_id": {
                        "StringValue": batch_id or "none",
                        "DataType": "String",
                    },
                },
            )
            logger.info("Published SQS message for delta: %s", delta_prefix)
        except Exception as e:
            logger.error("Failed to publish SQS message: %s", e, exc_info=True)
            # Don't fail the whole operation if SQS publish fails

    return {
        "status": "uploaded",
        "delta_key": delta_prefix,
        "s3_key": s3_key,
        "tar_size_bytes": upload_result.get("tar_size_bytes", 0),
    }


def create_embeddings_and_compaction_run(
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    receipt_metadata: Optional[ReceiptMetadata],
    image_id: str,
    new_receipt_id: int,
    chromadb_bucket: str,
) -> Optional[CompactionRun]:
    """
    Create embeddings and ChromaDB deltas for combined receipt.

    This is a backward-compatible wrapper that delegates to the canonical
    receipt_chroma.create_embeddings_and_compaction_run implementation.

    Note: This function does NOT persist the CompactionRun to DynamoDB.
    The caller is responsible for persisting it via client.add_compaction_run().

    Args:
        receipt_lines: Lines to embed
        receipt_words: Words to embed
        receipt_metadata: Optional metadata for merchant context
        image_id: Image identifier
        new_receipt_id: Receipt identifier (maps to receipt_id in receipt_chroma)
        chromadb_bucket: S3 bucket for ChromaDB deltas

    Returns:
        CompactionRun entity if embeddings were created, None otherwise.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set; skipping embeddings")
        return None

    if not receipt_lines or not receipt_words:
        logger.info("No lines/words provided; skipping embeddings")
        return None

    # Import and call receipt_chroma implementation
    from receipt_chroma.embedding.orchestration import (
        create_embeddings_and_compaction_run as chroma_create_embeddings,
    )

    try:
        # Use no-op DynamoDB client - caller persists CompactionRun separately
        result = chroma_create_embeddings(
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            image_id=image_id,
            receipt_id=new_receipt_id,  # Map new_receipt_id -> receipt_id
            chromadb_bucket=chromadb_bucket,
            dynamo_client=_NoOpDynamoClient(),
            receipt_metadata=receipt_metadata,
            receipt_word_labels=None,  # combine_receipts doesn't use labels
            merchant_name=None,  # Let receipt_chroma extract from metadata
        )

        # Extract CompactionRun before closing
        compaction_run = result.compaction_run

        # Close the EmbeddingResult to release resources
        # (combine_receipts doesn't need the local ChromaClients)
        result.close()

        logger.info(
            "Created CompactionRun %s for receipt %s",
            compaction_run.run_id,
            new_receipt_id,
        )
        return compaction_run

    except (ValueError, RuntimeError) as e:
        logger.error("Failed to create embeddings: %s", e)
        return None
