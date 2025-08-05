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
                "embedding_count": 100
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

    # Extract delta information
    delta_keys = [result["delta_key"] for result in delta_results]
    total_embeddings = sum(
        result.get("embedding_count", 0) for result in delta_results
    )

    logger.info(
        "Compacting %d deltas with %d total embeddings",
        len(delta_keys),
        total_embeddings
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

        # Perform compaction
        compaction_result = compact_deltas(delta_keys)

        # Release lock
        release_compaction_lock()

        logger.info(
            "Compaction completed successfully: %s", compaction_result
        )

        return {
            "statusCode": 200,
            "compaction_method": "batch",
            "delta_count": len(delta_keys),
            "total_embeddings": total_embeddings,
            "snapshot_key": compaction_result["snapshot_key"],
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


def compact_deltas(delta_keys: list) -> dict:
    """
    Download and compact multiple delta files into a single ChromaDB snapshot.
    """
    bucket_name = os.environ["CHROMADB_BUCKET"]
    snapshot_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create main ChromaDB instance for compaction
        main_client = chromadb.PersistentClient(
            path=os.path.join(temp_dir, "main")
        )
        main_collection = main_client.get_or_create_collection(
            name="receipt_words",
            metadata={"created_at": datetime.utcnow().isoformat()},
        )

        # Process each delta
        for delta_key in delta_keys:
            logger.info("Processing delta: %s", delta_key)

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
            delta_collection = delta_client.get_collection("receipt_words")

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
                    "Added %d embeddings from delta",
                    len(delta_data['ids'])
                )

        # Upload compacted snapshot to S3
        snapshot_key = f"snapshot/{snapshot_id}/"
        for root, _, files in os.walk(os.path.join(temp_dir, "main")):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(
                    local_path, os.path.join(temp_dir, "main")
                )
                s3_key = snapshot_key + relative_path

                s3_client.upload_file(local_path, bucket_name, s3_key)

        logger.info(
            "Uploaded snapshot %s to s3://%s/%s",
            snapshot_id, bucket_name, snapshot_key
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
        "deltas_processed": len(delta_keys),
    }
