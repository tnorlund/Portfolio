"""
Containerized Lambda handler for compacting multiple ChromaDB deltas.

This handler is called at the end of the step function to compact all deltas
created during parallel embedding processing.
"""

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from logging import INFO, Formatter, StreamHandler, getLogger

import boto3
import chromadb

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.compaction_lock import CompactionLock

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


def compact_handler(event, context):  # pylint: disable=unused-argument
    """
    Compact multiple delta files into ChromaDB.

    This handler is designed to be called at the end of a step function
    after all parallel embedding tasks have completed.

    Input event format:
    {
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
    """
    logger.info("Starting batch delta compaction")
    logger.info("Event: %s", json.dumps(event))

    delta_results = event.get("delta_results", [])

    if not delta_results:
        logger.warning("No delta results provided for compaction")
        return {"statusCode": 200, "message": "No deltas to compact"}

    # Group deltas by collection name
    deltas_by_collection = {}
    for result in delta_results:
        collection = result.get("collection", "receipt_words")  # Default for backward compat
        if collection not in deltas_by_collection:
            deltas_by_collection[collection] = []
        deltas_by_collection[collection].append(result)

    total_embeddings = sum(
        result.get("embedding_count", 0) for result in delta_results
    )

    logger.info(
        "Compacting %d deltas with %d total embeddings across %d collections",
        len(delta_results),
        total_embeddings,
        len(deltas_by_collection)
    )

    try:
        # Acquire compaction lock
        lock_acquired = acquire_compaction_lock()
        if not lock_acquired:
            logger.warning(
                "Could not acquire compaction lock - "
                "another compaction is in progress"
            )
            return {
                "statusCode": 423,  # Locked
                "message": "Compaction already in progress",
            }

        # Perform compaction for each collection
        compaction_results = {}
        for collection_name, collection_deltas in deltas_by_collection.items():
            logger.info(
                "Compacting %d deltas for collection: %s",
                len(collection_deltas),
                collection_name
            )
            delta_keys = [d["delta_key"] for d in collection_deltas]
            compaction_results[collection_name] = compact_deltas(
                delta_keys, collection_name
            )

        # Release lock
        release_compaction_lock()

        logger.info(
            "Compaction completed successfully for %d collections",
            len(compaction_results)
        )

        return {
            "statusCode": 200,
            "compaction_method": "batch",
            "collections_compacted": list(compaction_results.keys()),
            "compaction_results": compaction_results,
            "delta_count": len(delta_results),
            "total_embeddings": total_embeddings,
            "message": "Compaction completed successfully",
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Compaction failed: %s", str(e))
        # Try to release lock on error
        try:
            release_compaction_lock()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Compaction failed",
        }


def acquire_compaction_lock() -> bool:
    """Acquire a distributed lock using DynamoDB via receipt_dynamo."""
    lock_id = "chromadb_compaction_lock"
    try:
        # Create a new lock using the CompactionLock entity
        lock = CompactionLock(
            lock_id=lock_id,
            owner=str(uuid.uuid4()),  # Use UUID for lock holder
            expires=datetime.utcnow() + timedelta(minutes=15),  # 15 minute TTL
        )
        # Try to acquire the lock
        success = dynamo_client.acquire_compaction_lock(lock)
        if success:
            logger.info("Acquired compaction lock: %s", lock_id)
        else:
            logger.info("Failed to acquire compaction lock: %s", lock_id)
        return success
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error acquiring lock: %s", str(e))
        return False


def release_compaction_lock():
    """Release the distributed lock."""
    lock_id = "chromadb_compaction_lock"
    try:
        dynamo_client.release_compaction_lock(lock_id)
        logger.info("Released compaction lock: %s", lock_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error releasing lock: %s", str(e))


def compact_deltas(delta_keys: list, collection_name: str = "receipt_words") -> dict:
    """
    Download and compact multiple delta files into a single ChromaDB snapshot.
    
    Args:
        delta_keys: List of S3 keys for delta files to compact
        collection_name: Name of the ChromaDB collection (e.g., "receipt_words", "receipt_lines")
    """
    bucket_name = os.environ["CHROMADB_BUCKET"]
    snapshot_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create main ChromaDB instance for compaction
        main_client = chromadb.PersistentClient(
            path=os.path.join(temp_dir, collection_name)
        )
        main_collection = main_client.get_or_create_collection(
            name=collection_name,
            metadata={
                "created_at": datetime.utcnow().isoformat(),
                "collection_type": collection_name,
            },
        )

        # Process each delta
        for delta_key in delta_keys:
            logger.info("Processing delta for %s: %s", collection_name, delta_key)

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
            try:
                delta_client = chromadb.PersistentClient(path=delta_dir)
                # Try to get the collection with the expected name
                delta_collection = delta_client.get_collection(collection_name)
            except ValueError as e:
                # If collection doesn't exist, log and skip
                logger.warning(
                    "Delta %s does not contain collection %s, skipping: %s",
                    delta_key, collection_name, str(e)
                )
                continue

            # Get all data from delta
            delta_data = delta_collection.get(
                include=["embeddings", "documents", "metadatas"]
            )

            if delta_data["ids"]:
                # Add to main collection
                main_collection.add(
                    ids=delta_data["ids"],
                    embeddings=delta_data["embeddings"],
                    documents=delta_data["documents"],
                    metadatas=delta_data["metadatas"],
                )
                logger.info(
                    "Added %d embeddings from delta to %s collection",
                    len(delta_data['ids']),
                    collection_name
                )

        # Upload compacted snapshot to S3 with collection-specific path
        snapshot_key = f"snapshot/{collection_name}/{snapshot_id}/"
        collection_path = os.path.join(temp_dir, collection_name)
        
        for root, _, files in os.walk(collection_path):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, collection_path)
                s3_key = snapshot_key + relative_path

                s3_client.upload_file(local_path, bucket_name, s3_key)

        logger.info(
            "Uploaded %s snapshot %s to s3://%s/%s",
            collection_name, snapshot_id, bucket_name, snapshot_key
        )

        # Clean up processed deltas (optional - can be done via S3 lifecycle)
        if (
            os.environ.get("DELETE_PROCESSED_DELTAS", "false").lower()
            == "true"
        ):
            for delta_key in delta_keys:
                # Delete all objects with this prefix
                paginator = s3_client.get_paginator("list_objects_v2")
                pages = paginator.paginate(
                    Bucket=bucket_name, Prefix=delta_key
                )

                for page in pages:
                    if "Contents" in page:
                        objects = [
                            {"Key": obj["Key"]} for obj in page["Contents"]
                        ]
                        s3_client.delete_objects(
                            Bucket=bucket_name, Delete={"Objects": objects}
                        )
                logger.info("Deleted processed delta: %s", delta_key)

    return {
        "snapshot_id": snapshot_id,
        "snapshot_key": snapshot_key,
        "collection_name": collection_name,
        "deltas_processed": len(delta_keys),
    }
