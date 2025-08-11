"""
Containerized Lambda handler for compacting multiple ChromaDB deltas.

This handler is called at the end of the step function to compact all deltas
created during parallel embedding processing.
"""

import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict, List

import boto3
import chromadb

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.utils.lock_manager import LockManager

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

# Initialize clients
s3_client = boto3.client("s3")
dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

# Get configuration from environment
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "5"))


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
    logger.info("Event: %s", json.dumps(event))

    # Determine operation mode
    operation = event.get("operation")

    if operation == "process_chunk":
        return process_chunk_handler(event)
    if operation == "final_merge":
        return final_merge_handler(event)

    logger.error(
        "Invalid operation: %s. Expected 'process_chunk' or 'final_merge'",
        operation,
    )
    return {
        "statusCode": 400,
        "error": f"Invalid operation: {operation}",
        "message": "Operation must be 'process_chunk' or 'final_merge'",
    }


def process_chunk_handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a chunk of deltas without acquiring locks.

    Writes output to intermediate/{batch_id}/chunk-{index}/ in S3.
    """
    logger.info("Processing chunk compaction")

    batch_id = event.get("batch_id")
    chunk_index = event.get("chunk_index")
    delta_results = event.get("delta_results", [])

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

    if not delta_results:
        logger.info("No delta results in chunk %d, skipping", chunk_index)
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "embeddings_processed": 0,
            "message": "Empty chunk processed",
        }

    # Limit chunk size to 10 deltas as required
    chunk_deltas = delta_results[:10]
    remaining_deltas = delta_results[10:]

    # Group chunk deltas by collection name for collection-aware processing
    deltas_by_collection = {}
    for result in chunk_deltas:
        collection = result.get("collection", "receipt_words")  # Default for backward compat
        if collection not in deltas_by_collection:
            deltas_by_collection[collection] = []
        deltas_by_collection[collection].append(result)

    logger.info(
        "Processing chunk %d with %d deltas across %d collections (batch_id: %s)",
        chunk_index,
        len(chunk_deltas),
        len(deltas_by_collection),
        batch_id,
    )

    try:
        # Process chunk deltas with collection awareness
        chunk_result = process_chunk_deltas(
            batch_id, chunk_index, chunk_deltas, deltas_by_collection
        )

        # Prepare response
        response = {
            "statusCode": 200,
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "intermediate_key": chunk_result["intermediate_key"],
            "embeddings_processed": chunk_result["embeddings_processed"],
            "processing_time_seconds": chunk_result["processing_time"],
            "message": "Chunk processed successfully",
        }

        # Add continuation data if there are remaining deltas
        if remaining_deltas:
            response["next_chunk_index"] = chunk_index + 1
            response["remaining_deltas"] = remaining_deltas
            response["has_more_chunks"] = True
        else:
            response["has_more_chunks"] = False

        logger.info("Chunk %d processing completed: %s", chunk_index, response)
        return response

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Chunk %d processing failed: %s", chunk_index, str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "message": "Chunk processing failed",
        }


def final_merge_handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Final merge step that acquires lock and combines intermediate chunks.

    Preserves existing heartbeat support for the final merge operation.
    """
    logger.info("Starting final merge operation")

    batch_id = event.get("batch_id")
    total_chunks = event.get("total_chunks")

    if not batch_id:
        return {
            "statusCode": 400,
            "error": "batch_id is required for final merge",
        }

    if total_chunks is None:
        return {
            "statusCode": 400,
            "error": "total_chunks is required for final merge",
        }

    logger.info(
        "Final merge for batch %s with %d chunks", batch_id, total_chunks
    )

    # Create a fresh lock manager for this invocation
    lock_manager = LockManager(
        dynamo_client=dynamo_client,
        heartbeat_interval=heartbeat_interval,
        lock_duration_minutes=lock_duration_minutes,
    )

    try:
        # Acquire compaction lock for final merge
        if not lock_manager.acquire():
            logger.warning(
                "Could not acquire compaction lock for final merge - "
                "another compaction is in progress"
            )
            return {
                "statusCode": 423,  # Locked
                "message": "Final merge blocked - compaction already in progress",
            }

        # Start heartbeat thread to keep lock alive during processing
        lock_manager.start_heartbeat()

        # Perform final merge
        merge_result = merge_intermediate_chunks(batch_id, total_chunks)

        # Stop heartbeat thread and release lock
        lock_manager.stop_heartbeat()
        lock_manager.release()

        logger.info("Final merge completed successfully: %s", merge_result)

        return {
            "statusCode": 200,
            "compaction_method": "chunked",
            "batch_id": batch_id,
            "chunks_merged": total_chunks,
            "total_embeddings": merge_result["total_embeddings"],
            "snapshot_keys": merge_result["snapshot_keys"],  # Multiple keys for multiple collections
            "collections_compacted": merge_result["collections"],
            "processing_time_seconds": merge_result["processing_time"],
            "message": "Final merge completed successfully",
>>>>>>> origin/chroma_db
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Final merge failed: %s", str(e))
        # Stop heartbeat thread and try to release lock on error
        lock_manager.stop_heartbeat()
        lock_manager.release()

        return {
            "statusCode": 500,
            "error": str(e),
            "batch_id": batch_id,
            "message": "Final merge failed",
        }


def _process_single_delta(
    delta_key: str,
    temp_dir: str,
    bucket_name: str,
    chunk_collection: Any,
    collection_name: str = "receipt_words",
) -> int:
    """Process a single delta and add to chunk collection."""
    logger.info("Processing delta: %s for collection: %s", delta_key, collection_name)

    # Download delta to temporary directory
    delta_dir = os.path.join(
        temp_dir, "delta", os.path.basename(delta_key.rstrip("/"))
    )
    os.makedirs(delta_dir, exist_ok=True)

    # List and download all files in delta
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket_name, Prefix=delta_key)

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            local_path = os.path.join(
                delta_dir, os.path.relpath(key, delta_key)
            )
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3_client.download_file(bucket_name, key, local_path)

    # Load delta ChromaDB
    delta_client = chromadb.PersistentClient(path=delta_dir)
    try:
        # Try to get the collection with the expected name
        delta_collection = delta_client.get_collection(collection_name)
    except ValueError as e:
        # If collection doesn't exist, log warning and return 0
        logger.warning(
            "Delta %s does not contain collection %s, skipping: %s",
            delta_key, collection_name, str(e)
        )
        return 0

    # Get all data from delta
    delta_data = delta_collection.get(
        include=["embeddings", "documents", "metadatas"]
    )

    if delta_data["ids"]:
        # Add to chunk collection
        embeddings_count = len(delta_data["ids"])
        chunk_collection.add(
            ids=delta_data["ids"],
            embeddings=delta_data["embeddings"],
            documents=delta_data["documents"],
            metadatas=delta_data["metadatas"],
        )
        return embeddings_count
    return 0


def _upload_chunk_to_s3(
    temp_dir: str,
    batch_id: str,
    chunk_index: int,
    bucket_name: str,
    collections_processed: List[str],
) -> Dict[str, str]:
    """Upload chunk to S3 and return the intermediate keys for each collection."""
    intermediate_keys = {}
    
    for collection_name in collections_processed:
        intermediate_key = f"intermediate/{batch_id}/{collection_name}/chunk-{chunk_index}/"
        chunk_dir = os.path.join(temp_dir, "chunk", collection_name)
        
        if not os.path.exists(chunk_dir):
            logger.warning("Chunk directory not found for collection %s, skipping", collection_name)
            continue

        for root, _, files in os.walk(chunk_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, chunk_dir)
                s3_key = intermediate_key + relative_path
                s3_client.upload_file(local_path, bucket_name, s3_key)

        logger.info(
            "Uploaded chunk %d for collection %s to s3://%s/%s",
            chunk_index,
            collection_name,
            bucket_name,
            intermediate_key,
        )
        intermediate_keys[collection_name] = intermediate_key
    
    return intermediate_keys


def process_chunk_deltas(
    batch_id: str, 
    chunk_index: int, 
    delta_results: List[Dict[str, Any]],
    deltas_by_collection: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """
    Process a chunk of deltas and write to intermediate S3 location.

    This function does not acquire locks and processes deltas independently.
    Supports collection-aware processing by grouping deltas by collection.
    """
    start_time = time.time()
    bucket_name = os.environ["CHROMADB_BUCKET"]
    total_embeddings_processed = 0

    # Extract delta keys from results
    delta_keys = [result["delta_key"] for result in delta_results]

    with tempfile.TemporaryDirectory() as temp_dir:
        collections_processed = []
        intermediate_keys = {}
        
        # Process each collection separately
        for collection_name, collection_deltas in deltas_by_collection.items():
            # Create ChromaDB instance for this collection chunk
            chunk_path = os.path.join(temp_dir, "chunk", collection_name)
            chunk_client = chromadb.PersistentClient(path=chunk_path)
            chunk_collection = chunk_client.get_or_create_collection(
                name=collection_name,
                metadata={
                    "created_at": datetime.utcnow().isoformat(),
                    "batch_id": batch_id,
                    "chunk_index": chunk_index,
                    "collection_type": collection_name,
                },
            )

            # Process each delta for this collection
            collection_embeddings = 0
            for delta_result in collection_deltas:
                delta_key = delta_result["delta_key"]
                embeddings_count = _process_single_delta(
                    delta_key, temp_dir, bucket_name, chunk_collection, collection_name
                )
                collection_embeddings += embeddings_count
                total_embeddings_processed += embeddings_count
                if embeddings_count > 0:
                    logger.info(
                        "Added %d embeddings from delta to chunk %d for collection %s "
                        "(collection total: %d, chunk total: %d)",
                        embeddings_count,
                        chunk_index,
                        collection_name,
                        collection_embeddings,
                        total_embeddings_processed,
                    )
            
            collections_processed.append(collection_name)

        # Upload chunks to intermediate S3 location (one per collection)
        intermediate_keys = _upload_chunk_to_s3(
            temp_dir, batch_id, chunk_index, bucket_name, collections_processed
        )

    elapsed_time = time.time() - start_time
    logger.info(
        "Chunk %d processing completed in %.2f seconds with %d embeddings",
        chunk_index,
        elapsed_time,
        total_embeddings_processed,
    )

    return {
        "intermediate_keys": intermediate_keys,
        "collections_processed": collections_processed,
        "embeddings_processed": total_embeddings_processed,
        "deltas_processed": len(delta_results),
        "processing_time": elapsed_time,
    }


def _process_intermediate_chunk(
    chunk_index: int,
    batch_id: str,
    temp_dir: str,
    bucket_name: str,
    main_collections: Dict[str, Any],
) -> Dict[str, int]:
    """Process a single intermediate chunk and merge into main collections."""
    embeddings_by_collection = {}
    
    # Process each collection's chunk
    for collection_name, main_collection in main_collections.items():
        intermediate_key = f"intermediate/{batch_id}/{collection_name}/chunk-{chunk_index}/"
        logger.info("Merging intermediate chunk for collection %s: %s", collection_name, intermediate_key)

        # Download chunk to temporary directory
        chunk_dir = os.path.join(temp_dir, "chunks", collection_name, f"chunk-{chunk_index}")
        os.makedirs(chunk_dir, exist_ok=True)

        # List and download all files in chunk
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket_name, Prefix=intermediate_key)

        chunk_has_data = False
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local_path = os.path.join(
                    chunk_dir, os.path.relpath(key, intermediate_key)
                )
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                s3_client.download_file(bucket_name, key, local_path)
                chunk_has_data = True

        if not chunk_has_data:
            logger.warning("No data found for chunk %d in collection %s, skipping", chunk_index, collection_name)
            embeddings_by_collection[collection_name] = 0
            continue

        # Load chunk ChromaDB
        chunk_client = chromadb.PersistentClient(path=chunk_dir)
        try:
            chunk_collection = chunk_client.get_collection(collection_name)
        except ValueError:
            logger.warning("Collection %s not found in chunk %d, skipping", collection_name, chunk_index)
            embeddings_by_collection[collection_name] = 0
            continue

        # Get all data from chunk
        chunk_data = chunk_collection.get(
            include=["embeddings", "documents", "metadatas"]
        )

        if chunk_data["ids"]:
            # Add to main collection
            embeddings_count = len(chunk_data["ids"])
            main_collection.add(
                ids=chunk_data["ids"],
                embeddings=chunk_data["embeddings"],
                documents=chunk_data["documents"],
                metadatas=chunk_data["metadatas"],
            )
            embeddings_by_collection[collection_name] = embeddings_count
        else:
            embeddings_by_collection[collection_name] = 0
    
    return embeddings_by_collection


def _cleanup_intermediate_chunks(
    batch_id: str, total_chunks: int, bucket_name: str, collections: List[str]
) -> None:
    """Clean up intermediate chunks after merge."""
    if os.environ.get("DELETE_INTERMEDIATE_CHUNKS", "true").lower() != "true":
        return

    for collection_name in collections:
        for chunk_index in range(total_chunks):
            intermediate_key = f"intermediate/{batch_id}/{collection_name}/chunk-{chunk_index}/"
            # Delete all objects with this prefix
            paginator = s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket_name, Prefix=intermediate_key)

            for page in pages:
                if "Contents" in page:
                    objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                    if objects:  # Only delete if objects exist
                        # Fix for mypy typeddict issue
                        delete_batch = {"Objects": objects}
                        s3_client.delete_objects(
                            Bucket=bucket_name, Delete=delete_batch
                        )
            logger.info("Cleaned up intermediate chunk for collection %s: %s", collection_name, intermediate_key)


def merge_intermediate_chunks(
    batch_id: str, total_chunks: int
) -> Dict[str, Any]:
    """
    Merge all intermediate chunks into final ChromaDB snapshots.

    This function is called during the final merge step with lock protection.
    Supports multiple collections.
    """
    start_time = time.time()
    bucket_name = os.environ["CHROMADB_BUCKET"]
    total_embeddings_processed = 0
    snapshot_keys = {}
    collections_found = set()
    
    # First, discover which collections are present in the chunks
    for chunk_index in range(total_chunks):
        # Check for both receipt_words and receipt_lines collections
        for collection_name in ["receipt_words", "receipt_lines"]:
            intermediate_prefix = f"intermediate/{batch_id}/{collection_name}/chunk-{chunk_index}/"
            response = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=intermediate_prefix,
                MaxKeys=1
            )
            if response.get("Contents"):
                collections_found.add(collection_name)
    
    logger.info("Found collections to merge: %s", list(collections_found))

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create main ChromaDB instances for each collection found
        main_collections = {}
        for collection_name in collections_found:
            snapshot_id = str(uuid.uuid4())
            collection_path = os.path.join(temp_dir, collection_name)
            main_client = chromadb.PersistentClient(path=collection_path)
            main_collection = main_client.get_or_create_collection(
                name=collection_name,
                metadata={
                    "created_at": datetime.utcnow().isoformat(),
                    "batch_id": batch_id,
                    "snapshot_id": snapshot_id,
                    "collection_type": collection_name,
                },
            )
            main_collections[collection_name] = main_collection
            snapshot_keys[collection_name] = f"snapshot/{collection_name}/{snapshot_id}/"

        # Process each intermediate chunk for all collections
        for chunk_index in range(total_chunks):
            embeddings_by_collection = _process_intermediate_chunk(
                chunk_index, batch_id, temp_dir, bucket_name, main_collections
            )
            for collection_name, embeddings_count in embeddings_by_collection.items():
                total_embeddings_processed += embeddings_count
                if embeddings_count > 0:
                    logger.info(
                        "Merged %d embeddings from chunk %d for collection %s (total: %d)",
                        embeddings_count,
                        chunk_index,
                        collection_name,
                        total_embeddings_processed,
                    )

        # Upload final snapshots to S3 (one per collection)
        for collection_name in collections_found:
            snapshot_key = snapshot_keys[collection_name]
            collection_path = os.path.join(temp_dir, collection_name)

            for root, _, files in os.walk(collection_path):
                for file in files:
                    local_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_path, collection_path)
                    s3_key = snapshot_key + relative_path
                    s3_client.upload_file(local_path, bucket_name, s3_key)

            logger.info(
                "Uploaded %s snapshot to s3://%s/%s",
                collection_name, bucket_name, snapshot_key
            )

        # Clean up intermediate chunks for all collections
        _cleanup_intermediate_chunks(batch_id, total_chunks, bucket_name, list(collections_found))

    elapsed_time = time.time() - start_time
    logger.info(
        "Final merge completed in %.2f seconds. "
        "Merged %d chunks with %d total embeddings",
        elapsed_time,
        total_chunks,
        total_embeddings_processed,
    )

    return {
        "snapshot_keys": snapshot_keys,
        "collections": list(collections_found),
        "chunks_merged": total_chunks,
        "total_embeddings": total_embeddings_processed,
        "processing_time": elapsed_time,
    }


# LockManager from receipt_label.utils.lock_manager
