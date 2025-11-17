"""ChromaDB compaction handler with chunked processing.

This handler efficiently compacts multiple ChromaDB deltas created during
parallel embedding processing, with support for collection-aware processing.
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import utils.logging # pylint: disable=import-error
from utils.metrics import emf_metrics

import boto3
import chromadb

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.utils.lock_manager import LockManager

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

try:
    from receipt_label.utils.chroma_s3_helpers import (
        upload_snapshot_atomic,
        download_snapshot_atomic,
    )
    from receipt_label.vector_store import upload_snapshot_with_hash

    ATOMIC_UPLOAD_AVAILABLE = True
    ATOMIC_DOWNLOAD_AVAILABLE = True
    HASH_UPLOAD_AVAILABLE = True
except ImportError:
    ATOMIC_UPLOAD_AVAILABLE = False
    ATOMIC_DOWNLOAD_AVAILABLE = False
    HASH_UPLOAD_AVAILABLE = False
    logger.warning(
        "Atomic and hash-enabled uploads not available, using legacy upload"
    )

# Try to import EFS snapshot manager (available in container Lambda)
try:
    # Try absolute import first (Lambda environment)
    from compaction.efs_snapshot_manager import get_efs_snapshot_manager
    EFS_AVAILABLE = True
except ImportError:
    try:
        # Try relative import (test environment)
        from .compaction.efs_snapshot_manager import get_efs_snapshot_manager
        EFS_AVAILABLE = True
    except ImportError:
        EFS_AVAILABLE = False
        logger.info("EFS snapshot manager not available, will use S3-only mode")

# Initialize clients
s3_client = boto3.client("s3")
dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

# Get configuration from environment
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "5"))


def close_chromadb_client(client: Any, collection_name: Optional[str] = None) -> None:
    """
    Properly close ChromaDB client to ensure SQLite files are flushed and unlocked.

    This is critical to prevent corruption when uploading/copying SQLite files
    that are still being written to by an active ChromaDB connection.

    Args:
        client: ChromaDB PersistentClient instance (or wrapper with _client attribute)
        collection_name: Optional collection name for logging
    """
    if client is None:
        return

    try:
        logger.debug(
            "Cleaning up ChromaDB client",
            collection=collection_name or "unknown",
        )

        # Clear collections cache
        if hasattr(client, '_collections'):
            client._collections.clear()

        # Clear the underlying client reference
        if hasattr(client, '_client') and client._client is not None:
            client._client = None

        # Force garbage collection to ensure SQLite connections are closed
        # This is necessary because ChromaDB doesn't expose a close() method
        import gc
        gc.collect()

        # Small delay to ensure file handles are released by OS
        import time as _time
        _time.sleep(0.1)

        logger.debug(
            "ChromaDB client cleaned up",
            collection=collection_name or "unknown",
        )
    except Exception as e:
        logger.debug(
            "Error cleaning up ChromaDB client (non-critical)",
            error=str(e),
            collection=collection_name or "unknown",
        )


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Main entry point for Lambda handler.

    Routes to either chunk processing or final merge based on operation.
    """
    return compact_handler(event, context)


def compact_handler(
    event: Dict[str, Any], context: Any  # pylint: disable=unused-argument
) -> Dict[str, Any]:
    """
    Compact multiple delta files into ChromaDB using chunked processing.

    This handler supports two modes:
    1. Chunked processing: Process deltas in chunks without locks
    2. Final merge: Acquire lock and merge all intermediate chunks

    Input event format for chunked processing:
    {
        "operation": "process_chunk",
        "batch_id": "batch-uuid",
        "chunk_index": 0,
        "delta_results": [
            {
                "delta_key": "delta/abc123/",
                "delta_id": "abc123",
                "embedding_count": 100,
                "collection": "receipt_words"  # or "receipt_lines"
            },
            ...
        ]
    }

    Input event format for final merge:
    {
        "operation": "final_merge",
        "batch_id": "batch-uuid",
        "total_chunks": 5
    }
    """
    logger.info("Starting ChromaDB compaction handler")
    logger.info("Event", event=json.dumps(event))

    # Determine operation mode
    operation = event.get("operation")

    if operation == "process_chunk":
        return process_chunk_handler(event)
    if operation == "merge_chunk_group":
        return merge_chunk_group_handler(event)
    if operation == "final_merge":
        return final_merge_handler(event)

    logger.error(
        "Invalid operation. Expected 'process_chunk', "
        "'merge_chunk_group', or 'final_merge'",
        operation=operation,
    )
    return {
        "statusCode": 400,
        "error": f"Invalid operation: {operation}",
        "message": (
            "Operation must be 'process_chunk', 'merge_chunk_group', "
            "or 'final_merge'"
        ),
    }


def process_chunk_handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a chunk of deltas without acquiring locks.

    Writes output to intermediate/{batch_id}/chunk-{index}/ in S3.

    Supports two modes:
    1. Inline mode: delta_results provided directly in event
    2. S3 mode: chunks_s3_key and chunks_s3_bucket provided, downloads specific chunk by chunk_index
    """
    logger.info("Processing chunk compaction")

    batch_id = event.get("batch_id")
    chunk_index = event.get("chunk_index")
    delta_results = event.get("delta_results", [])
    database_name = event.get("database")  # Track database for this chunk

    # S3 mode: download chunk from S3 if delta_results not provided
    chunks_s3_key = event.get("chunks_s3_key")
    chunks_s3_bucket = event.get("chunks_s3_bucket")

    if not batch_id:
        return {
            "statusCode": 400,
            "error": "batch_id is required for chunk processing",
        }

    if chunk_index is None:
        return {
            "statusCode": 400,
            "error": "chunk_index is required for chunk processing",
        }

    # If delta_results not provided but S3 info is, download chunk from S3
    if not delta_results and chunks_s3_key and chunks_s3_bucket:
        logger.info(
            "Downloading chunk from S3",
            chunk_index=chunk_index,
            s3_key=chunks_s3_key,
            bucket=chunks_s3_bucket,
        )
        try:
            import boto3
            import json
            import tempfile
            import os

            s3_client = boto3.client("s3")

            # Download chunks file from S3
            # Use mktemp instead of NamedTemporaryFile to avoid file handle leaks
            tmp_file_path = tempfile.mktemp(suffix=".json")

            try:
                s3_client.download_file(chunks_s3_bucket, chunks_s3_key, tmp_file_path)

                with open(tmp_file_path, "r", encoding="utf-8") as f:
                    all_chunks = json.load(f)

                # Find the specific chunk by chunk_index
                chunk_found = None
                for chunk in all_chunks:
                    if chunk.get("chunk_index") == chunk_index:
                        chunk_found = chunk
                        break

                if not chunk_found:
                    return {
                        "statusCode": 404,
                        "error": f"Chunk {chunk_index} not found in S3 chunks file",
                        "batch_id": batch_id,
                        "chunk_index": chunk_index,
                    }

                # Extract delta_results from the chunk
                delta_results = chunk_found.get("delta_results", [])
                logger.info(
                    "Downloaded chunk from S3",
                    chunk_index=chunk_index,
                    delta_count=len(delta_results),
                )
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass
        except Exception as e:
            logger.error(
                "Failed to download chunk from S3",
                chunk_index=chunk_index,
                error=str(e),
            )
            return {
                "statusCode": 500,
                "error": f"Failed to download chunk from S3: {str(e)}",
                "batch_id": batch_id,
                "chunk_index": chunk_index,
            }

    if not delta_results:
        logger.info(
            "No delta results in chunk, skipping", chunk_index=chunk_index
        )
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "embeddings_processed": 0,
            "message": "Empty chunk processed",
        }

    # With Map state, each chunk should already be limited to 10 deltas
    # But we'll enforce the limit here as a safety measure
    chunk_deltas = delta_results[:10]
    if len(delta_results) > 10:
        logger.warning(
            "Chunk has more deltas than expected max 10. Processing first 10.",
            chunk_index=chunk_index,
            delta_count=len(delta_results),
        )

    # Group chunk deltas by collection name for collection-aware processing
    deltas_by_collection: dict[str, list] = {}
    for result in chunk_deltas:
        collection = result.get(
            "collection", "receipt_words"
        )  # Default for backward compat
        if collection not in deltas_by_collection:
            deltas_by_collection[collection] = []
        deltas_by_collection[collection].append(result)

    logger.info(
        "Processing chunk with deltas across collections",
        chunk_index=chunk_index,
        delta_count=len(chunk_deltas),
        collection_count=len(deltas_by_collection),
        batch_id=batch_id,
    )

    try:
        # Process chunk deltas with collection awareness
        chunk_result = process_chunk_deltas(
            batch_id, chunk_index, chunk_deltas, deltas_by_collection
        )

        # Prepare minimal response for Map state
        response = {
            "intermediate_key": chunk_result["intermediate_key"],
        }

        logger.info(
            "Chunk processing completed",
            chunk_index=chunk_index,
            response=response,
        )
        return response

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "Chunk processing failed", chunk_index=chunk_index, error=str(e)
        )
        # Raise exception instead of returning error object
        # This allows Step Functions to properly handle the failure with retry logic
        raise


def merge_chunk_group_handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge a group of intermediate chunks WITHOUT acquiring a lock.

    This operation merges up to 10 intermediate chunk snapshots into a single
    intermediate snapshot for hierarchical processing. No mutex lock is required
    as this operates on intermediate data, not the final snapshot.

    Input format:
    {
        "operation": "merge_chunk_group",
        "batch_id": "batch-group-0",
        "group_index": 0,
        "chunk_group": [
            {"intermediate_key": "intermediate/batch-id/chunk-0/"},
            {"intermediate_key": "intermediate/batch-id/chunk-1/"},
            ...
        ],
        "database": "lines" or "words"
    }

    OR (S3 mode):
    {
        "operation": "merge_chunk_group",
        "batch_id": "batch-group-0",
        "group_index": 0,
        "chunk_group": None,  # Will be loaded from S3
        "groups_s3_key": "chunk_groups/batch-id/groups.json",
        "groups_s3_bucket": "bucket-name",
        "database": "lines" or "words"
    }

    Returns:
    {
        "intermediate_key": "intermediate/batch-group-0/merged/"
    }
    """
    logger.info("Starting chunk group merge (no lock)")

    batch_id = event.get("batch_id")
    group_index = event.get("group_index", 0)
    # Note: Step Functions passes null as None in Python
    # event.get() returns None if key exists with null value, [] only if key is missing
    chunk_group = event.get("chunk_group")
    if chunk_group is None:
        chunk_group = []  # Default to empty list for easier handling
    database_name = event.get("database", "lines")

    # S3 mode: download group from S3 if chunk_group not provided
    groups_s3_key = event.get("groups_s3_key")
    groups_s3_bucket = event.get("groups_s3_bucket")

    if not batch_id:
        return {
            "statusCode": 400,
            "error": "batch_id is required for chunk group merging",
        }

    # If chunk_group is None or empty and S3 info is provided, download from S3
    # Note: Step Functions passes null as None in Python, and event.get() returns None if key exists with null value
    needs_s3_download = (
        (chunk_group is None or chunk_group == [] or not chunk_group)
        and groups_s3_key
        and groups_s3_bucket
    )

    if needs_s3_download:
        logger.info(
            "Downloading group from S3",
            group_index=group_index,
            s3_key=groups_s3_key,
            bucket=groups_s3_bucket,
            chunk_group_type=type(chunk_group).__name__ if chunk_group is not None else "None",
        )
        try:
            # Download groups file from S3
            # Use mktemp instead of NamedTemporaryFile to avoid file handle leaks
            tmp_file_path = tempfile.mktemp(suffix=".json")

            try:
                s3_client.download_file(groups_s3_bucket, groups_s3_key, tmp_file_path)

                with open(tmp_file_path, "r", encoding="utf-8") as f:
                    all_groups = json.load(f)

                # Validate all_groups is a list
                if not isinstance(all_groups, list):
                    logger.error(
                        "Invalid groups format in S3 - expected list",
                        groups_type=type(all_groups).__name__,
                        s3_key=groups_s3_key,
                    )
                    return {
                        "statusCode": 500,
                        "error": f"Invalid groups format in S3: expected list, got {type(all_groups).__name__}",
                        "batch_id": batch_id,
                        "group_index": group_index,
                    }

                # Find the specific group by group_index
                if group_index >= len(all_groups):
                    return {
                        "statusCode": 404,
                        "error": f"Group {group_index} not found in S3 groups file (total groups: {len(all_groups)})",
                        "batch_id": batch_id,
                        "group_index": group_index,
                    }

                chunk_group = all_groups[group_index]

                # Validate downloaded group is a list
                if not isinstance(chunk_group, list):
                    logger.error(
                        "Invalid group format in S3 - expected list",
                        group_type=type(chunk_group).__name__,
                        group_index=group_index,
                        s3_key=groups_s3_key,
                    )
                    return {
                        "statusCode": 500,
                        "error": f"Invalid group format in S3: expected list, got {type(chunk_group).__name__}",
                        "batch_id": batch_id,
                        "group_index": group_index,
                    }

                logger.info(
                    "Downloaded group from S3",
                    group_index=group_index,
                    chunk_count=len(chunk_group),
                )
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass
        except Exception as e:
            logger.error(
                "Failed to download group from S3",
                group_index=group_index,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            # Raise exception instead of returning error object
            # This allows Step Functions to properly handle the failure with retry logic
            raise

    # Ensure chunk_group is a list after S3 download (or handle None/empty cases)
    if chunk_group is None:
        chunk_group = []

    # Validate chunk_group is a list before processing
    if not isinstance(chunk_group, list):
        logger.error(
            "Invalid chunk_group type - expected list",
            batch_id=batch_id,
            group_index=group_index,
            chunk_group_type=type(chunk_group).__name__,
            chunk_group_value=str(chunk_group)[:200],  # Truncate for logging
        )
        return {
            "statusCode": 400,
            "error": f"chunk_group must be a list, got {type(chunk_group).__name__}",
            "batch_id": batch_id,
            "group_index": group_index,
        }

    if not chunk_group:
        logger.warning(
            "Empty chunk group detected - this should not happen if groups were created correctly",
            batch_id=batch_id,
            group_index=group_index,
            groups_s3_key=groups_s3_key,
            groups_s3_bucket=groups_s3_bucket,
        )
        # Return a response that will be filtered out by final merge
        # Don't return intermediate_key since there's nothing to merge
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "group_index": group_index,
            "message": "Empty chunk group processed - no intermediate_key",
            "empty": True,  # Flag for filtering
        }

    # Limit to 10 chunks per group for consistent processing
    original_count = len(chunk_group)
    chunk_group = chunk_group[:10]
    if original_count > 10:
        logger.warning(
            "Chunk group has more than 10 chunks, processing first 10",
            group_index=group_index,
            chunk_count=original_count,
        )

    # Extract intermediate keys
    intermediate_keys = []
    for chunk in chunk_group:
        if isinstance(chunk, dict) and "intermediate_key" in chunk:
            intermediate_keys.append(chunk["intermediate_key"])
        elif isinstance(chunk, str):
            intermediate_keys.append(chunk)
        else:
            logger.error(
                "Invalid chunk format in group",
                chunk=chunk,
                chunk_type=type(chunk),
            )
            return {
                "statusCode": 400,
                "error": f"Invalid chunk format: {chunk}",
            }

    logger.info(
        "Merging chunk group",
        batch_id=batch_id,
        group_index=group_index,
        chunk_count=len(intermediate_keys),
        database=database_name,
    )

    try:
        # Perform the merge without locks
        merge_result = perform_intermediate_merge(
            batch_id, group_index, intermediate_keys, database_name
        )

        # Return intermediate key for further processing
        return {
            "intermediate_key": merge_result["intermediate_key"],
        }

    except Exception as e:
        logger.error(
            "Chunk group merge failed",
            batch_id=batch_id,
            group_index=group_index,
            error=str(e),
        )
        return {
            "statusCode": 500,
            "error": str(e),
            "batch_id": batch_id,
            "group_index": group_index,
            "message": "Chunk group merge failed",
        }


def final_merge_handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Final merge step that ALWAYS acquires mutex lock and merges to final snapshot.

    This is the ONLY operation that should modify the final snapshot and
    must always acquire a mutex lock to prevent conflicts.

    Input format:
    {
        "operation": "final_merge",
        "batch_id": "batch-uuid",
        "chunk_results": [
            {"intermediate_key": "intermediate/batch-id/chunk-0/"},
            {"intermediate_key": "intermediate/batch-group-0/merged/"},
            ...
        ],
        "database": "lines" or "words"
    }
    """
    logger.info("Starting FINAL merge with mutex lock")

    batch_id = event.get("batch_id")
    chunk_results = event.get("chunk_results", [])
    database_name = event.get("database", "lines")
    # Pass through poll_results_s3_key/bucket for MarkBatchesComplete
    poll_results_s3_key = event.get("poll_results_s3_key")
    poll_results_s3_bucket = event.get("poll_results_s3_bucket")

    if not batch_id:
        return {
            "statusCode": 400,
            "error": "batch_id is required for final merge",
        }

    if not chunk_results:
        return {
            "statusCode": 400,
            "error": "chunk_results is required for final merge",
        }

    # Filter out empty groups and error responses before processing
    original_count = len(chunk_results)
    valid_chunk_results = []
    for chunk in chunk_results:
        if isinstance(chunk, dict):
            # Include only chunks with intermediate_key (valid merge results)
            if "intermediate_key" in chunk:
                valid_chunk_results.append(chunk)
            # Skip error responses and empty groups
            elif chunk.get("empty") or ("message" in chunk and "Empty" in chunk.get("message", "")):
                logger.warning(
                    "Filtering out empty group from final merge",
                    chunk=chunk,
                    batch_id=batch_id,
                )
            elif "statusCode" in chunk and chunk.get("statusCode") != 200:
                logger.error(
                    "Filtering out error response from final merge",
                    chunk=chunk,
                    batch_id=batch_id,
                )

    if not valid_chunk_results:
        error_msg = f"No valid intermediate keys found in {original_count} chunk results. All groups were empty or failed."
        logger.error(
            "Final merge failed - no valid chunks",
            batch_id=batch_id,
            total_chunks=original_count,
            database=database_name,
        )
        return {
            "statusCode": 500,
            "error": error_msg,
            "batch_id": batch_id,
            "message": "Final merge failed - no valid intermediate snapshots to merge",
        }

    # Use filtered results
    chunk_results = valid_chunk_results
    filtered_count = original_count - len(valid_chunk_results)

    logger.info(
        "Final merge with lock - processing intermediate snapshots",
        chunk_count=len(chunk_results),
        batch_id=batch_id,
        database=database_name,
        filtered_count=filtered_count,
        original_count=original_count,
    )

    # Determine collection from database name
    collection = (
        ChromaDBCollection.LINES
        if database_name == "lines"
        else ChromaDBCollection.WORDS
    )

    # Acquire lock for final merge
    lock_manager = LockManager(
        dynamo_client,
        collection=collection,
        heartbeat_interval=heartbeat_interval,
        lock_duration_minutes=lock_duration_minutes,
    )
    try:
        lock_acquired = lock_manager.acquire(f"chroma-final-merge-{batch_id}")
        if not lock_acquired:
            logger.warning("Could not acquire lock for final merge")
            return {
                "statusCode": 423,
                "error": "Could not acquire lock",
                "message": "Another process is performing final merge",
            }

        # Start heartbeat
        lock_manager.start_heartbeat()

        # Perform final merge with database awareness
        # Pass lock_manager for CAS validation during atomic upload (consistent with compactor)
        merge_result = perform_final_merge(
            batch_id, len(chunk_results), database_name, chunk_results, lock_manager
        )

        # Clean up intermediate chunks - extract intermediate keys for cleanup
        # Filter out error responses and empty groups
        intermediate_keys = []
        for chunk in chunk_results:
            # Skip error responses (empty groups or failed merges)
            if isinstance(chunk, dict):
                if "intermediate_key" in chunk:
                    intermediate_keys.append(chunk["intermediate_key"])
                elif "statusCode" in chunk and chunk.get("statusCode") != 200:
                    # Error response - log but skip
                    logger.warning(
                        "Skipping error response in chunk_results",
                        chunk=chunk,
                        batch_id=batch_id,
                    )
                elif "message" in chunk and "Empty" in chunk.get("message", ""):
                    # Empty group response - skip silently
                    logger.debug(
                        "Skipping empty group response",
                        chunk=chunk,
                        batch_id=batch_id,
                    )

        cleanup_intermediate_chunks_by_keys(intermediate_keys)

        # Always return full format for final merge
        # Pass through poll_results_s3_key/bucket for MarkBatchesComplete
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "snapshot_key": merge_result["snapshot_key"],
            "total_embeddings": merge_result["total_embeddings"],
            "processing_time_seconds": merge_result["processing_time"],
            "message": "Final merge completed successfully",
            "poll_results_s3_key": poll_results_s3_key,  # Pass through for MarkBatchesComplete
            "poll_results_s3_bucket": poll_results_s3_bucket,  # Pass through for MarkBatchesComplete
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Final merge failed", error=str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "batch_id": batch_id,
            "message": "Final merge failed",
        }
    finally:
        # Stop heartbeat and release lock
        lock_manager.stop_heartbeat()
        lock_manager.release()


def process_chunk_deltas(
    batch_id: str,
    chunk_index: int,
    chunk_deltas: List[Dict[str, Any]],
    deltas_by_collection: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Process a chunk of deltas and save to intermediate storage.

    Supports collection-aware processing to handle both words and lines.

    If any delta fails to process (e.g., corrupted), the entire chunk fails.
    This ensures all-or-nothing behavior - either all deltas are processed
    successfully or the chunk fails completely.
    """
    start_time = time.time()
    bucket = os.environ["CHROMADB_BUCKET"]
    temp_dir = tempfile.mkdtemp()
    total_embeddings = 0

    try:
        # Initialize ChromaDB in memory
        chroma_client = chromadb.PersistentClient(path=temp_dir)

        # Process each collection separately
        for collection_name, collection_deltas in deltas_by_collection.items():
            logger.info(
                "Processing deltas for collection",
                delta_count=len(collection_deltas),
                collection_name=collection_name,
            )

            # Get or create collection
            try:
                collection = chroma_client.get_collection(collection_name)
            except Exception:
                # Collection doesn't exist, create it
                collection = chroma_client.create_collection(
                    collection_name,
                    metadata={
                        "chunk_index": chunk_index,
                        "batch_id": batch_id,
                    },
                )

            # Process deltas for this collection
            # If any delta fails, the entire chunk fails (all-or-nothing behavior)
            deltas_processed = 0
            deltas_failed = 0
            for i, delta in enumerate(collection_deltas):
                delta_key = delta["delta_key"]

                try:
                    # Download and merge delta
                    # If this fails, the exception will propagate and fail the entire chunk
                    embeddings_added = download_and_merge_delta(
                        bucket, delta_key, collection, temp_dir
                    )
                    total_embeddings += embeddings_added
                    deltas_processed += 1
                    logger.info(
                        "Merged embeddings from delta into collection",
                        embeddings_added=embeddings_added,
                        delta_key=delta_key,
                        collection_name=collection_name,
                        current=i + 1,
                        total=len(collection_deltas),
                    )
                except Exception as e:
                    deltas_failed += 1
                    # Log chunk-level validation metrics
                    emf_metrics.log_metrics(
                        {
                            "ChunkDeltaValidationFailures": 1,
                        },
                        dimensions={
                            "validation_stage": "process_chunks",
                            "collection": collection_name,
                        },
                        properties={
                            "batch_id": batch_id,
                            "chunk_index": chunk_index,
                            "delta_key": delta_key,
                            "error": str(e),
                        },
                    )
                    # Re-raise to fail the entire chunk
                    raise

                # Memory usage available in CloudWatch metrics

        # Check what files are in temp directory before upload
        temp_files = []
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                temp_files.append(f"{file} ({file_size} bytes)")

        logger.info(
            "Temp directory contents before upload",
            temp_dir=temp_dir,
            files=temp_files,
            total_embeddings=total_embeddings,
        )

        # Simple cleanup for ChromaDB 1.0.21 (testing if workarounds are needed)
        close_chromadb_client(chroma_client, collection_name="chunk_processing")
        chroma_client = None

        # Upload intermediate chunk to S3
        intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
        logger.info(
            "Starting intermediate chunk upload to S3",
            intermediate_key=intermediate_key,
            bucket=bucket,
            temp_dir=temp_dir,
            total_embeddings=total_embeddings,
        )

        upload_result = upload_to_s3(
            temp_dir, bucket, intermediate_key, calculate_hash=False
        )  # No hash for intermediate chunks

        logger.info(
            "Completed intermediate chunk upload to S3",
            intermediate_key=intermediate_key,
            upload_result=upload_result,
            total_embeddings=total_embeddings,
        )

        processing_time = time.time() - start_time
        logger.info(
            "Chunk processed",
            chunk_index=chunk_index,
            embeddings_processed=total_embeddings,
            processing_time_seconds=processing_time,
        )

        return {
            "intermediate_key": intermediate_key,
            "embeddings_processed": total_embeddings,
            "processing_time": processing_time,
        }

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def download_and_merge_delta(
    bucket: str, delta_key: str, collection: Any, temp_dir: str
) -> int:
    """
    Download a delta from S3 and merge it into the collection.

    Returns the number of embeddings added.
    """
    delta_temp = tempfile.mkdtemp(dir=temp_dir)
    validation_success = False
    validation_error_type = None
    validation_start_time = time.time()

    try:
        # Download delta from S3
        download_from_s3(bucket, delta_key, delta_temp)

        # Validate delta directory has required files before opening
        # ChromaDB requires at least a SQLite database file
        delta_path = Path(delta_temp)
        sqlite_files = list(delta_path.rglob("*.sqlite*"))
        if not sqlite_files:
            validation_duration = time.time() - validation_start_time
            logger.error(
                "No SQLite files found in delta",
                delta_key=delta_key,
                delta_temp=delta_temp,
                files=list(delta_path.rglob("*")),
                validation_duration=validation_duration,
            )
            validation_error_type = "no_sqlite_files"
            raise RuntimeError(
                f"Delta {delta_key} appears to be corrupted: no SQLite files found"
            )

        # Load delta into temporary ChromaDB instance
        # Wrap in try-except to provide better error messages for corrupted deltas
        try:
            delta_client = chromadb.PersistentClient(path=delta_temp)
            validation_success = True
            validation_duration = time.time() - validation_start_time
        except Exception as e:
            validation_duration = time.time() - validation_start_time
            logger.error(
                "Failed to open delta ChromaDB client",
                delta_key=delta_key,
                delta_temp=delta_temp,
                error=str(e),
                error_type=type(e).__name__,
                sqlite_files=[str(f) for f in sqlite_files],
            )
            validation_error_type = f"chromadb_open_failed_{type(e).__name__}"
            # Re-raise with more context
            raise RuntimeError(
                f"Failed to open delta {delta_key}: {str(e)}. "
                f"This may indicate the delta was corrupted during upload or download."
            ) from e

        # Get the first (and typically only) collection from the delta
        # The delta should contain exactly one collection with all embeddings
        delta_collections = delta_client.list_collections()
        if not delta_collections:
            logger.warning(
                "No collections found in delta", delta_key=delta_key
            )
            validation_error_type = "no_collections"
            return 0

        # Use the first collection from the delta (there should only be one)
        delta_collection = delta_collections[0]
        logger.info(
            "Found collection in delta",
            collection_name=delta_collection.name,
            delta_key=delta_key,
        )

        # Get total count first to process in batches
        total_count = delta_collection.count()
        if total_count == 0:
            return 0

        logger.info(
            "Processing embeddings from delta in batches",
            total_count=total_count,
        )

        # Process embeddings in batches to reduce memory usage
        batch_size = 1000  # Process 1000 embeddings at a time
        total_processed = 0

        for offset in range(0, total_count, batch_size):
            # Get batch of embeddings
            batch_results = delta_collection.get(
                include=["embeddings", "documents", "metadatas"],
                limit=batch_size,
                offset=offset,
            )

            if batch_results["ids"]:
                # Upsert batch into main collection
                collection.upsert(
                    ids=batch_results["ids"],
                    embeddings=batch_results["embeddings"],
                    documents=batch_results["documents"],
                    metadatas=batch_results["metadatas"],
                )
                total_processed += len(batch_results["ids"])
                logger.debug(
                    "Processed batch",
                    offset_start=offset,
                    offset_end=offset + len(batch_results["ids"]),
                    embedding_count=len(batch_results["ids"]),
                )

        logger.info(
            "Successfully processed embeddings from delta",
            count=total_processed,
        )

        # Log validation metrics for successful delta processing
        collection_name = "unknown"
        try:
            if hasattr(collection, 'name'):
                collection_name = collection.name
        except Exception:
            pass

        emf_metrics.log_metrics(
            {
                "DeltaValidationSuccess": 1,
                "DeltaValidationAttempts": 1,
                "DeltaValidationDuration": validation_duration,
            },
            dimensions={
                "validation_stage": "process_chunks",
                "collection": collection_name,
            },
            properties={
                "delta_key": delta_key,
                "embeddings_processed": total_processed,
            },
        )

        return total_processed

    except Exception as e:
        # Log the error with context before re-raising
        logger.error(
            "Error processing delta",
            delta_key=delta_key,
            error=str(e),
            error_type=type(e).__name__,
        )

        # Log validation failure metrics
        collection_name = "unknown"
        try:
            if 'collection' in locals() and hasattr(collection, 'name'):
                collection_name = collection.name
        except Exception:
            pass

        # Get validation duration if available
        validation_duration = 0.0
        try:
            if 'validation_duration' in locals():
                validation_duration = validation_duration
            else:
                validation_duration = time.time() - validation_start_time
        except Exception:
            pass

        emf_metrics.log_metrics(
            {
                "DeltaValidationSuccess": 0,
                "DeltaValidationAttempts": 1,
                "DeltaValidationFailures": 1,
                "DeltaValidationDuration": validation_duration,
            },
            dimensions={
                "validation_stage": "process_chunks",
                "error_type": validation_error_type or type(e).__name__,
                "collection": collection_name,
            },
            properties={
                "delta_key": delta_key,
                "error": str(e),
            },
        )

        raise
    finally:
        # CRITICAL: Close delta client before cleanup to ensure SQLite files are flushed
        try:
            if 'delta_client' in locals() and delta_client is not None:
                close_chromadb_client(delta_client, collection_name="delta_processing")
                delta_client = None
        except Exception:
            pass

        # Clean up delta temp directory
        shutil.rmtree(delta_temp, ignore_errors=True)


def perform_intermediate_merge(
    batch_id: str,
    group_index: int,
    intermediate_keys: List[str],
    database_name: str,
) -> Dict[str, Any]:
    """
    Merge intermediate chunks into a single intermediate snapshot WITHOUT locks.

    This function is used by merge_chunk_group to merge multiple intermediate
    snapshots into a single intermediate snapshot for hierarchical processing.

    Args:
        batch_id: Unique identifier for this batch group
        group_index: Index of the group being merged
        intermediate_keys: List of S3 keys to intermediate snapshots
        database_name: Database name ('lines' or 'words')

    Returns:
        Dictionary with intermediate_key for the merged snapshot
    """
    start_time = time.time()
    bucket = os.environ["CHROMADB_BUCKET"]
    temp_dir = tempfile.mkdtemp()
    total_embeddings = 0

    # Create intermediate output key
    intermediate_key = f"intermediate/{batch_id}-group-{group_index}/merged/"

    try:
        # Initialize ChromaDB in memory for merging
        chroma_client = chromadb.PersistentClient(path=temp_dir)

        logger.info(
            "Starting intermediate merge",
            batch_id=batch_id,
            group_index=group_index,
            chunk_count=len(intermediate_keys),
            intermediate_key=intermediate_key,
        )

        # Process each intermediate chunk
        for i, chunk_key in enumerate(intermediate_keys):
            logger.info(
                "Processing intermediate chunk",
                current_chunk=i + 1,
                total_chunks=len(intermediate_keys),
                chunk_key=chunk_key,
            )
            chunk_temp = tempfile.mkdtemp()

            try:
                # Download intermediate chunk
                download_from_s3(bucket, chunk_key, chunk_temp)
                logger.info(
                    "Downloaded intermediate chunk",
                    chunk_index=i,
                    chunk_key=chunk_key,
                )

                # Load chunk ChromaDB (simple for 1.0.21 testing)
                chunk_client = chromadb.PersistentClient(path=chunk_temp)

                # Merge all collections from this chunk
                for collection_meta in chunk_client.list_collections():
                    collection_name = collection_meta.name
                    chunk_collection = chunk_client.get_collection(
                        collection_name
                    )
                    chunk_count = chunk_collection.count()

                    if chunk_count == 0:
                        logger.info(
                            "Skipping empty collection",
                            collection_name=collection_name,
                        )
                        continue

                    logger.info(
                        "Merging collection from chunk",
                        collection_name=collection_name,
                        embedding_count=chunk_count,
                    )

                    # Get or create collection in main client
                    try:
                        main_collection = chroma_client.get_collection(
                            collection_name
                        )
                    except Exception:
                        main_collection = chroma_client.create_collection(
                            collection_name
                        )

                    # Get all data from chunk collection
                    chunk_data = chunk_collection.get(
                        include=["embeddings", "documents", "metadatas"]
                    )

                    if chunk_data["ids"]:
                        # Add to main collection
                        main_collection.add(
                            ids=chunk_data["ids"],
                            embeddings=chunk_data["embeddings"],
                            documents=chunk_data["documents"],
                            metadatas=chunk_data["metadatas"],
                        )
                        total_embeddings += len(chunk_data["ids"])

                        logger.info(
                            "Merged chunk collection data",
                            collection_name=collection_name,
                            embeddings_added=len(chunk_data["ids"]),
                            total_embeddings=total_embeddings,
                        )

            except Exception as e:
                logger.error(
                    "Failed to process intermediate chunk",
                    chunk_key=chunk_key,
                    chunk_index=i,
                    current_chunk=i + 1,
                    total_chunks=len(intermediate_keys),
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,  # Include full traceback
                )
                raise
            finally:
                # CRITICAL: Close chunk client before cleanup to ensure SQLite files are flushed
                try:
                    if 'chunk_client' in locals() and chunk_client is not None:
                        logger.debug(
                            "Closing chunk client before cleanup",
                            chunk_index=i,
                        )
                        close_chromadb_client(chunk_client, collection_name="group_merge")
                        chunk_client = None
                except Exception as close_error:
                    logger.warning(
                        "Error closing chunk client",
                        chunk_index=i,
                        error=str(close_error),
                    )
                finally:
                    # Clean up temp directory
                    try:
                        shutil.rmtree(chunk_temp, ignore_errors=True)
                    except Exception as cleanup_error:
                        logger.warning(
                            "Error cleaning up chunk temp directory",
                            chunk_index=i,
                            error=str(cleanup_error),
                        )

        logger.info(
            "Intermediate merge completed",
            total_embeddings=total_embeddings,
            processing_time=time.time() - start_time,
        )

        # CRITICAL: Close ChromaDB client BEFORE uploading to ensure SQLite files are flushed and unlocked
        close_chromadb_client(chroma_client, collection_name="intermediate_merge")
        chroma_client = None

        # Upload merged intermediate snapshot
        upload_result = upload_to_s3(
            temp_dir, bucket, intermediate_key, calculate_hash=False
        )

        logger.info(
            "Uploaded merged intermediate snapshot",
            intermediate_key=intermediate_key,
            upload_result=upload_result,
        )

        return {
            "intermediate_key": intermediate_key,
            "total_embeddings": total_embeddings,
            "processing_time": time.time() - start_time,
        }

    except Exception as e:
        logger.error("Intermediate merge failed", error=str(e))
        raise
    finally:
        # Clean up temporary directory
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def perform_final_merge(
    batch_id: str,
    total_chunks: int,
    database_name: Optional[str] = None,
    chunk_results: Optional[List[Dict[str, Any]]] = None,
    lock_manager: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Perform the final merge of all intermediate chunks into a snapshot.

    Args:
        batch_id: Unique identifier for this batch
        total_chunks: Number of chunks to merge
        database_name: Database name ('lines' or 'words') for separate snapshots
        chunk_results: List of chunk results with intermediate keys
        lock_manager: Optional lock manager for CAS validation during atomic upload
    """
    start_time = time.time()
    bucket = os.environ["CHROMADB_BUCKET"]
    temp_dir = tempfile.mkdtemp()
    total_embeddings = 0

    # Determine snapshot paths based on database
    if database_name:
        snapshot_key = f"{database_name}/snapshot/latest/"
        collection_name = database_name
        logger.info(
            "Using database-specific snapshot path", snapshot_key=snapshot_key
        )
    else:
        # Backward compatibility - unified snapshot
        snapshot_key = "snapshot/latest/"
        collection_name = "words"  # Default for backward compatibility
        logger.info("Using unified snapshot path for backward compatibility")

    # Storage mode configuration - check if EFS should be used
    storage_mode = os.environ.get("CHROMADB_STORAGE_MODE", "auto").lower()
    efs_root = os.environ.get("CHROMA_ROOT")

    # Determine storage mode
    if storage_mode == "s3":
        use_efs = False
        mode_reason = "explicitly set to S3-only"
    elif storage_mode == "efs":
        use_efs = EFS_AVAILABLE
        mode_reason = "explicitly set to EFS" if use_efs else "EFS not available"
    elif storage_mode == "auto":
        # Auto-detect based on EFS availability
        use_efs = EFS_AVAILABLE and efs_root and efs_root != "/tmp/chroma"
        mode_reason = f"auto-detected (efs_root={'available' if use_efs else 'not available'})"
    else:
        # Default to S3-only for unknown modes
        use_efs = False
        mode_reason = f"unknown mode '{storage_mode}', defaulting to S3-only"

    logger.info(
        "Storage mode configuration",
        storage_mode=storage_mode,
        efs_root=efs_root,
        use_efs=use_efs,
        mode_reason=mode_reason,
        collection=collection_name,
        efs_available=EFS_AVAILABLE,
    )

    efs_manager = None
    efs_snapshot_path = None
    local_snapshot_path = None

    try:
        # Load snapshot from EFS if available, otherwise from S3
        if use_efs and EFS_AVAILABLE:
            logger.info("Using EFS + S3 hybrid approach", collection=collection_name)
            efs_manager = get_efs_snapshot_manager(collection_name, logger, metrics=None)

            # Get latest version from S3 pointer
            latest_version = efs_manager.get_latest_s3_version()
            if not latest_version:
                logger.warning("Failed to get latest S3 version, falling back to S3-only")
                use_efs = False
            else:
                # Ensure snapshot is available on EFS
                snapshot_result = efs_manager.ensure_snapshot_available(latest_version)
                if snapshot_result["status"] != "available":
                    logger.warning(
                        "Failed to ensure snapshot availability on EFS, falling back to S3",
                        result=snapshot_result
                    )
                    use_efs = False
                else:
                    # Copy EFS snapshot to local temp directory for ChromaDB operations
                    efs_snapshot_path = snapshot_result["efs_path"]
                    local_snapshot_path = tempfile.mkdtemp()

                    copy_start_time = time.time()
                    shutil.copytree(efs_snapshot_path, local_snapshot_path, dirs_exist_ok=True)
                    copy_time_ms = (time.time() - copy_start_time) * 1000

                    temp_dir = local_snapshot_path

                    logger.info(
                        "Using EFS snapshot (copied to local)",
                        collection=collection_name,
                        version=latest_version,
                        efs_path=efs_snapshot_path,
                        local_path=local_snapshot_path,
                        copy_time_ms=copy_time_ms,
                        source=snapshot_result.get("source", "unknown")
                    )

        # Fallback to S3 if EFS not available or failed
        if not use_efs:
            # Use atomic download which automatically initializes empty snapshot if needed
            if ATOMIC_DOWNLOAD_AVAILABLE:
                logger.info(
                    "Downloading snapshot using atomic download",
                    collection=collection_name,
                )
                download_result = download_snapshot_atomic(
                    bucket=bucket,
                    collection=collection_name,
                    local_path=temp_dir,
                    verify_integrity=False,  # Skip integrity check for speed
                )

                if download_result.get("status") == "downloaded":
                    chroma_client = chromadb.PersistentClient(path=temp_dir)
                    logger.info(
                        "Loaded snapshot from S3",
                        collection=collection_name,
                        version_id=download_result.get("version_id"),
                        initialized=download_result.get("initialized", False),
                    )

                    # Ensure collection exists (should already exist if snapshot was initialized)
                    try:
                        collection = chroma_client.get_collection(collection_name)
                        logger.info(
                            "Collection exists in snapshot",
                            collection=collection_name,
                            count=collection.count(),
                        )
                    except Exception:
                        # Collection doesn't exist, create it
                        collection = chroma_client.create_collection(
                            collection_name,
                            metadata={
                                "created_by": "compaction_handler",
                                "created_at": datetime.now().isoformat(),
                                "collection_type": collection_name,
                            },
                        )
                        logger.info(
                            "Created collection in snapshot",
                            collection=collection_name,
                            count=collection.count(),
                        )
                else:
                    logger.error(
                        "Failed to download or initialize snapshot",
                        error=download_result.get("error"),
                        collection=collection_name,
                    )
                    raise RuntimeError(
                        f"Failed to download snapshot: {download_result.get('error')}"
                    )
            else:
                # Fallback to legacy download method
                try:
                    download_from_s3(bucket, snapshot_key, temp_dir)
                    chroma_client = chromadb.PersistentClient(path=temp_dir)
                    logger.info(
                        "Loaded existing snapshot from S3", snapshot_key=snapshot_key
                    )
                except Exception as e:
                    # No existing snapshot, create new empty snapshot with collection
                    logger.info(
                        "No existing snapshot found, creating new empty snapshot",
                        snapshot_key=snapshot_key,
                        error=str(e),
                    )
                    chroma_client = chromadb.PersistentClient(path=temp_dir)

                    # Create the collection (will be empty)
                    try:
                        collection = chroma_client.get_collection(collection_name)
                        logger.info(
                            "Collection already exists in new snapshot",
                            collection=collection_name,
                        )
                    except Exception:
                        # Collection doesn't exist, create it
                        collection = chroma_client.create_collection(
                            collection_name,
                            metadata={
                                "created_by": "compaction_handler",
                                "created_at": datetime.now().isoformat(),
                                "collection_type": collection_name,
                            },
                        )
                        logger.info(
                            "Created empty collection in new snapshot",
                            collection=collection_name,
                            count=collection.count(),
                        )
        else:
            # Create ChromaDB client from EFS snapshot
            chroma_client = chromadb.PersistentClient(path=temp_dir)

        # Merge intermediate chunks - handle both legacy and new formats
        if chunk_results:
            # New format: we have specific intermediate_key objects
            logger.info(
                "Merging using chunk_results format",
                chunk_count=len(chunk_results),
            )
            logger.info("Chunk results format", chunk_results=chunk_results)

            # Handle different possible formats
            chunk_keys = []
            for chunk in chunk_results:
                if isinstance(chunk, dict) and "intermediate_key" in chunk:
                    chunk_keys.append(chunk["intermediate_key"])
                elif isinstance(chunk, str):
                    # Direct string key
                    chunk_keys.append(chunk)
                else:
                    logger.error(
                        "Unexpected chunk format",
                        chunk=chunk,
                        chunk_type=type(chunk),
                    )
                    raise ValueError(f"Unexpected chunk format: {chunk}")

            logger.info("Extracted chunk keys", chunk_keys=chunk_keys)
        else:
            # Legacy format: generate keys from batch_id and chunk indices
            logger.info(
                "Merging using legacy total_chunks format",
                total_chunks=total_chunks,
            )
            chunk_keys = [
                f"intermediate/{batch_id}/chunk-{i}/"
                for i in range(total_chunks)
            ]

        for i, intermediate_key in enumerate(chunk_keys):
            logger.info(
                "Processing chunk",
                current_chunk=i + 1,
                total_chunks=len(chunk_keys),
                intermediate_key=intermediate_key,
            )
            chunk_temp = tempfile.mkdtemp()

            try:
                # Download intermediate chunk
                download_from_s3(bucket, intermediate_key, chunk_temp)
                logger.info(
                    "Downloaded chunk",
                    chunk_index=i,
                    intermediate_key=intermediate_key,
                )

                # Load chunk
                chunk_client = chromadb.PersistentClient(path=chunk_temp)

                # Merge all collections from chunk
                for collection_meta in chunk_client.list_collections():
                    chunk_collection = chunk_client.get_collection(
                        collection_meta.name
                    )

                    # Get or create collection in main snapshot
                    try:
                        main_collection = chroma_client.get_collection(
                            collection_meta.name
                        )
                    except Exception:
                        main_collection = chroma_client.create_collection(
                            collection_meta.name
                        )

                    # Process embeddings in batches to reduce memory usage
                    batch_size = 1000  # Process 1000 embeddings at a time
                    chunk_count = chunk_collection.count()

                    if chunk_count > 0:
                        # For large collections, process in batches
                        if chunk_count > batch_size:
                            # Get all IDs first (lightweight)
                            all_ids = chunk_collection.get(include=[])["ids"]

                            for i in range(0, len(all_ids), batch_size):
                                batch_ids = all_ids[i : i + batch_size]
                                results = chunk_collection.get(
                                    ids=batch_ids,
                                    include=[
                                        "embeddings",
                                        "documents",
                                        "metadatas",
                                    ],
                                )

                                main_collection.upsert(
                                    ids=results["ids"],
                                    embeddings=results["embeddings"],
                                    documents=results["documents"],
                                    metadatas=results["metadatas"],
                                )
                                total_embeddings += len(results["ids"])
                        else:
                            # Small collection, process all at once
                            results = chunk_collection.get(
                                include=[
                                    "embeddings",
                                    "documents",
                                    "metadatas",
                                ]
                            )

                            if results["ids"]:
                                main_collection.upsert(
                                    ids=results["ids"],
                                    embeddings=results["embeddings"],
                                    documents=results["documents"],
                                    metadatas=results["metadatas"],
                                )
                                total_embeddings += len(results["ids"])

            finally:
                # CRITICAL: Close chunk client before cleanup to ensure SQLite files are flushed
                try:
                    if 'chunk_client' in locals() and chunk_client is not None:
                        close_chromadb_client(chunk_client, collection_name="final_merge_chunk")
                        chunk_client = None
                except Exception:
                    pass
                shutil.rmtree(chunk_temp, ignore_errors=True)

        # CRITICAL: Close ChromaDB client BEFORE uploading to ensure SQLite files are flushed and unlocked
        close_chromadb_client(chroma_client, collection_name="final_merge")
        chroma_client = None

        # Use atomic upload pattern if available, fallback to legacy method
        if ATOMIC_UPLOAD_AVAILABLE:
            # Determine collection name for atomic upload
            collection_name = (
                database_name or "words"
            )  # Default to words for backward compatibility

            atomic_result = upload_snapshot_atomic(
                local_path=temp_dir,
                bucket=bucket,
                collection=collection_name,
                lock_manager=lock_manager,  # Pass lock manager for CAS validation (consistent with compactor)
                metadata={
                    "batch_id": batch_id,
                    "total_embeddings": str(total_embeddings),
                    "merge_operation": "final_merge",
                    "database": database_name or "unknown",
                },
                keep_versions=4,
            )
            logger.info(
                "Uploaded snapshot using atomic pattern",
                version_id=atomic_result.get("version_id"),
                collection=collection_name,
                total_embeddings=total_embeddings,
            )

            # Update EFS cache if EFS was used
            if use_efs and efs_manager and atomic_result.get("version_id"):
                try:
                    new_version = atomic_result.get("version_id")
                    if new_version:
                        # Copy updated snapshot back to EFS
                        new_efs_snapshot_path = os.path.join(
                            efs_manager.efs_snapshots_dir, new_version
                        )
                        if os.path.exists(new_efs_snapshot_path):
                            shutil.rmtree(new_efs_snapshot_path)
                        os.makedirs(os.path.dirname(new_efs_snapshot_path), exist_ok=True)
                        shutil.copytree(temp_dir, new_efs_snapshot_path)

                        # Update EFS version file
                        efs_manager.set_efs_version(new_version)

                        logger.info(
                            "Updated EFS snapshot cache",
                            collection=collection_name,
                            version=new_version,
                            efs_path=new_efs_snapshot_path,
                        )

                        # Clean up old EFS snapshots
                        try:
                            efs_manager.cleanup_old_snapshots()
                        except Exception as cleanup_error:
                            logger.warning(
                                "Failed to cleanup old EFS snapshots (non-critical)",
                                error=str(cleanup_error)
                            )
                except Exception as efs_error:
                    logger.warning(
                        "Failed to update EFS cache (non-critical)",
                        error=str(efs_error),
                        collection=collection_name,
                    )

            return {
                "snapshot_key": atomic_result.get("versioned_key"),
                "total_embeddings": total_embeddings,
                "processing_time": time.time() - start_time,
                "atomic_upload": True,
                "version_id": atomic_result.get("version_id"),
                "used_efs": use_efs,
            }
        else:
            # Fallback to legacy non-atomic upload
            logger.warning("Atomic upload not available, using legacy method")

            # Create timestamped snapshot with dedicated prefix for lifecycle management
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

            # Use database-specific path if provided
            if database_name:
                timestamped_key = (
                    f"{database_name}/snapshot/timestamped/{timestamp}/"
                )
            else:
                # Backward compatibility
                timestamped_key = f"snapshot/timestamped/{timestamp}/"

            # Upload timestamped snapshot with hash
            timestamped_result = upload_to_s3(
                temp_dir,
                bucket,
                timestamped_key,
                calculate_hash=True,
                metadata={
                    "batch_id": batch_id,
                    "total_embeddings": str(total_embeddings),
                    "merge_operation": "final_merge",
                    "database": database_name or "unknown",
                },
            )
            logger.info(
                "Uploaded timestamped snapshot (legacy)",
                snapshot_key=timestamped_key,
                hash=timestamped_result.get("hash", "not_calculated"),
            )

            # Update latest pointer with hash
            latest_result = upload_to_s3(
                temp_dir,
                bucket,
                snapshot_key,
                calculate_hash=True,
                metadata={
                    "batch_id": batch_id,
                    "total_embeddings": str(total_embeddings),
                    "merge_operation": "final_merge",
                    "database": database_name or "unknown",
                    "pointer_to": timestamped_key,
                },
            )
            logger.info(
                "Updated latest snapshot pointer (legacy)",
                snapshot_key=snapshot_key,
                hash=latest_result.get("hash", "not_calculated"),
            )

            # Legacy retention: keep only the latest 4 timestamped versions
            try:
                base_prefix = (
                    f"{database_name}/snapshot/timestamped/"
                    if database_name
                    else "snapshot/timestamped/"
                )

                # List all version directories
                paginator = s3_client.get_paginator("list_objects_v2")
                pages = paginator.paginate(
                    Bucket=bucket, Prefix=base_prefix, Delimiter="/"
                )
                version_dirs = []
                for page in pages:
                    for cp in page.get("CommonPrefixes", []):
                        prefix = cp.get("Prefix")
                        if prefix and prefix.startswith(base_prefix):
                            # Extract version id from .../timestamped/<version>/
                            parts = prefix.rstrip("/").split("/")
                            if parts:
                                version_id = parts[-1]
                                version_dirs.append((version_id, prefix))

                # Sort by version_id (timestamps sort lexicographically)
                version_dirs.sort(key=lambda x: x[0], reverse=True)
                for _, old_prefix in version_dirs[4:]:
                    # Delete all objects under old_prefix
                    try:
                        del_pages = paginator.paginate(
                            Bucket=bucket, Prefix=old_prefix
                        )
                        to_delete = []
                        for del_page in del_pages:
                            for obj in del_page.get("Contents", []):
                                to_delete.append({"Key": obj["Key"]})
                            if to_delete:
                                s3_client.delete_objects(
                                    Bucket=bucket,
                                    Delete={"Objects": to_delete},
                                )
                                to_delete = []
                        logger.info(
                            "Deleted old timestamped snapshot",
                            prefix=old_prefix,
                        )
                    except Exception as cleanup_err:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            "Failed to delete old timestamped snapshot",
                            prefix=old_prefix,
                            error=str(cleanup_err),
                        )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Legacy retention cleanup failed", error=str(e)
                )

            processing_time = time.time() - start_time
            logger.info(
                "Final merge completed (legacy)",
                total_embeddings=total_embeddings,
                processing_time_seconds=processing_time,
            )

            return {
                "snapshot_key": timestamped_key,
                "total_embeddings": total_embeddings,
                "processing_time": processing_time,
                "atomic_upload": False,
            }

    finally:
        # Clean up temporary directories
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if local_snapshot_path and local_snapshot_path != temp_dir:
            shutil.rmtree(local_snapshot_path, ignore_errors=True)


def cleanup_intermediate_chunks(batch_id: str, total_chunks: int):
    """Clean up intermediate chunk files from S3."""
    bucket = os.environ["CHROMADB_BUCKET"]

    for chunk_index in range(total_chunks):
        intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
        try:
            # List and delete all objects with this prefix
            response = s3_client.list_objects_v2(
                Bucket=bucket, Prefix=intermediate_key
            )

            if "Contents" in response:
                objects = [{"Key": obj["Key"]} for obj in response["Contents"]]
                s3_client.delete_objects(
                    Bucket=bucket, Delete={"Objects": objects}  # type: ignore
                )
                logger.info(
                    "Deleted intermediate chunk", chunk_index=chunk_index
                )
        except Exception as e:
            logger.warning(
                "Failed to delete chunk", chunk_index=chunk_index, error=str(e)
            )


def cleanup_intermediate_chunks_by_keys(intermediate_keys: List[str]):
    """Clean up specific intermediate chunk files from S3 by their keys."""
    bucket = os.environ["CHROMADB_BUCKET"]

    for intermediate_key in intermediate_keys:
        try:
            # List and delete all objects with this prefix
            response = s3_client.list_objects_v2(
                Bucket=bucket, Prefix=intermediate_key
            )

            if "Contents" in response:
                objects = [{"Key": obj["Key"]} for obj in response["Contents"]]
                s3_client.delete_objects(
                    Bucket=bucket, Delete={"Objects": objects}  # type: ignore
                )
                logger.info(
                    "Deleted intermediate chunk by key",
                    intermediate_key=intermediate_key,
                )
        except Exception as e:
            logger.warning(
                "Failed to delete intermediate chunk",
                intermediate_key=intermediate_key,
                error=str(e),
            )


def download_from_s3(bucket: str, prefix: str, local_path: str):
    """Download all objects with a given prefix from S3."""
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    for page in pages:
        if "Contents" not in page:
            continue

        for obj in page["Contents"]:
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path:
                continue

            local_file = os.path.join(local_path, relative_path)
            os.makedirs(os.path.dirname(local_file), exist_ok=True)

            s3_client.download_file(bucket, key, local_file)


def upload_to_s3(
    local_path: str,
    bucket: str,
    prefix: str,
    calculate_hash: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Upload a directory to S3 with optional hash calculation.

    Uses enhanced upload_snapshot_with_hash when available, falls back to legacy upload.
    """
    # Use hash-enabled upload for snapshot directories (latest/, timestamped/)
    use_hash_upload = (
        HASH_UPLOAD_AVAILABLE
        and calculate_hash
        and ("snapshot/" in prefix or "latest/" in prefix)
    )

    if use_hash_upload:
        logger.info("Using hash-enabled upload for", prefix=prefix)

        # Ensure prefix ends with / for snapshot key format
        snapshot_key = prefix.rstrip("/") + "/"

        result = upload_snapshot_with_hash(
            local_snapshot_path=local_path,
            bucket=bucket,
            snapshot_key=snapshot_key,
            calculate_hash=True,
            metadata=metadata or {},
        )

        if result["status"] == "uploaded":
            logger.info(
                "Uploaded snapshot with hash",
                prefix=prefix,
                file_count=result.get("file_count", 0),
                hash=result.get("hash", "not_calculated"),
            )

        return result
    else:
        # Legacy upload (for intermediate chunks and when hash utils not available)
        logger.info("Using legacy upload for", prefix=prefix)

        file_count = 0
        total_size = 0

        for root, _, files in os.walk(local_path):
            for file in files:
                local_file = os.path.join(root, file)
                relative_path = os.path.relpath(local_file, local_path)
                s3_key = os.path.join(prefix, relative_path)

                s3_client.upload_file(local_file, bucket, s3_key)

                file_count += 1
                total_size += os.path.getsize(local_file)

        return {
            "status": "uploaded",
            "file_count": file_count,
            "total_size_bytes": total_size,
            "snapshot_key": prefix,
            "hash": None,  # No hash calculated for legacy uploads
        }
