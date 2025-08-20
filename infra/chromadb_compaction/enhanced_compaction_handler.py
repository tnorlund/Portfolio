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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class LambdaResponse:
    """Response from the Lambda handler with processing statistics."""

    status_code: int
    message: str
    processed_messages: Optional[int] = None
    stream_messages: Optional[int] = None
    delta_messages: Optional[int] = None
    metadata_updates: Optional[int] = None
    label_updates: Optional[int] = None
    metadata_results: Optional[List[Dict[str, Any]]] = None
    label_results: Optional[List[Dict[str, Any]]] = None
    processed_deltas: Optional[int] = None
    skipped_deltas: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to AWS Lambda-compatible dictionary."""
        result = {"statusCode": self.status_code, "message": self.message}

        # Add optional fields if they have values
        optional_fields = [
            "processed_messages", "stream_messages", "delta_messages",
            "metadata_updates", "label_updates", "metadata_results",
            "label_results", "processed_deltas", "skipped_deltas", "error"
        ]

        for field in optional_fields:
            value = getattr(self, field)
            if value is not None:
                result[field] = value

        return result


@dataclass(frozen=True)
class StreamMessage:
    """Parsed stream message from SQS."""

    entity_type: str
    entity_data: Dict[str, Any]
    changes: Dict[str, Any]
    event_name: str
    source: str = "dynamodb_stream"


@dataclass(frozen=True)
class MetadataUpdateResult:
    """Result from processing a metadata update."""

    database: str
    collection: str
    updated_count: int
    image_id: str
    receipt_id: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "database": self.database,
            "collection": self.collection,
            "updated_count": self.updated_count,
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class LabelUpdateResult:
    """Result from processing a label update."""

    chromadb_id: str
    updated_count: int
    event_name: str
    changes: List[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "chromadb_id": self.chromadb_id,
            "updated_count": self.updated_count,
            "event_name": self.event_name,
            "changes": self.changes,
        }
        if self.error:
            result["error"] = self.error
        return result


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
DYNAMO_CLIENT = None


def get_dynamo_client():
    """Get DynamoDB client, initializing if needed."""
    global DYNAMO_CLIENT  # pylint: disable=global-statement
    if DYNAMO_CLIENT is None:
        DYNAMO_CLIENT = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    return DYNAMO_CLIENT


# Get configuration from environment
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "15"))
compaction_queue_url = os.environ.get("COMPACTION_QUEUE_URL", "")


def handle(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
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

    # Direct invocation not supported - Lambda is for SQS triggers only
    logger.warning(
        "Direct invocation not supported. "
        "This Lambda processes SQS messages only."
    )
    response = LambdaResponse(
        status_code=400,
        error="Direct invocation not supported",
        message=(
            "This Lambda is designed to process SQS messages "
            "from DynamoDB streams"
        ),
    )
    return response.to_dict()


def process_sqs_messages(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Process SQS messages from the compaction queue.

    Handles both:
    1. Stream messages (from DynamoDB stream processor)
    2. Traditional delta notifications (existing functionality)
    """
    logger.info("Processing %d SQS messages", len(records))

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

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error parsing SQS message: %s", e)
            continue

    # Process stream messages if any
    if stream_messages:
        process_stream_messages(stream_messages)
        logger.info("Processed %d stream messages", len(stream_messages))

    # Process delta messages if any
    if delta_messages:
        process_delta_messages(delta_messages)
        logger.info("Processed %d delta messages", len(delta_messages))

    response = LambdaResponse(
        status_code=200,
        processed_messages=processed_count,
        stream_messages=len(stream_messages),
        delta_messages=len(delta_messages),
        message="SQS messages processed successfully",
    )
    return response.to_dict()


def process_stream_messages(
    stream_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process DynamoDB stream messages for metadata updates.

    Uses the existing mutex lock to ensure thread-safe metadata updates.
    """
    logger.info("Processing %d stream messages", len(stream_messages))

    # Parse and group messages by entity type for efficient processing
    metadata_updates = []
    label_updates = []

    for message_dict in stream_messages:
        try:
            # Parse into StreamMessage dataclass for type safety
            message = StreamMessage(
                entity_type=message_dict.get("entity_type", ""),
                entity_data=message_dict.get("entity_data", {}),
                changes=message_dict.get("changes", {}),
                event_name=message_dict.get("event_name", ""),
                source=message_dict.get("source", "dynamodb_stream"),
            )

            if message.entity_type == "RECEIPT_METADATA":
                metadata_updates.append(message)
            elif message.entity_type == "RECEIPT_WORD_LABEL":
                label_updates.append(message)
            else:
                logger.warning("Unknown entity type: %s", message.entity_type)

        except (KeyError, TypeError) as e:
            logger.error("Failed to parse stream message: %s", e)
            continue

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
            response = LambdaResponse(
                status_code=423,
                error="Could not acquire lock",
                message="Another process is performing updates",
            )
            return response.to_dict()

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

        response = LambdaResponse(
            status_code=200,
            metadata_updates=len(metadata_updates),
            label_updates=len(label_updates),
            metadata_results=metadata_results,
            label_results=label_results,
            message="Stream messages processed successfully",
        )
        return response.to_dict()

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error processing stream messages: %s", e)
        response = LambdaResponse(
            status_code=500,
            error=str(e),
            message="Stream message processing failed",
        )
        return response.to_dict()
    finally:
        # Stop heartbeat and release lock
        lock_manager.stop_heartbeat()
        lock_manager.release()


def process_metadata_updates(
    metadata_updates: List[StreamMessage],
) -> List[Dict[str, Any]]:
    """Process RECEIPT_METADATA updates.

    Updates merchant information across ALL embeddings for affected receipts.
    """
    logger.info("Processing %d metadata updates", len(metadata_updates))
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]

    for update_msg in metadata_updates:
        try:
            entity_data = update_msg.entity_data
            changes = update_msg.changes
            event_name = update_msg.event_name

            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]

            logger.info(
                "Processing %s for metadata: image_id=%s, receipt_id=%s",
                event_name, image_id, receipt_id
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
                        verify_integrity=True,
                    )

                    if download_result["status"] != "downloaded":
                        logger.error(
                            "Failed to download snapshot: %s",
                            download_result
                        )
                        continue

                    # Load ChromaDB using helper
                    chroma_client = ChromaDBClient(
                        persist_directory=temp_dir,
                        collection_prefix="receipt",
                        mode="read",
                    )

                    # Get appropriate collection
                    try:
                        collection = chroma_client.get_collection(database)
                    except Exception:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            "Collection receipt_%s not found", database
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
                                "updated_count": str(updated_count),
                            },
                        )

                        if upload_result["status"] == "uploaded":
                            logger.info(
                                "Updated %d records in receipt_%s",
                                updated_count, database
                            )
                        else:
                            logger.error(
                                "Failed to upload snapshot: %s", upload_result
                            )

                    result = MetadataUpdateResult(
                        database=database,
                        collection=f"receipt_{database}",
                        updated_count=updated_count,
                        image_id=image_id,
                        receipt_id=receipt_id,
                    )
                    results.append(result.to_dict())

                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error("Error updating %s metadata: %s", database, e)
                    result = MetadataUpdateResult(
                        database=database,
                        collection=f"receipt_{database}",
                        updated_count=0,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        error=str(e),
                    )
                    results.append(result.to_dict())
                finally:
                    if "temp_dir" in locals():
                        shutil.rmtree(temp_dir, ignore_errors=True)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error processing metadata update: %s", e)
            # Create error result with minimal info available
            result = MetadataUpdateResult(
                database="unknown",
                collection="unknown",
                updated_count=0,
                image_id="unknown",
                receipt_id=0,
                error=f"{str(e)} - message: {update_msg}",
            )
            results.append(result.to_dict())

    return results


def process_label_updates(
    label_updates: List[StreamMessage],
) -> List[Dict[str, Any]]:
    """Process RECEIPT_WORD_LABEL updates.

    Updates label metadata for SPECIFIC word embeddings.
    """
    logger.info("Processing %d label updates", len(label_updates))
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
            verify_integrity=True,
        )

        if download_result["status"] != "downloaded":
            logger.error("Failed to download snapshot: %s", download_result)
            return results

        # Load ChromaDB using helper
        chroma_client = ChromaDBClient(
            persist_directory=temp_dir,
            collection_prefix="receipt",
            mode="read",
        )

        # Get words collection
        try:
            collection = chroma_client.get_collection("words")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("receipt_words collection not found")
            return results

        # Process each label update
        for update_msg in label_updates:
            try:
                entity_data = update_msg.entity_data
                changes = update_msg.changes
                event_name = update_msg.event_name

                image_id = entity_data["image_id"]
                receipt_id = entity_data["receipt_id"]
                line_id = entity_data["line_id"]
                word_id = entity_data["word_id"]

                # Create ChromaDB ID for this specific word
                chromadb_id = (
                    f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"
                    f"LINE#{line_id:05d}#WORD#{word_id:05d}"
                )

                logger.info(
                    "Processing %s for label: %s", event_name, chromadb_id
                )

                if event_name == "REMOVE":
                    updated_count = remove_word_labels(collection, chromadb_id)
                else:  # MODIFY
                    updated_count = update_word_labels(
                        collection, chromadb_id, changes
                    )

                result = LabelUpdateResult(
                    chromadb_id=chromadb_id,
                    updated_count=updated_count,
                    event_name=event_name,
                    changes=list(changes.keys()) if changes else [],
                )
                results.append(result.to_dict())

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error processing label update: %s", e)
                result = LabelUpdateResult(
                    chromadb_id="unknown",
                    updated_count=0,
                    event_name="unknown",
                    changes=[],
                    error=f"{str(e)} - message: {update_msg}",
                )
                results.append(result.to_dict())

        # Upload updated snapshot if any updates occurred
        total_updates = sum(r.get("updated_count", 0) for r in results)
        if total_updates > 0:
            upload_result = upload_delta_to_s3(
                local_delta_path=temp_dir,
                bucket=bucket,
                delta_key=snapshot_key.rstrip("/") + "/",
                metadata={
                    "update_type": "label_update",
                    "total_updates": str(total_updates),
                },
            )

            if upload_result["status"] == "uploaded":
                logger.info(
                    "Updated %d word labels in ChromaDB", total_updates
                )
            else:
                logger.error("Failed to upload snapshot: %s", upload_result)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error processing label updates: %s", e)
        result = LabelUpdateResult(
            chromadb_id="unknown",
            updated_count=0,
            event_name="unknown",
            changes=[],
            error=str(e),
        )
        results.append(result.to_dict())
    finally:
        if "temp_dir" in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return results


def update_receipt_metadata(
    collection, image_id: str, receipt_id: int, changes: Dict[str, Any]
) -> int:
    """Update metadata for all embeddings of a specific receipt."""
    # Build query to find all embeddings for this receipt
    # ChromaDB IDs follow pattern:
    # IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#..."
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
        logger.info("Updated metadata for %d embeddings", len(matching_ids))

    return len(matching_ids)


def remove_receipt_metadata(
    collection, image_id: str, receipt_id: int
) -> int:
    """Remove merchant metadata fields from all embeddings of
    a specific receipt."""
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
        logger.info("Removed metadata from %d embeddings", len(matching_ids))

    return len(matching_ids)


def update_word_labels(
    collection, chromadb_id: str, changes: Dict[str, Any]
) -> int:
    """Update label metadata for a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            logger.warning("Word embedding not found: %s", chromadb_id)
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

        logger.info("Updated labels for word: %s", chromadb_id)
        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error updating word labels for %s: %s", chromadb_id, e)
        return 0


def remove_word_labels(collection, chromadb_id: str) -> int:
    """Remove label metadata from a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            logger.warning("Word embedding not found: %s", chromadb_id)
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

        logger.info("Removed labels from word: %s", chromadb_id)
        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error removing word labels for %s: %s", chromadb_id, e)
        return 0


def process_delta_messages(
    delta_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process traditional delta file messages.

    Currently not implemented - this handler focuses on DynamoDB
    stream messages. Traditional delta processing would be handled
    by a separate compaction system.
    """
    logger.info(
        "Received %d delta messages (not processed)",
        len(delta_messages)
    )
    logger.warning("Delta message processing not implemented in this handler")

    response = LambdaResponse(
        status_code=200,
        processed_deltas=0,  # None actually processed
        skipped_deltas=len(delta_messages),
        message=(
            "Delta messages skipped - not implemented in "
            "stream-focused handler"
        ),
    )
    return response.to_dict()


# Note: S3 utility functions removed - now using chroma_s3_helpers
# instead
# Note: Traditional compaction handler removed - this Lambda is
# SQS-triggered only
