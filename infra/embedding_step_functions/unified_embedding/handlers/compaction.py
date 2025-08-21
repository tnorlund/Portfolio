"""ChromaDB compaction handler with chunked processing.

This handler efficiently compacts multiple ChromaDB deltas created during
parallel embedding processing, with support for collection-aware processing.
"""

from typing import Optional

import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List
import utils.logging

import boto3
import chromadb

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.lock_manager import LockManager

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

try:
    from receipt_label.utils.chroma_s3_helpers import upload_snapshot_with_hash

    HASH_UPLOAD_AVAILABLE = True
except ImportError:
    HASH_UPLOAD_AVAILABLE = False
    logger.warning("Hash-enabled upload not available, using legacy upload")

# Initialize clients
s3_client = boto3.client("s3")
dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

# Get configuration from environment
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "5"))


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
    if operation == "final_merge":
        return final_merge_handler(event)

    logger.error(
        "Invalid operation. Expected 'process_chunk' or 'final_merge'",
        operation=operation,
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
    database_name = event.get("database")  # Track database for this chunk

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

        # Prepare response for Map state
        # No need for continuation logic since all chunks process in parallel
        response = {
            "statusCode": 200,
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "intermediate_key": chunk_result["intermediate_key"],
            "embeddings_processed": chunk_result["embeddings_processed"],
            "processing_time_seconds": chunk_result["processing_time"],
            "message": "Chunk processed successfully",
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
    logger.info("Starting final merge compaction")

    batch_id = event.get("batch_id")
    total_chunks = event.get("total_chunks", 1)
    database_name = event.get(
        "database", "lines"
    )  # Get database from event with default

    if not batch_id:
        return {
            "statusCode": 400,
            "error": "batch_id is required for final merge",
        }

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
        merge_result = perform_final_merge(
            batch_id, total_chunks, database_name
        )

        # Clean up intermediate chunks
        cleanup_intermediate_chunks(batch_id, total_chunks)

        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "snapshot_key": merge_result["snapshot_key"],
            "total_embeddings": merge_result["total_embeddings"],
            "processing_time_seconds": merge_result["processing_time"],
            "message": "Final merge completed successfully",
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
            for i, delta in enumerate(collection_deltas):
                delta_key = delta["delta_key"]

                # Download and merge delta
                embeddings_added = download_and_merge_delta(
                    bucket, delta_key, collection, temp_dir
                )
                total_embeddings += embeddings_added
                logger.info(
                    "Merged embeddings from delta into collection",
                    embeddings_added=embeddings_added,
                    delta_key=delta_key,
                    collection_name=collection_name,
                    current=i + 1,
                    total=len(collection_deltas),
                )

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

    try:
        # Download delta from S3
        download_from_s3(bucket, delta_key, delta_temp)

        # Load delta into temporary ChromaDB instance
        delta_client = chromadb.PersistentClient(path=delta_temp)

        # Get the first (and typically only) collection from the delta
        # The delta should contain exactly one collection with all embeddings
        delta_collections = delta_client.list_collections()
        if not delta_collections:
            logger.warning(
                "No collections found in delta", delta_key=delta_key
            )
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
        return total_processed

    finally:
        # Clean up delta temp directory
        shutil.rmtree(delta_temp, ignore_errors=True)


def perform_final_merge(
    batch_id: str, total_chunks: int, database_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Perform the final merge of all intermediate chunks into a snapshot.

    Args:
        batch_id: Unique identifier for this batch
        total_chunks: Number of chunks to merge
        database_name: Database name ('lines' or 'words') for separate snapshots
    """
    start_time = time.time()
    bucket = os.environ["CHROMADB_BUCKET"]
    temp_dir = tempfile.mkdtemp()
    total_embeddings = 0

    # Determine snapshot paths based on database
    if database_name:
        snapshot_key = f"{database_name}/snapshot/latest/"
        logger.info(
            "Using database-specific snapshot path", snapshot_key=snapshot_key
        )
    else:
        # Backward compatibility - unified snapshot
        snapshot_key = "snapshot/latest/"
        logger.info("Using unified snapshot path for backward compatibility")

    try:
        # Download current snapshot if exists
        try:
            download_from_s3(bucket, snapshot_key, temp_dir)
            chroma_client = chromadb.PersistentClient(path=temp_dir)
            logger.info(
                "Loaded existing snapshot from S3", snapshot_key=snapshot_key
            )
        except Exception:
            # No existing snapshot, create new
            chroma_client = chromadb.PersistentClient(path=temp_dir)
            logger.info("Creating new snapshot at", snapshot_key=snapshot_key)

        # Merge all intermediate chunks
        for chunk_index in range(total_chunks):
            logger.info(
                "Processing chunk",
                current_chunk=chunk_index + 1,
                total_chunks=total_chunks,
            )
            intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
            chunk_temp = tempfile.mkdtemp()

            try:
                # Download intermediate chunk
                download_from_s3(bucket, intermediate_key, chunk_temp)
                logger.info("Downloaded chunk", chunk_index=chunk_index)

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
                shutil.rmtree(chunk_temp, ignore_errors=True)

        # Create timestamped snapshot with dedicated prefix for
        # lifecycle management
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
            "Uploaded timestamped snapshot",
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
            "Updated latest snapshot pointer",
            snapshot_key=snapshot_key,
            hash=latest_result.get("hash", "not_calculated"),
        )

        processing_time = time.time() - start_time
        logger.info(
            "Final merge completed",
            total_embeddings=total_embeddings,
            processing_time_seconds=processing_time,
        )

        return {
            "snapshot_key": timestamped_key,
            "total_embeddings": total_embeddings,
            "processing_time": processing_time,
        }

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


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
