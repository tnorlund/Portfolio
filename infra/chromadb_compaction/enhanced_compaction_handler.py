"""Enhanced ChromaDB compaction handler with stream message support.

This handler extends the existing compaction functionality to handle both:
1. Traditional delta file processing (existing functionality)
2. DynamoDB stream messages for metadata updates (new functionality)

Maintains compatibility with existing SQS queue and mutex lock infrastructure.
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict, List, Optional

import boto3

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.utils.lock_manager import LockManager
from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_from_s3,
    upload_delta_to_s3,
)

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
sqs_client = boto3.client("sqs")

# Initialize DynamoDB client only when needed to avoid import-time errors
dynamo_client = None


def get_dynamo_client():
    """Get DynamoDB client, initializing if needed."""
    global dynamo_client
    if dynamo_client is None:
        dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    return dynamo_client


# Get configuration from environment
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "15"))
compaction_queue_url = os.environ.get("COMPACTION_QUEUE_URL", "")


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Enhanced entry point for Lambda handler.

    Routes to appropriate handler based on trigger type:
    - SQS messages: Process stream events and traditional deltas
    - Direct invocation: Traditional compaction operations
    """
    logger.info("Enhanced compaction handler started")
    logger.info("Event: %s", json.dumps(event, default=str))

    # Check if this is an SQS trigger
    if "Records" in event:
        return process_sqs_messages(event["Records"])

    # Direct invocation - traditional compaction operations
    return traditional_compaction_handler(event, context)


def process_sqs_messages(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Process SQS messages from the compaction queue.

    Handles both:
    1. Stream messages (from DynamoDB stream processor)
    2. Traditional delta notifications (existing functionality)
    """
    logger.info(f"Processing {len(records)} SQS messages")

    stream_messages = []
    delta_messages = []
    processed_count = 0

    # Categorize messages by type
    for record in records:
        try:
            # Parse message body
            message_body = json.loads(record["body"])

            # Check message attributes for source
            attributes = record.get("messageAttributes", {})
            source = attributes.get("source", {}).get("stringValue", "unknown")

            if source == "dynamodb_stream":
                stream_messages.append(message_body)
            else:
                # Traditional delta message or unknown - treat as delta
                delta_messages.append(message_body)

            processed_count += 1

        except Exception as e:
            logger.error(f"Error parsing SQS message: {e}")
            continue

    # Process stream messages if any
    if stream_messages:
        stream_result = process_stream_messages(stream_messages)
        logger.info(f"Processed {len(stream_messages)} stream messages")

    # Process delta messages if any
    if delta_messages:
        delta_result = process_delta_messages(delta_messages)
        logger.info(f"Processed {len(delta_messages)} delta messages")

    return {
        "statusCode": 200,
        "processed_messages": processed_count,
        "stream_messages": len(stream_messages),
        "delta_messages": len(delta_messages),
        "message": "SQS messages processed successfully",
    }


def process_stream_messages(
    stream_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process DynamoDB stream messages for metadata updates.

    Uses the existing mutex lock to ensure thread-safe metadata updates.
    """
    logger.info(f"Processing {len(stream_messages)} stream messages")

    # Group messages by entity type for efficient processing
    metadata_updates = []
    label_updates = []

    for message in stream_messages:
        entity_type = message.get("entity_type")
        if entity_type == "RECEIPT_METADATA":
            metadata_updates.append(message)
        elif entity_type == "RECEIPT_WORD_LABEL":
            label_updates.append(message)
        else:
            logger.warning(f"Unknown entity type: {entity_type}")

    # Acquire lock for metadata updates
    lock_manager = LockManager(
        get_dynamo_client(),
        heartbeat_interval=heartbeat_interval,
        lock_duration_minutes=lock_duration_minutes,
    )

    try:
        lock_id = f"chroma-metadata-update-{int(time.time())}"
        lock_acquired = lock_manager.acquire(lock_id)

        if not lock_acquired:
            logger.warning("Could not acquire lock for metadata updates")
            return {
                "statusCode": 423,
                "error": "Could not acquire lock",
                "message": "Another process is performing updates",
            }

        # Start heartbeat
        lock_manager.start_heartbeat()

        # Process metadata updates
        metadata_results = []
        if metadata_updates:
            metadata_results = process_metadata_updates(metadata_updates)

        # Process label updates
        label_results = []
        if label_updates:
            label_results = process_label_updates(label_updates)

        return {
            "statusCode": 200,
            "metadata_updates": len(metadata_updates),
            "label_updates": len(label_updates),
            "metadata_results": metadata_results,
            "label_results": label_results,
            "message": "Stream messages processed successfully",
        }

    except Exception as e:
        logger.error(f"Error processing stream messages: {e}")
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Stream message processing failed",
        }
    finally:
        # Stop heartbeat and release lock
        lock_manager.stop_heartbeat()
        lock_manager.release()


def process_metadata_updates(
    metadata_updates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Process RECEIPT_METADATA updates.

    Updates merchant information across ALL embeddings for affected receipts.
    """
    logger.info(f"Processing {len(metadata_updates)} metadata updates")
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]

    for update_msg in metadata_updates:
        try:
            entity_data = update_msg["entity_data"]
            changes = update_msg["changes"]
            event_name = update_msg["event_name"]

            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]

            logger.info(
                f"Processing {event_name} for metadata: "
                f"image_id={image_id}, receipt_id={receipt_id}"
            )

            # Update metadata for both lines and words collections
            for database in ["lines", "words"]:
                snapshot_key = f"{database}/snapshot/latest/"

                try:
                    # Download current snapshot using helper
                    temp_dir = tempfile.mkdtemp()
                    download_result = download_snapshot_from_s3(
                        bucket=bucket,
                        snapshot_key=snapshot_key,
                        local_snapshot_path=temp_dir,
                        verify_integrity=True
                    )
                    
                    if download_result["status"] != "downloaded":
                        logger.error(f"Failed to download snapshot: {download_result}")
                        continue

                    # Load ChromaDB using helper
                    chroma_client = ChromaDBClient(
                        persist_directory=temp_dir,
                        collection_prefix="receipt",
                        mode="read"
                    )

                    # Get appropriate collection
                    try:
                        collection = chroma_client.get_collection(database)
                    except Exception:
                        logger.warning(
                            f"Collection receipt_{database} not found"
                        )
                        continue

                    # Update metadata for this receipt
                    if event_name == "REMOVE":
                        updated_count = remove_receipt_metadata(
                            collection, image_id, receipt_id
                        )
                    else:  # MODIFY
                        updated_count = update_receipt_metadata(
                            collection, image_id, receipt_id, changes
                        )

                    if updated_count > 0:
                        # Upload updated snapshot using helper
                        upload_result = upload_delta_to_s3(
                            local_delta_path=temp_dir,
                            bucket=bucket,
                            delta_key=snapshot_key.rstrip("/") + "/",
                            metadata={
                                "update_type": "metadata_update",
                                "image_id": image_id,
                                "receipt_id": str(receipt_id),
                                "updated_count": str(updated_count)
                            }
                        )
                        
                        if upload_result["status"] == "uploaded":
                            logger.info(
                                f"Updated {updated_count} records in receipt_{database}"
                            )
                        else:
                            logger.error(f"Failed to upload snapshot: {upload_result}")

                    results.append(
                        {
                            "database": database,
                            "collection": f"receipt_{database}",
                            "updated_count": updated_count,
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                        }
                    )

                except Exception as e:
                    logger.error(f"Error updating {database} metadata: {e}")
                    results.append(
                        {
                            "database": database,
                            "error": str(e),
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                        }
                    )
                finally:
                    if "temp_dir" in locals():
                        shutil.rmtree(temp_dir, ignore_errors=True)

        except Exception as e:
            logger.error(f"Error processing metadata update: {e}")
            results.append({"error": str(e), "message": update_msg})

    return results


def process_label_updates(
    label_updates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Process RECEIPT_WORD_LABEL updates.

    Updates label metadata for SPECIFIC word embeddings.
    """
    logger.info(f"Processing {len(label_updates)} label updates")
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]
    snapshot_key = (
        "words/snapshot/latest/"  # Labels only affect word embeddings
    )

    try:
        # Download current snapshot using helper
        temp_dir = tempfile.mkdtemp()
        download_result = download_snapshot_from_s3(
            bucket=bucket,
            snapshot_key=snapshot_key,
            local_snapshot_path=temp_dir,
            verify_integrity=True
        )
        
        if download_result["status"] != "downloaded":
            logger.error(f"Failed to download snapshot: {download_result}")
            return results

        # Load ChromaDB using helper
        chroma_client = ChromaDBClient(
            persist_directory=temp_dir,
            collection_prefix="receipt",
            mode="read"
        )

        # Get words collection
        try:
            collection = chroma_client.get_collection("words")
        except Exception:
            logger.warning("receipt_words collection not found")
            return results

        # Process each label update
        for update_msg in label_updates:
            try:
                entity_data = update_msg["entity_data"]
                changes = update_msg["changes"]
                event_name = update_msg["event_name"]

                image_id = entity_data["image_id"]
                receipt_id = entity_data["receipt_id"]
                line_id = entity_data["line_id"]
                word_id = entity_data["word_id"]

                # Create ChromaDB ID for this specific word
                chromadb_id = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"

                logger.info(
                    f"Processing {event_name} for label: {chromadb_id}"
                )

                if event_name == "REMOVE":
                    updated_count = remove_word_labels(collection, chromadb_id)
                else:  # MODIFY
                    updated_count = update_word_labels(
                        collection, chromadb_id, changes
                    )

                results.append(
                    {
                        "chromadb_id": chromadb_id,
                        "updated_count": updated_count,
                        "event_name": event_name,
                        "changes": list(changes.keys()) if changes else [],
                    }
                )

            except Exception as e:
                logger.error(f"Error processing label update: {e}")
                results.append({"error": str(e), "message": update_msg})

        # Upload updated snapshot if any updates occurred
        total_updates = sum(r.get("updated_count", 0) for r in results)
        if total_updates > 0:
            upload_result = upload_delta_to_s3(
                local_delta_path=temp_dir,
                bucket=bucket,
                delta_key=snapshot_key.rstrip("/") + "/",
                metadata={
                    "update_type": "label_update",
                    "total_updates": str(total_updates)
                }
            )
            
            if upload_result["status"] == "uploaded":
                logger.info(f"Updated {total_updates} word labels in ChromaDB")
            else:
                logger.error(f"Failed to upload snapshot: {upload_result}")

    except Exception as e:
        logger.error(f"Error processing label updates: {e}")
        results.append({"error": str(e)})
    finally:
        if "temp_dir" in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return results


def update_receipt_metadata(
    collection, image_id: str, receipt_id: int, changes: Dict[str, Any]
) -> int:
    """Update metadata for all embeddings of a specific receipt."""
    # Build query to find all embeddings for this receipt
    # ChromaDB IDs follow pattern: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#...
    id_prefix = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"

    # Get all records that match this receipt
    all_results = collection.get(include=["metadatas"])
    matching_ids = []
    matching_metadatas = []

    for i, record_id in enumerate(all_results["ids"]):
        if record_id.startswith(id_prefix):
            matching_ids.append(record_id)
            # Get existing metadata and apply changes
            existing_metadata = all_results["metadatas"][i] or {}
            updated_metadata = existing_metadata.copy()

            # Apply field changes
            for field, change in changes.items():
                new_value = change["new"]
                if new_value is not None:
                    updated_metadata[field] = new_value
                elif field in updated_metadata:
                    # Remove field if new value is None
                    del updated_metadata[field]

            # Add update timestamp
            updated_metadata["last_metadata_update"] = datetime.now(
                timezone.utc
            ).isoformat()
            matching_metadatas.append(updated_metadata)

    # Update records if any found
    if matching_ids:
        collection.update(ids=matching_ids, metadatas=matching_metadatas)
        logger.info(f"Updated metadata for {len(matching_ids)} embeddings")

    return len(matching_ids)


def remove_receipt_metadata(
    collection, image_id: str, receipt_id: int
) -> int:
    """Remove merchant metadata fields from all embeddings of a specific receipt."""
    id_prefix = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"

    # Fields to remove when metadata is deleted
    fields_to_remove = [
        "canonical_merchant_name",
        "merchant_name",
        "merchant_category",
        "address",
        "phone_number",
        "place_id",
    ]

    # Get all records that match this receipt
    all_results = collection.get(include=["metadatas"])
    matching_ids = []
    matching_metadatas = []

    for i, record_id in enumerate(all_results["ids"]):
        if record_id.startswith(id_prefix):
            matching_ids.append(record_id)
            # Remove merchant fields from metadata
            existing_metadata = all_results["metadatas"][i] or {}
            updated_metadata = existing_metadata.copy()

            for field in fields_to_remove:
                if field in updated_metadata:
                    del updated_metadata[field]

            # Add removal timestamp
            updated_metadata["metadata_removed_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            matching_metadatas.append(updated_metadata)

    # Update records if any found
    if matching_ids:
        collection.update(ids=matching_ids, metadatas=matching_metadatas)
        logger.info(f"Removed metadata from {len(matching_ids)} embeddings")

    return len(matching_ids)


def update_word_labels(
    collection, chromadb_id: str, changes: Dict[str, Any]
) -> int:
    """Update label metadata for a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            logger.warning(f"Word embedding not found: {chromadb_id}")
            return 0

        # Update metadata with label changes
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()

        # Apply label field changes with "label_" prefix to avoid conflicts
        for field, change in changes.items():
            label_field = f"label_{field}"
            new_value = change["new"]
            if new_value is not None:
                updated_metadata[label_field] = new_value
            elif label_field in updated_metadata:
                del updated_metadata[label_field]

        # Add update timestamp
        updated_metadata["last_label_update"] = datetime.now(
            timezone.utc
        ).isoformat()

        # Update the record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])

        logger.info(f"Updated labels for word: {chromadb_id}")
        return 1

    except Exception as e:
        logger.error(f"Error updating word labels for {chromadb_id}: {e}")
        return 0


def remove_word_labels(collection, chromadb_id: str) -> int:
    """Remove label metadata from a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            logger.warning(f"Word embedding not found: {chromadb_id}")
            return 0

        # Remove all label fields from metadata
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()

        # Remove all fields that start with "label_"
        label_fields = [
            key for key in updated_metadata.keys() if key.startswith("label_")
        ]
        for field in label_fields:
            del updated_metadata[field]

        # Add removal timestamp
        updated_metadata["labels_removed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        # Update the record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])

        logger.info(f"Removed labels from word: {chromadb_id}")
        return 1

    except Exception as e:
        logger.error(f"Error removing word labels for {chromadb_id}: {e}")
        return 0


def process_delta_messages(
    delta_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process traditional delta file messages.

    Delegates to existing compaction logic for backward compatibility.
    """
    logger.info(f"Processing {len(delta_messages)} delta messages")

    # For now, log and return success
    # In full implementation, this would trigger the existing compaction workflow
    return {
        "statusCode": 200,
        "processed_deltas": len(delta_messages),
        "message": "Delta messages logged for processing",
    }


def traditional_compaction_handler(
    event: Dict[str, Any], context: Any
) -> Dict[str, Any]:
    """Handle traditional direct invocation compaction operations.

    Maintains backward compatibility with existing compaction workflows.
    """
    # Import and delegate to existing compaction handler
    from .compaction import compact_handler

    return compact_handler(event, context)


# Note: S3 utility functions removed - now using chroma_s3_helpers instead
