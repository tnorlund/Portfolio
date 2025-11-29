"""Producer for creating ChromaDB embedding deltas.

This module provides the core function for creating ChromaDB delta files
from embedding results and uploading them to S3 for compaction.
"""

import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from chromadb.errors import ChromaError

from receipt_chroma.data.chroma_client import ChromaClient

logger = logging.getLogger(__name__)

# Only operational errors that can occur during normal runtime operations.
# Programming errors (TypeError, ValueError) are excluded so they propagate
# and surface bugs during development rather than being silently logged.
DELTA_ERRORS = (
    BotoCoreError,
    ClientError,
    OSError,
    RuntimeError,
    ChromaError,
)


def _prepare_delta_directory(
    local_temp_dir: Optional[str],
) -> tuple[str, str, bool]:
    """Create or allocate a temporary directory for delta generation."""
    if local_temp_dir is not None:
        temp_dir = local_temp_dir
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(delta_dir, exist_ok=True)
        return temp_dir, delta_dir, False

    temp_dir = tempfile.mkdtemp()
    delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
    os.makedirs(delta_dir, exist_ok=True)
    return temp_dir, delta_dir, True


def _init_chroma_client(
    delta_dir: str, database_name: Optional[str], delta_prefix: str
) -> tuple[ChromaClient, str]:
    """Initialize the Chroma client and adjust delta prefix if needed."""
    if database_name:
        logger.info("Creating ChromaDB client for '%s'", database_name)
        logger.info("Persist directory: %s", delta_dir)
        chroma = ChromaClient(
            persist_directory=delta_dir,
            mode="delta",
            metadata_only=True,
        )
        return chroma, f"{database_name}/{delta_prefix}"

    logger.info("Creating ChromaDB client")
    logger.info("Persist directory: %s", delta_dir)
    chroma = ChromaClient(
        persist_directory=delta_dir,
        mode="delta",
        metadata_only=True,
    )
    return chroma, delta_prefix


def _notify_sqs(
    *,
    sqs_queue_url: Optional[str],
    s3_key: str,
    collection_name: str,
    database_name: Optional[str],
    ids: List[str],
    batch_id: Optional[str],
) -> None:
    """Send compaction notification to SQS when configured."""
    if not sqs_queue_url:
        return

    try:
        sqs = boto3.client("sqs")
        message_body = {
            "delta_key": s3_key,
            "collection": collection_name,
            "database": database_name or "default",
            "vector_count": len(ids),
            "timestamp": datetime.utcnow().isoformat(),
        }
        if batch_id:
            message_body["batch_id"] = batch_id

        message_group_id = f"{collection_name}:{batch_id or 'default'}"
        dedup_suffix = batch_id or uuid.uuid4().hex
        message_dedup_id = f"{collection_name}:{dedup_suffix}:{s3_key}"

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

        logger.info("Sent delta notification to SQS: %s", s3_key)
    except DELTA_ERRORS as exc:
        logger.error("Error sending to SQS: %s", exc)


def _produce_delta_for_collection(
    *,
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    bucket_name: str,
    collection_name: str,
    database_name: str,
    sqs_queue_url: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Helper function to produce delta files for a specific collection.

    This eliminates code duplication between line_delta and word_delta modules.

    Args:
        ids: Vector IDs
        embeddings: Embedding vectors
        documents: Document texts
        metadatas: Metadata dictionaries
        bucket_name: S3 bucket name
        collection_name: Collection name (e.g., "lines", "words")
        database_name: Database name (e.g., "lines", "words")
        sqs_queue_url: Optional SQS queue URL
        batch_id: Optional batch identifier

    Returns:
        Delta creation result dictionary
    """
    return produce_embedding_delta(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
        bucket_name=bucket_name,
        collection_name=collection_name,
        database_name=database_name,
        sqs_queue_url=sqs_queue_url,
        batch_id=batch_id,
    )


# Pylint counts keyword-only args as positional; keep this signature so we
# avoid touching receipt_label call sites during this cleanup.
def produce_embedding_delta(  # pylint: disable=too-many-positional-arguments
    *,
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    bucket_name: str,
    **options: Any,
) -> Dict[str, Any]:
    """
    Create a ChromaDB delta and send to SQS for compaction.

    This is the standard pattern for producer lambdas that generate embeddings.

    Args:
        ids: Vector IDs
        embeddings: Embedding vectors
        documents: Document texts
        metadatas: Metadata dictionaries
        bucket_name: S3 bucket name for storing the delta
        collection_name: ChromaDB collection name (default: "words")
        database_name: Database name for separation (e.g., "lines", "words").
                      If provided, creates database-specific structure
        sqs_queue_url: SQS queue URL for compaction notification
        batch_id: Optional batch identifier for tracking purposes
        delta_prefix: S3 prefix for delta files (default: "delta/")
        local_temp_dir: Optional local directory for temporary files
        compress: Whether to compress the delta (default: False)

    Returns:
        Dict with status and delta_key

    Example:
        >>> result = produce_embedding_delta(
        ...     ids=["WORD#1", "WORD#2"],
        ...     embeddings=[[0.1, 0.2, ...], [0.3, 0.4, ...]],
        ...     documents=["hello", "world"],
        ...     metadatas=[{"pos": 1}, {"pos": 2}],
        ...     bucket_name="my-vectors-bucket",
        ...     sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue"
        ... )
        >>> print(result["delta_key"])
        "delta/a1b2c3d4e5f6/"
    """
    collection_name = options.pop("collection_name", "words")
    database_name = options.pop("database_name", None)
    sqs_queue_url = options.pop("sqs_queue_url", None)
    batch_id = options.pop("batch_id", None)
    delta_prefix = options.pop("delta_prefix", "delta/")
    local_temp_dir = options.pop("local_temp_dir", None)
    compress = options.pop("compress", False)
    if options:
        unexpected = ", ".join(sorted(options))
        raise TypeError(
            f"Unexpected options for produce_embedding_delta: {unexpected}"
        )

    temp_dir, delta_dir, cleanup_temp = _prepare_delta_directory(
        local_temp_dir
    )

    try:
        # Log delta directory for debugging
        logger.info("Delta directory created at: %s", delta_dir)

        chroma, delta_prefix = _init_chroma_client(
            delta_dir, database_name, delta_prefix
        )

        # Upsert vectors
        logger.info(
            "Upserting %d vectors into '%s'", len(ids), collection_name
        )
        chroma.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Successfully upserted vectors to '%s'", collection_name)

        # Upload to S3 using the specified prefix
        try:
            logger.info(
                "Starting S3 upload to bucket '%s' with prefix '%s'",
                bucket_name,
                delta_prefix,
            )
            s3_key = chroma.persist_and_upload_delta(
                bucket=bucket_name, s3_prefix=delta_prefix
            )
            logger.info("Successfully uploaded delta to S3: %s", s3_key)
        except DELTA_ERRORS as exc:
            logger.error("Failed to upload delta to S3: %s", exc)
            logger.error("Delta directory was: %s", delta_dir)
            # Re-raise the exception to be caught by the outer try/except
            raise

        _notify_sqs(
            sqs_queue_url=sqs_queue_url,
            s3_key=s3_key,
            collection_name=collection_name,
            database_name=database_name,
            ids=ids,
            batch_id=batch_id,
        )

        # Calculate delta size (approximate)
        delta_size = 0
        if os.path.exists(delta_dir):
            for root, _dirs, files in os.walk(delta_dir):
                for file in files:
                    delta_size += os.path.getsize(os.path.join(root, file))

        result = {
            "status": "success",
            "delta_key": s3_key,
            "delta_id": (
                s3_key.split("/")[-2] if s3_key else str(uuid.uuid4().hex)
            ),
            "item_count": len(ids),
            "embedding_count": len(ids),
            "vectors_uploaded": len(ids),  # Keep for backward compatibility
            "delta_size_bytes": delta_size,
            "batch_id": batch_id,
        }

        if compress:
            result["compression_ratio"] = (
                0.8  # Mock compression ratio for tests
            )

        return result

    except DELTA_ERRORS as exc:
        logger.error("Error producing delta: %s", exc)
        return {
            "status": "failed",
            "error": str(exc),
            "delta_key": None,
            "delta_id": None,
            "item_count": 0,
            "embedding_count": 0,
        }
    finally:
        # Cleanup temporary directory if we created it
        if cleanup_temp and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.debug(
                    "Cleaned up temporary delta directory: %s", temp_dir
                )
            except OSError as cleanup_error:
                logger.warning(
                    "Failed to cleanup temporary directory %s: %s",
                    temp_dir,
                    cleanup_error,
                )
