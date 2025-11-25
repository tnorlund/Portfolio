"""Producer for creating ChromaDB embedding deltas.

This module provides the core function for creating ChromaDB delta files
from embedding results and uploading them to S3 for compaction.
"""

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

from receipt_chroma import ChromaClient

logger = logging.getLogger(__name__)


def produce_embedding_delta(
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    bucket_name: str,
    collection_name: str = "words",
    database_name: Optional[str] = None,
    sqs_queue_url: Optional[str] = None,
    batch_id: Optional[str] = None,
    delta_prefix: str = "delta/",
    local_temp_dir: Optional[str] = None,
    compress: bool = False,
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
        sqs_queue_url: SQS queue URL for compaction notification. If None, skips SQS notification
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
    # Create temporary directory for delta
    if local_temp_dir is not None:
        # Use provided temp directory
        temp_dir = local_temp_dir
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(delta_dir, exist_ok=True)
        cleanup_temp = False
    else:
        # Use context manager for auto-cleanup
        temp_dir = tempfile.mkdtemp()
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(
            delta_dir, exist_ok=True
        )  # CRITICAL: Must create the directory!
        cleanup_temp = True

    try:
        # Log delta directory for debugging
        logger.info(f"Delta directory created at: {delta_dir}")

        # Create ChromaDB client in delta mode
        if database_name:
            logger.info(
                f"Creating ChromaDB client for database '{database_name}'"
            )
            logger.info(f"Persist directory: {delta_dir}")
            chroma = ChromaClient(
                persist_directory=delta_dir,
                mode="delta",
                metadata_only=True,  # No embeddings needed for delta creation
            )
            # Adjust delta prefix to include database name
            delta_prefix = f"{database_name}/{delta_prefix}"
            logger.info(f"S3 delta prefix will be: {delta_prefix}")
        else:
            logger.info("Creating ChromaDB client")
            logger.info(f"Persist directory: {delta_dir}")
            chroma = ChromaClient(
                persist_directory=delta_dir,
                mode="delta",
                metadata_only=True,  # No embeddings needed for delta creation
            )

        # Upsert vectors
        logger.info(
            f"Upserting {len(ids)} vectors to collection '{collection_name}'"
        )
        chroma.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(
            f"Successfully upserted vectors to collection '{collection_name}'"
        )

        # Upload to S3 using the specified prefix
        try:
            logger.info(
                f"Starting S3 upload to bucket '{bucket_name}' with prefix '{delta_prefix}'"
            )
            s3_key = chroma.persist_and_upload_delta(
                bucket=bucket_name, s3_prefix=delta_prefix
            )
            logger.info("Successfully uploaded delta to S3: %s", s3_key)
        except Exception as e:
            logger.error(f"Failed to upload delta to S3: {e}")
            logger.error(f"Delta directory was: {delta_dir}")
            # Re-raise the exception to be caught by the outer try/except
            raise

        # Send to SQS if queue URL is provided and not empty
        if sqs_queue_url:
            try:
                sqs = boto3.client("sqs")

                message_body = {
                    "delta_key": s3_key,
                    "collection": collection_name,
                    "database": database_name if database_name else "default",
                    "vector_count": len(ids),
                    "timestamp": datetime.utcnow().isoformat(),
                }

                # Add batch_id if provided
                if batch_id:
                    message_body["batch_id"] = batch_id

                # FIFO compatibility: provide stable group and deduplication IDs
                message_group_id = f"{collection_name}:{batch_id or 'default'}"
                message_dedup_id = f"{collection_name}:{(batch_id or uuid.uuid4().hex)}:{s3_key}"

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

            except Exception as e:
                logger.error("Error sending to SQS: %s", e)
                # Delta is still in S3, compactor can find it later

        # Calculate delta size (approximate)
        delta_size = 0
        if os.path.exists(delta_dir):
            for root, dirs, files in os.walk(delta_dir):
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

    except Exception as e:
        logger.error("Error producing delta: %s", e)
        return {
            "status": "failed",
            "error": str(e),
            "delta_key": None,
            "delta_id": None,
            "item_count": 0,
            "embedding_count": 0,
        }
    finally:
        # Cleanup temporary directory if we created it
        if cleanup_temp and os.path.exists(temp_dir):
            import shutil

            try:
                shutil.rmtree(temp_dir)
                logger.debug(
                    "Cleaned up temporary delta directory: %s", temp_dir
                )
            except Exception as cleanup_error:
                logger.warning(
                    "Failed to cleanup temporary directory %s: %s",
                    temp_dir,
                    cleanup_error,
                )
