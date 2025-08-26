"""Enhanced ChromaDB compaction handler with stream message support.

This handler extends the existing compaction functionality to handle both:
1. Traditional delta file processing (existing functionality)
2. DynamoDB stream messages for metadata updates (new functionality)

Maintains compatibility with existing SQS queue and mutex lock infrastructure.
"""

# pylint: disable=duplicate-code,too-many-instance-attributes,too-many-locals,too-many-nested-blocks
# Some duplication with stream_processor is expected for shared data structures
# Complex compaction logic requires many variables and nested operations

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

# Enhanced observability imports (with fallback)
try:
    from utils import (
        get_operation_logger,
        metrics,
        trace_function,
        trace_compaction_operation,
        start_compaction_lambda_monitoring,
        stop_compaction_lambda_monitoring,
        with_compaction_timeout_protection,
        format_response,
    )
    OBSERVABILITY_AVAILABLE = True
except ImportError:
    # Fallback for development/testing - provide no-op decorators
    OBSERVABILITY_AVAILABLE = False
    
    # No-op decorator functions for fallback
    def trace_function(operation_name=None, collection=None):
        def decorator(func):
            return func
        return decorator
    
    def trace_compaction_operation(operation_name=None):
        def decorator(func):
            return func
        return decorator
    
    def with_compaction_timeout_protection(max_duration=None):
        def decorator(func):
            return func
        return decorator
    
    # Placeholder functions
    get_operation_logger = None
    metrics = None
    start_compaction_lambda_monitoring = None
    stop_compaction_lambda_monitoring = None
    format_response = None

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.lock_manager import LockManager
from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.vector_store import upload_snapshot_with_hash
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_from_s3,
    upload_delta_to_s3,
    upload_snapshot_atomic,
    download_snapshot_atomic,
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
            "processed_messages",
            "stream_messages",
            "delta_messages",
            "metadata_updates",
            "label_updates",
            "metadata_results",
            "label_results",
            "processed_deltas",
            "skipped_deltas",
            "error",
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
    collection: ChromaDBCollection
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


# Enhanced structured logging with fallback
if OBSERVABILITY_AVAILABLE:
    logger = get_operation_logger(__name__)
else:
    # Fallback to basic logging
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
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "30"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "3"))
max_heartbeat_failures = int(os.environ.get("MAX_HEARTBEAT_FAILURES", "3"))
compaction_queue_url = os.environ.get("COMPACTION_QUEUE_URL", "")


@trace_function(operation_name="enhanced_compaction_handler")
@with_compaction_timeout_protection(max_duration=840)  # 14 minutes for long compaction operations
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Enhanced entry point for Lambda handler.

    Routes to appropriate handler based on trigger type:
    - SQS messages: Process stream events and traditional deltas
    - Direct invocation: Traditional compaction operations
    """
    correlation_id = None
    
    # Start enhanced monitoring if available
    if OBSERVABILITY_AVAILABLE:
        start_compaction_lambda_monitoring(context)
        correlation_id = getattr(logger, 'correlation_id', None)
        logger.info("Enhanced compaction handler started",
                   event_keys=list(event.keys()),
                   correlation_id=correlation_id)
    else:
        logger.info("Enhanced compaction handler started")
        logger.info("Event structure present with size: %s", len(json.dumps(event, default=str)))
        
    start_time = time.time()
    function_name = context.function_name if context and hasattr(context, 'function_name') else "enhanced_compaction_handler"
    
    try:
        # Check if this is an SQS trigger
        if "Records" in event:
            if OBSERVABILITY_AVAILABLE:
                metrics.gauge("CompactionRecordsReceived", len(event["Records"]))
                
            result = process_sqs_messages(event["Records"])
            
            # Track successful execution with metrics if available
            execution_time = time.time() - start_time
            if OBSERVABILITY_AVAILABLE:
                metrics.timer("CompactionLambdaExecutionTime", execution_time)
                metrics.count("CompactionLambdaSuccess", 1)
                logger.info("Enhanced compaction handler completed successfully",
                           execution_time_seconds=execution_time)
            else:
                logger.info(f"Enhanced compaction handler completed in {execution_time:.2f}s")
                
            # Format response with observability
            if OBSERVABILITY_AVAILABLE:
                return format_response(result, event)
            return result

        # Direct invocation not supported - Lambda is for SQS triggers only
        if OBSERVABILITY_AVAILABLE:
            logger.warning("Direct invocation not supported", 
                          invocation_type="direct")
            metrics.count("CompactionDirectInvocationAttempt", 1)
        else:
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
        
        if OBSERVABILITY_AVAILABLE:
            return format_response(response.to_dict(), event, is_error=True)
        return response.to_dict()
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_type = type(e).__name__
        
        # Enhanced error logging if available
        if OBSERVABILITY_AVAILABLE:
            logger.error("Enhanced compaction handler failed",
                        error=str(e),
                        error_type=error_type,
                        execution_time_seconds=execution_time,
                        exc_info=True)
            metrics.count("CompactionLambdaError", 1, {"error_type": error_type})
        else:
            logger.error(f"Handler failed: {str(e)}", exc_info=True)
        
        error_response = LambdaResponse(
            status_code=500,
            message=f"Compaction handler failed: {str(e)}",
            error=str(e)
        )
        
        if OBSERVABILITY_AVAILABLE:
            return format_response(error_response.to_dict(), event, is_error=True)
        return error_response.to_dict()
        
    finally:
        # Stop monitoring if available
        if OBSERVABILITY_AVAILABLE:
            stop_compaction_lambda_monitoring()


@trace_compaction_operation(operation_name="sqs_message_processing")
def process_sqs_messages(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Process SQS messages from the compaction queue.

    Handles both:
    1. Stream messages (from DynamoDB stream processor)
    2. Traditional delta notifications (existing functionality)
    
    Returns partial batch failure response for unprocessed delta messages.
    """
    logger.info("Processing SQS messages", message_count=len(records))

    stream_messages = []
    delta_message_records = []  # Store full records for delta messages
    processed_count = 0
    failed_message_ids = []  # Track failed message IDs for partial batch failure

    # Categorize messages by type
    for record in records:
        try:
            # Parse message body
            message_body = json.loads(record["body"])

            # Check message attributes for source
            attributes = record.get("messageAttributes", {})
            source = attributes.get("source", {}).get("stringValue", "unknown")

            if source == "dynamodb_stream":
                # Get collection from message attributes
                collection_value = attributes.get("collection", {}).get(
                    "stringValue"
                )
                if not collection_value:
                    logger.warning(
                        "Stream message missing collection attribute"
                    )
                    if OBSERVABILITY_AVAILABLE:
                        metrics.count("CompactionMessageMissingCollection", 1)
                    continue
                    
                try:
                    collection = ChromaDBCollection(collection_value)
                except ValueError:
                    logger.warning(
                        "Invalid collection value", collection_value=collection_value
                    )
                    if OBSERVABILITY_AVAILABLE:
                        metrics.count("CompactionInvalidCollection", 1)
                    continue

                # Parse stream message with collection info
                stream_msg = StreamMessage(
                    entity_type=message_body.get("entity_type", ""),
                    entity_data=message_body.get("entity_data", {}),
                    changes=message_body.get("changes", {}),
                    event_name=message_body.get("event_name", ""),
                    collection=collection,
                    source=source,
                )
                stream_messages.append(stream_msg)
                
                if OBSERVABILITY_AVAILABLE:
                    metrics.count("CompactionStreamMessage", 1, {
                        "entity_type": stream_msg.entity_type,
                        "collection": collection.value
                    })
            else:
                # Traditional delta message or unknown - treat as delta
                # Store the full record to get messageId for partial batch failure
                delta_message_records.append({
                    "record": record,
                    "body": message_body
                })
                
                if OBSERVABILITY_AVAILABLE:
                    metrics.count("CompactionDeltaMessage", 1)

            processed_count += 1

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error parsing SQS message", error=str(e))
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionMessageParseError", 1, {
                    "error_type": type(e).__name__
                })
            continue

    # Process stream messages if any
    if stream_messages:
        result = process_stream_messages(stream_messages)
        logger.info("Processed stream messages", count=len(stream_messages))

    # Process delta messages if any - collect failed message IDs
    if delta_message_records:
        delta_bodies = [msg["body"] for msg in delta_message_records]
        delta_result = process_delta_messages(delta_bodies)
        
        # Since delta processing is not implemented, mark all delta messages as failed
        # to prevent data loss by forcing SQS to retry them
        for msg_record in delta_message_records:
            message_id = msg_record["record"].get("receiptHandle")
            if message_id:
                failed_message_ids.append(message_id)
        
        if OBSERVABILITY_AVAILABLE:
            logger.warning("Delta messages not processed - added to failed list for retry", 
                          count=len(delta_message_records))
            metrics.count("CompactionDeltaMessagesFailedForRetry", len(delta_message_records))
        else:
            logger.warning("Processed delta messages (marked as failed for retry)", count=len(delta_message_records))

    # Return partial batch failure response if there are failed messages
    if failed_message_ids:
        response = {
            "batchItemFailures": [
                {"itemIdentifier": msg_id} for msg_id in failed_message_ids
            ]
        }
        
        if OBSERVABILITY_AVAILABLE:
            logger.info("Returning partial batch failure response", 
                       failed_count=len(failed_message_ids),
                       stream_processed=len(stream_messages))
            metrics.count("CompactionPartialBatchFailure", 1)
            metrics.gauge("CompactionFailedMessages", len(failed_message_ids))
        else:
            logger.info(f"Partial batch failure: {len(failed_message_ids)} failed, "
                       f"{len(stream_messages)} stream messages processed")
        
        return response

    # All messages processed successfully
    response = LambdaResponse(
        status_code=200,
        processed_messages=processed_count,
        stream_messages=len(stream_messages),
        delta_messages=len(delta_message_records),
        message="SQS messages processed successfully",
    )
    
    if OBSERVABILITY_AVAILABLE:
        metrics.gauge("CompactionProcessedMessages", processed_count)
        metrics.gauge("CompactionStreamMessages", len(stream_messages))
        metrics.gauge("CompactionDeltaMessages", len(delta_message_records))
        metrics.gauge("CompactionBatchProcessedSuccessfully", len(records))
        metrics.count("CompactionBatchProcessingSuccess", 1)
        
    return response.to_dict()


@trace_compaction_operation(operation_name="stream_message_processing")
def process_stream_messages(
    stream_messages: List[StreamMessage],
) -> Dict[str, Any]:
    """Process DynamoDB stream messages for metadata updates.

    Groups messages by collection and processes each collection in batches
    for improved efficiency with bulk ChromaDB operations.
    """
    logger.info("Processing stream messages with batch optimization", 
               message_count=len(stream_messages))
    
    if OBSERVABILITY_AVAILABLE:
        metrics.gauge("CompactionBatchSize", len(stream_messages))

    # Group messages by collection
    messages_by_collection = {}
    for msg in stream_messages:
        collection = msg.collection
        if collection not in messages_by_collection:
            messages_by_collection[collection] = []
        messages_by_collection[collection].append(msg)

    if OBSERVABILITY_AVAILABLE:
        logger.info("Messages grouped by collection",
                   collections={col.value: len(msgs) for col, msgs in messages_by_collection.items()})
    else:
        logger.info(
            "Messages grouped by collection: %s",
            {col.value: len(msgs) for col, msgs in messages_by_collection.items()},
        )

    # Process each collection separately
    all_metadata_results = []
    all_label_results = []
    total_metadata_updates = 0
    total_label_updates = 0

    for collection, messages in messages_by_collection.items():
        if OBSERVABILITY_AVAILABLE:
            logger.info("Processing collection messages",
                       collection=collection.value,
                       message_count=len(messages))
        else:
            logger.info(
                "Processing %d messages for %s collection",
                len(messages),
                collection.value,
            )

        result = _process_collection_messages(collection, messages)
        # Aggregate results
        all_metadata_results.extend(result.get("metadata_results", []))
        all_label_results.extend(result.get("label_results", []))
        total_metadata_updates += result.get("metadata_updates", 0)
        total_label_updates += result.get("label_updates", 0)

    # Return aggregated results
    response = LambdaResponse(
        status_code=200,
        message=(
            f"Successfully processed {len(stream_messages)} stream messages"
        ),
        stream_messages=len(stream_messages),
        metadata_updates=total_metadata_updates,
        label_updates=total_label_updates,
        metadata_results=all_metadata_results,
        label_results=all_label_results,
    )
    
    if OBSERVABILITY_AVAILABLE:
        metrics.count("CompactionMetadataUpdates", total_metadata_updates)
        metrics.count("CompactionLabelUpdates", total_label_updates)
        
    return response.to_dict()


@trace_compaction_operation(operation_name="collection_message_processing")
def _process_collection_messages(
    collection: ChromaDBCollection, messages: List[StreamMessage]
) -> Dict[str, Any]:
    """Process messages for a specific collection with collection-specific
    lock.
    Args:
        collection: ChromaDBCollection to process
        messages: List of StreamMessage objects for this collection

    Returns:
        Dictionary with processing results
    """
    # Parse and group messages by entity type for efficient processing
    metadata_updates = []
    label_updates = []

    for message in messages:
        if message.entity_type == "RECEIPT_METADATA":
            metadata_updates.append(message)
        elif message.entity_type == "RECEIPT_WORD_LABEL":
            label_updates.append(message)
        else:
            logger.warning("Unknown entity type", entity_type=message.entity_type)
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionUnknownEntityType", 1, {
                    "entity_type": message.entity_type
                })

    # Acquire collection-specific lock for metadata updates
    lock_manager = LockManager(
        get_dynamo_client(),
        collection=collection,
        heartbeat_interval=heartbeat_interval,
        lock_duration_minutes=lock_duration_minutes,
        max_heartbeat_failures=max_heartbeat_failures,
    )

    try:
        # Create collection-specific lock ID
        lock_id = f"chroma-{collection.value}-update-{int(time.time())}"
        lock_acquired = lock_manager.acquire(lock_id)

        if not lock_acquired:
            if OBSERVABILITY_AVAILABLE:
                logger.warning("Could not acquire collection lock",
                              collection=collection.value)
                metrics.count("CompactionLockAcquisitionFailed", 1, {
                    "collection": collection.value
                })
            else:
                logger.warning(
                    "Could not acquire lock for %s collection updates",
                    collection.value,
                )
            return {
                "error": (
                    f"Could not acquire lock for {collection.value} collection"
                ),
                "metadata_updates": 0,
                "label_updates": 0,
                "metadata_results": [],
                "label_results": [],
            }

        if OBSERVABILITY_AVAILABLE:
            logger.info("Acquired collection lock",
                       collection=collection.value,
                       lock_id=lock_id)
            metrics.count("CompactionLockAcquired", 1, {"collection": collection.value})
        else:
            logger.info(
                "Acquired lock for %s collection: %s", collection.value, lock_id
            )

        # Start heartbeat
        lock_manager.start_heartbeat()

        # Process metadata updates with lock validation
        metadata_results = []
        if metadata_updates:
            # Validate lock ownership before critical operations
            if not lock_manager.validate_ownership():
                if OBSERVABILITY_AVAILABLE:
                    logger.error("Lock ownership validation failed before metadata updates",
                               collection=collection.value, lock_id=lock_id)
                    metrics.count("CompactionLockValidationFailed", 1, {
                        "collection": collection.value,
                        "operation": "metadata_updates"
                    })
                else:
                    logger.error("Lock validation failed before metadata updates for %s", collection.value)
                
                return {
                    "error": f"Lock validation failed for {collection.value} collection",
                    "metadata_updates": 0,
                    "label_updates": 0,
                    "metadata_results": [],
                    "label_results": [],
                }
            
            metadata_results = process_metadata_updates(
                metadata_updates, collection, lock_manager
            )

        # Process label updates with lock validation
        label_results = []
        if label_updates:
            # Validate lock ownership before critical operations
            logger.info("Validating lock ownership before label updates",
                       collection=collection.value, lock_id=lock_id, 
                       label_count=len(label_updates))
            
            lock_validation_start = time.time()
            is_owner = lock_manager.validate_ownership()
            lock_validation_time = time.time() - lock_validation_start
            
            logger.info("Lock validation completed",
                       collection=collection.value, lock_id=lock_id,
                       is_owner=is_owner, validation_time_ms=lock_validation_time * 1000)
            
            if not is_owner:
                if OBSERVABILITY_AVAILABLE:
                    logger.error("Lock ownership validation failed before label updates",
                               collection=collection.value, lock_id=lock_id,
                               validation_time_ms=lock_validation_time * 1000)
                    metrics.count("CompactionLockValidationFailed", 1, {
                        "collection": collection.value,
                        "operation": "label_updates"
                    })
                else:
                    logger.error("Lock validation failed before label updates for %s", collection.value)
                
                return {
                    "error": f"Lock validation failed for {collection.value} collection",
                    "metadata_updates": len(metadata_results) if metadata_results else 0,
                    "label_updates": 0,
                    "metadata_results": metadata_results,
                    "label_results": [],
                }
            
            label_results = process_label_updates(label_updates, collection, lock_manager)

        total_metadata_updates = sum(
            result.updated_count
            for result in metadata_results
            if result.error is None
        )
        total_label_updates = sum(
            result.updated_count
            for result in label_results
            if result.error is None
        )

        if OBSERVABILITY_AVAILABLE:
            logger.info("Completed collection processing",
                       collection=collection.value,
                       metadata_updates=total_metadata_updates,
                       label_updates=total_label_updates)
        else:
            logger.info(
                "Completed %s collection: %d metadata, %d label updates",
                collection.value,
                total_metadata_updates,
                total_label_updates,
            )

        return {
            "metadata_updates": total_metadata_updates,
            "label_updates": total_label_updates,
            "metadata_results": [
                result.to_dict() for result in metadata_results
            ],
            "label_results": [result.to_dict() for result in label_results],
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        if OBSERVABILITY_AVAILABLE:
            logger.error("Error processing collection messages",
                        collection=collection.value,
                        error=str(e))
            metrics.count("CompactionCollectionProcessingError", 1, {
                "collection": collection.value,
                "error_type": type(e).__name__
            })
        else:
            logger.error(
                "Error processing %s collection messages: %s", collection.value, e
            )
        return {
            "error": str(e),
            "metadata_updates": 0,
            "label_updates": 0,
            "metadata_results": [],
            "label_results": [],
        }
    finally:
        # Stop heartbeat and release lock
        lock_manager.stop_heartbeat()
        lock_manager.release()
        
        if OBSERVABILITY_AVAILABLE:
            logger.info("Released collection lock",
                       collection=collection.value,
                       lock_id=lock_id)
        else:
            logger.info(
                "Released lock for %s collection: %s", collection.value, lock_id
            )


@trace_compaction_operation(operation_name="metadata_updates")
def process_metadata_updates(
    metadata_updates: List[StreamMessage],
    collection: ChromaDBCollection,
    lock_manager: Optional["LockManager"] = None,
) -> List[MetadataUpdateResult]:
    """Process RECEIPT_METADATA updates for a specific collection.

    Updates merchant information for embeddings in the specified collection.
    """
    logger.info("Processing metadata updates", count=len(metadata_updates))
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]

    for update_msg in metadata_updates:
        try:
            entity_data = update_msg.entity_data
            changes = update_msg.changes
            event_name = update_msg.event_name

            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]

            if OBSERVABILITY_AVAILABLE:
                logger.info("Processing metadata update",
                           event_name=event_name,
                           image_id=image_id,
                           receipt_id=receipt_id,
                           changes=list(changes.keys()))
            else:
                logger.info(
                    "Processing %s for metadata: image_id=%s, receipt_id=%s",
                    event_name,
                    image_id,
                    receipt_id,
                )

            # Update metadata for the specified collection
            database = collection.value
            snapshot_key = f"{database}/snapshot/latest/"

            try:
                # Validate lock ownership before S3 operations
                if lock_manager and not lock_manager.validate_ownership():
                    error_msg = f"Lock validation failed during metadata update for {collection.value}"
                    if OBSERVABILITY_AVAILABLE:
                        logger.error("Lock ownership lost during metadata processing",
                                   collection=collection.value, receipt_id=receipt_id)
                        metrics.count("CompactionLockValidationFailed", 1, {
                            "collection": collection.value,
                            "operation": "s3_download"
                        })
                    else:
                        logger.error(error_msg)
                    
                    results.append(MetadataUpdateResult(
                        database=database,
                        collection=database,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        error=error_msg,
                        updated_count=0
                    ))
                    continue

                # Download current snapshot using atomic helper
                temp_dir = tempfile.mkdtemp()
                download_result = download_snapshot_atomic(
                    bucket=bucket,
                    collection=collection.value,  # "lines" or "words"
                    local_path=temp_dir,
                    verify_integrity=True,
                )

                if download_result["status"] != "downloaded":
                    logger.error(
                        "Failed to download snapshot", result=download_result
                    )
                    
                    if OBSERVABILITY_AVAILABLE:
                        metrics.count("CompactionSnapshotDownloadError", 1, {
                            "collection": collection.value
                        })
                    continue

                # Load ChromaDB using helper in metadata-only mode
                chroma_client = ChromaDBClient(
                    persist_directory=temp_dir,
                    mode="read",
                    metadata_only=True,  # No embeddings needed for metadata updates
                )

                # Get appropriate collection
                try:
                    logger.info("Getting ChromaDB collection", collection=database)
                    collection_obj = chroma_client.get_collection(database)
                    
                    logger.info("Successfully got ChromaDB collection", collection=database)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    if OBSERVABILITY_AVAILABLE:
                        logger.warning("ChromaDB collection not found",
                                      collection=database,
                                      error=str(e))
                        metrics.count("CompactionCollectionNotFound", 1, {
                            "collection": database
                        })
                    else:
                        logger.warning("Collection not found", database=database, error=str(e))
                    continue

                # Update metadata for this receipt
                if event_name == "REMOVE":
                    updated_count = remove_receipt_metadata(
                        collection_obj, image_id, receipt_id
                    )
                else:  # MODIFY
                    updated_count = update_receipt_metadata(
                        collection_obj, image_id, receipt_id, changes
                    )

                if updated_count > 0:
                    # Validate lock ownership before S3 upload
                    if lock_manager and not lock_manager.validate_ownership():
                        error_msg = f"Lock validation failed before snapshot upload for {collection.value}"
                        if OBSERVABILITY_AVAILABLE:
                            logger.error("Lock ownership lost before snapshot upload",
                                       collection=collection.value, receipt_id=receipt_id)
                            metrics.count("CompactionLockValidationFailed", 1, {
                                "collection": collection.value,
                                "operation": "s3_upload"
                            })
                        else:
                            logger.error(error_msg)
                        
                        results.append(MetadataUpdateResult(
                            database=database,
                            collection=database,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            error=error_msg,
                            updated_count=0
                        ))
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        continue

                    # Upload updated snapshot atomically with lock validation
                    upload_result = upload_snapshot_atomic(
                        local_path=temp_dir,
                        bucket=bucket,
                        collection=collection.value,  # "lines" or "words"
                        lock_manager=lock_manager,
                        metadata={
                            "update_type": "metadata_update",
                            "image_id": image_id,
                            "receipt_id": str(receipt_id),
                            "updated_count": str(updated_count),
                        },
                    )

                    if upload_result["status"] == "uploaded":
                        if OBSERVABILITY_AVAILABLE:
                            logger.info("Updated ChromaDB metadata",
                                       updated_count=updated_count,
                                       database=database,
                                       hash=upload_result.get("hash", "not_calculated"))
                            metrics.count("CompactionSnapshotUploaded", 1, {
                                "collection": database
                            })
                        else:
                            logger.info(
                                "Updated %d records in receipt_%s, hash: %s",
                                updated_count,
                                database,
                                upload_result.get("hash", "not_calculated")
                            )
                    else:
                        logger.error(
                            "Failed to upload snapshot", result=upload_result
                        )
                        
                        if OBSERVABILITY_AVAILABLE:
                            metrics.count("CompactionSnapshotUploadError", 1, {
                                "collection": database
                            })

                result = MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=updated_count,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
                results.append(result)

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error updating metadata",
                            database=database,
                            error=str(e))
                            
                if OBSERVABILITY_AVAILABLE:
                    metrics.count("CompactionMetadataUpdateError", 1, {
                        "collection": database,
                        "error_type": type(e).__name__
                    })
                    
                result = MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=0,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    error=str(e),
                )
                results.append(result)
            finally:
                if "temp_dir" in locals():
                    shutil.rmtree(temp_dir, ignore_errors=True)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error processing metadata update", error=str(e))
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionMetadataProcessingError", 1, {
                    "error_type": type(e).__name__
                })
                
            # Create error result with minimal info available
            result = MetadataUpdateResult(
                database="unknown",
                collection="unknown",
                updated_count=0,
                image_id="unknown",
                receipt_id=0,
                error=f"{str(e)} - message: {update_msg}",
            )
            results.append(result)

    return results


@trace_compaction_operation(operation_name="label_updates")
def process_label_updates(
    label_updates: List[StreamMessage],
    collection: ChromaDBCollection,
    lock_manager: Optional["LockManager"] = None,
) -> List[LabelUpdateResult]:
    """Process RECEIPT_WORD_LABEL updates for a specific collection.

    Updates label metadata for specific word embeddings in the collection.
    """
    logger.info("Processing label updates", count=len(label_updates))
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]
    # Use the specific collection instead of hardcoded "words"
    database = collection.value
    snapshot_key = f"{database}/snapshot/latest/"

    try:
        # Download current snapshot using atomic helper
        logger.info("Starting atomic snapshot download for label updates",
                   collection=collection.value, bucket=bucket)
        temp_dir = tempfile.mkdtemp()
        download_start_time = time.time()
        
        download_result = download_snapshot_atomic(
            bucket=bucket,
            collection=collection.value,  # "lines" or "words"
            local_path=temp_dir,
            verify_integrity=True,
        )
        
        download_time = time.time() - download_start_time
        logger.info("Atomic snapshot download completed",
                   collection=collection.value, 
                   status=download_result.get("status"),
                   download_time_ms=download_time * 1000,
                   version_id=download_result.get("version_id"))

        if download_result["status"] != "downloaded":
            logger.error("Failed to download snapshot", result=download_result)
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionLabelSnapshotDownloadError", 1, {
                    "collection": collection.value
                })
            return results

        # Load ChromaDB using helper in metadata-only mode
        chroma_client = ChromaDBClient(
            persist_directory=temp_dir,
            mode="read",
            metadata_only=True,  # No embeddings needed for metadata updates
        )

        # Get words collection
        try:
            logger.info("Getting ChromaDB collection for labels", collection=database)
            collection_obj = chroma_client.get_collection(database)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Collection not found for labels", collection=database)
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionLabelCollectionNotFound", 1, {
                    "collection": database
                })
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

                if OBSERVABILITY_AVAILABLE:
                    logger.info("Processing label update",
                               event_name=event_name,
                               chromadb_id=chromadb_id)
                else:
                    logger.info(
                        "Processing %s for label: %s", event_name, chromadb_id
                    )

                if event_name == "REMOVE":
                    updated_count = remove_word_labels(
                        collection_obj, chromadb_id
                    )
                else:  # MODIFY
                    updated_count = update_word_labels(
                        collection_obj, chromadb_id, changes
                    )

                result = LabelUpdateResult(
                    chromadb_id=chromadb_id,
                    updated_count=updated_count,
                    event_name=event_name,
                    changes=list(changes.keys()) if changes else [],
                )
                results.append(result)

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error processing label update", error=str(e))
                
                if OBSERVABILITY_AVAILABLE:
                    metrics.count("CompactionLabelProcessingError", 1, {
                        "error_type": type(e).__name__
                    })
                    
                result = LabelUpdateResult(
                    chromadb_id="unknown",
                    updated_count=0,
                    event_name="unknown",
                    changes=[],
                    error=f"{str(e)} - message: {update_msg}",
                )
                results.append(result)

        # Upload updated snapshot with hash if any updates occurred
        total_updates = sum(
            r.updated_count for r in results if r.error is None
        )
        if total_updates > 0:
            # Validate lock ownership before final S3 upload
            if lock_manager and not lock_manager.validate_ownership():
                error_msg = f"Lock validation failed before label snapshot upload for {collection.value}"
                if OBSERVABILITY_AVAILABLE:
                    logger.error("Lock ownership lost before label snapshot upload",
                               collection=collection.value)
                    metrics.count("CompactionLockValidationFailed", 1, {
                        "collection": collection.value,
                        "operation": "label_upload"
                    })
                else:
                    logger.error(error_msg)
                
                # Replace successful results with error results
                results = [
                    LabelUpdateResult(
                        chromadb_id=result.chromadb_id,
                        updated_count=0,
                        event_name=result.event_name,
                        changes=result.changes,
                        error=error_msg if result.error is None else result.error
                    )
                    for result in results
                ]

                shutil.rmtree(temp_dir, ignore_errors=True)
                return results

            logger.info("Starting atomic snapshot upload for label updates",
                       collection=collection.value, bucket=bucket,
                       total_updates=total_updates)
            upload_start_time = time.time()
            
            upload_result = upload_snapshot_atomic(
                local_path=temp_dir,
                bucket=bucket,
                collection=collection.value,  # "lines" or "words"
                lock_manager=lock_manager,
                metadata={
                    "update_type": "label_update",
                    "total_updates": str(total_updates),
                },
            )
            
            upload_time = time.time() - upload_start_time
            logger.info("Atomic snapshot upload completed",
                       collection=collection.value,
                       status=upload_result.get("status"),
                       upload_time_ms=upload_time * 1000,
                       version_id=upload_result.get("version_id"),
                       hash=upload_result.get("hash"))

            if upload_result["status"] == "uploaded":
                if OBSERVABILITY_AVAILABLE:
                    logger.info("Updated ChromaDB labels",
                               total_updates=total_updates,
                               hash=upload_result.get("hash", "not_calculated"))
                    metrics.count("CompactionLabelSnapshotUploaded", 1, {
                        "collection": database
                    })
                else:
                    logger.info(
                        "Updated %d word labels in ChromaDB, hash: %s",
                        total_updates,
                        upload_result.get("hash", "not_calculated")
                    )
            else:
                logger.error("Failed to upload snapshot", result=upload_result)
                
                if OBSERVABILITY_AVAILABLE:
                    metrics.count("CompactionLabelSnapshotUploadError", 1, {
                        "collection": database
                    })

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error processing label updates", error=str(e))
        
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionLabelUpdatesError", 1, {
                "error_type": type(e).__name__
            })
            
        result = LabelUpdateResult(
            chromadb_id="unknown",
            updated_count=0,
            event_name="unknown",
            changes=[],
            error=str(e),
        )
        results.append(result)
    finally:
        if "temp_dir" in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return results


def update_receipt_metadata(
    collection, image_id: str, receipt_id: int, changes: Dict[str, Any]
) -> int:
    """Update metadata for all embeddings of a specific receipt.
    
    Uses DynamoDB to construct exact ChromaDB IDs instead of scanning entire collection.
    This is much more efficient for large collections.
    """
    start_time = time.time()
    
    if OBSERVABILITY_AVAILABLE:
        logger.info("Starting metadata update",
                   image_id=image_id,
                   receipt_id=receipt_id,
                   changes=changes)
    else:
        logger.info("Starting metadata update", image_id=image_id, receipt_id=receipt_id)
        logger.info("Changes to apply", changes=changes)
    
    # Get DynamoDB client to query for words/lines
    dynamo_client = get_dynamo_client()
    
    # Determine collection type from collection name
    collection_name = collection.name
    
    logger.info("Processing collection", collection_name=collection_name)
    
    # Construct ChromaDB IDs by querying DynamoDB for exact entities
    chromadb_ids = []
    
    if "words" in collection_name:
        # Get all words for this receipt from DynamoDB
        try:
            words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
            
            logger.info("Found words in DynamoDB", count=len(words))
            if OBSERVABILITY_AVAILABLE:
                metrics.gauge("CompactionDynamoDBWords", len(words))
            
            # Construct exact ChromaDB IDs for words
            chromadb_ids = [
                f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                for word in words
            ]
        except Exception as e:
            logger.error("Failed to query words from DynamoDB", error=str(e))
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionDynamoDBQueryError", 1, {
                    "entity_type": "words",
                    "error_type": type(e).__name__
                })
            return 0
            
    elif "lines" in collection_name:
        # Get all lines for this receipt from DynamoDB
        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)
            
            logger.info("Found lines in DynamoDB", count=len(lines))
            if OBSERVABILITY_AVAILABLE:
                metrics.gauge("CompactionDynamoDBLines", len(lines))
            
            # Construct exact ChromaDB IDs for lines
            chromadb_ids = [
                f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"
                for line in lines
            ]
        except Exception as e:
            logger.error("Failed to query lines from DynamoDB", error=str(e))
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionDynamoDBQueryError", 1, {
                    "entity_type": "lines",
                    "error_type": type(e).__name__
                })
            return 0
    else:
        logger.warning("Unknown collection type", collection_name=collection_name)
        
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionUnknownCollectionType", 1, {
                "collection_name": collection_name
            })
        return 0
    
    logger.info("Constructed ChromaDB IDs", count=len(chromadb_ids))
    
    if not chromadb_ids:
        logger.warning("No entities found in DynamoDB",
                      image_id=image_id,
                      receipt_id=receipt_id)
        return 0
    
    # Use direct ID lookup - much faster than scanning entire collection
    try:
        results = collection.get(ids=chromadb_ids, include=["metadatas"])
        found_count = len(results.get("ids", []))
        
        if OBSERVABILITY_AVAILABLE:
            logger.info("Retrieved records from ChromaDB",
                       found_count=found_count,
                       expected_count=len(chromadb_ids))
            metrics.gauge("CompactionChromaDBRecordsFound", found_count)
        else:
            logger.info("Retrieved records from ChromaDB", found_count=found_count, expected_count=len(chromadb_ids))
    except Exception as e:
        logger.error("Failed to query ChromaDB with exact IDs", error=str(e))
        
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionChromaDBQueryError", 1, {
                "error_type": type(e).__name__
            })
        return 0
        
    # Prepare updated metadata for found records
    matching_ids = results.get("ids", [])
    matching_metadatas = []
    
    for i, record_id in enumerate(matching_ids):
        # Get existing metadata and apply changes
        existing_metadata = results["metadatas"][i] or {}
        updated_metadata = existing_metadata.copy()

        # Apply field changes
        for field, change in changes.items():
            old_value = change.get("old")
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
        try:
            collection.update(ids=matching_ids, metadatas=matching_metadatas)
            elapsed_time = time.time() - start_time
            
            if OBSERVABILITY_AVAILABLE:
                logger.info("Successfully updated metadata",
                           updated_count=len(matching_ids),
                           elapsed_seconds=elapsed_time)
                metrics.timer("CompactionMetadataUpdateTime", elapsed_time)
                metrics.count("CompactionMetadataUpdatedRecords", len(matching_ids))
            else:
                logger.info("Successfully updated metadata for embeddings", embedding_count=len(matching_ids), elapsed_seconds=elapsed_time)
        except Exception as e:
            logger.error("Failed to update ChromaDB metadata", error=str(e))
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionChromaDBUpdateError", 1, {
                    "error_type": type(e).__name__
                })
            return 0
    else:
        logger.warning("No matching ChromaDB records found",
                      dynamodb_ids=len(chromadb_ids))
        
        # If no records found in ChromaDB but DynamoDB has entities, this might indicate
        # that embeddings haven't been created yet
        if chromadb_ids:
            logger.info("DynamoDB entities exist but no ChromaDB embeddings found - embeddings may not be created yet")

    elapsed_time = time.time() - start_time
    
    if OBSERVABILITY_AVAILABLE:
        logger.info("Metadata update completed",
                   elapsed_seconds=elapsed_time,
                   approach="DynamoDB-driven")
    else:
        logger.info("Metadata update completed (DynamoDB-driven approach)", elapsed_seconds=elapsed_time)
    return len(matching_ids)


def remove_receipt_metadata(collection, image_id: str, receipt_id: int) -> int:
    """Remove merchant metadata fields from all embeddings of a specific receipt.
    
    Uses DynamoDB to construct exact ChromaDB IDs instead of scanning entire collection.
    """
    start_time = time.time()
    
    if OBSERVABILITY_AVAILABLE:
        logger.info("Starting metadata removal",
                   image_id=image_id,
                   receipt_id=receipt_id)
    else:
        logger.info("Starting metadata removal", image_id=image_id, receipt_id=receipt_id)
    
    # Get DynamoDB client to query for words/lines
    dynamo_client = get_dynamo_client()
    
    # Determine collection type from collection name
    collection_name = collection.name
    
    # Construct ChromaDB IDs by querying DynamoDB for exact entities
    chromadb_ids = []
    
    if "words" in collection_name:
        # Get all words for this receipt from DynamoDB
        try:
            words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
            chromadb_ids = [
                f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                for word in words
            ]
        except Exception as e:
            logger.error("Failed to query words from DynamoDB", error=str(e))
            return 0
            
    elif "lines" in collection_name:
        # Get all lines for this receipt from DynamoDB
        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)
            chromadb_ids = [
                f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"
                for line in lines
            ]
        except Exception as e:
            logger.error("Failed to query lines from DynamoDB", error=str(e))
            return 0
    else:
        logger.warning("Unknown collection type", collection_name=collection_name)
        return 0
    
    if not chromadb_ids:
        logger.warning("No entities found in DynamoDB",
                      image_id=image_id,
                      receipt_id=receipt_id)
        return 0

    # Fields to remove when metadata is deleted
    fields_to_remove = [
        "canonical_merchant_name",
        "merchant_name",
        "merchant_category",
        "address",
        "phone_number",
        "place_id",
    ]

    # Use direct ID lookup instead of scanning entire collection
    try:
        results = collection.get(ids=chromadb_ids, include=["metadatas"])
        found_count = len(results.get("ids", []))
        
        logger.info("Retrieved records from ChromaDB for removal", count=found_count)
    except Exception as e:
        logger.error("Failed to query ChromaDB for metadata removal", error=str(e))
        return 0

    matching_ids = results.get("ids", [])
    matching_metadatas = []

    for i, record_id in enumerate(matching_ids):
        # Remove merchant fields from metadata
        existing_metadata = results["metadatas"][i] or {}
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
        try:
            collection.update(ids=matching_ids, metadatas=matching_metadatas)
            elapsed_time = time.time() - start_time
            
            if OBSERVABILITY_AVAILABLE:
                logger.info("Removed metadata",
                           removed_count=len(matching_ids),
                           elapsed_seconds=elapsed_time)
                metrics.timer("CompactionMetadataRemovalTime", elapsed_time)
                metrics.count("CompactionMetadataRemovedRecords", len(matching_ids))
            else:
                logger.info("Removed metadata from embeddings", embedding_count=len(matching_ids), elapsed_seconds=elapsed_time)
        except Exception as e:
            logger.error("Failed to remove ChromaDB metadata", error=str(e))
            return 0
    else:
        logger.warning("No matching ChromaDB records found for removal",
                      dynamodb_ids=len(chromadb_ids))

    elapsed_time = time.time() - start_time
    
    if OBSERVABILITY_AVAILABLE:
        logger.info("Metadata removal completed",
                   elapsed_seconds=elapsed_time,
                   approach="DynamoDB-driven")
    else:
        logger.info("Metadata removal completed (DynamoDB-driven approach)", elapsed_seconds=elapsed_time)
    return len(matching_ids)


def reconstruct_label_metadata(
    image_id: str, 
    receipt_id: int, 
    line_id: int, 
    word_id: int,
    dynamo_client
) -> Dict[str, Any]:
    """
    Reconstruct all label-related metadata fields exactly as the step function does.
    
    Args:
        image_id: Image ID
        receipt_id: Receipt ID  
        line_id: Line ID
        word_id: Word ID
        dynamo_client: DynamoDB client instance
        
    Returns:
        Dictionary with reconstructed label metadata fields:
        - validated_labels: comma-delimited string of valid labels
        - invalid_labels: comma-delimited string of invalid labels  
        - label_status: overall status (validated/auto_suggested/unvalidated)
        - label_confidence: confidence from latest pending label
        - label_proposed_by: proposer of latest pending label
        - label_validated_at: timestamp of most recent validation
    """
    from receipt_dynamo.constants import ValidationStatus
    
    # Get all labels for this specific word directly
    word_labels, _ = dynamo_client.list_receipt_word_labels_for_word(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        limit=None  # Get all labels for this word
    )
    
    # Calculate label_status - overall state for this word
    if any(
        lbl.validation_status == ValidationStatus.VALID.value
        for lbl in word_labels
    ):
        label_status = "validated"
    elif any(
        lbl.validation_status == ValidationStatus.PENDING.value
        for lbl in word_labels
    ):
        label_status = "auto_suggested"
    else:
        label_status = "unvalidated"
    
    # Get auto suggestions for confidence and proposed_by
    auto_suggestions = [
        lbl for lbl in word_labels
        if lbl.validation_status == ValidationStatus.PENDING.value
    ]
    
    # label_confidence & label_proposed_by from latest auto suggestion
    if auto_suggestions:
        latest = sorted(auto_suggestions, key=lambda l: l.timestamp_added)[-1]
        label_confidence = getattr(latest, "confidence", None)
        label_proposed_by = latest.label_proposed_by
    else:
        label_confidence = None
        label_proposed_by = None
    
    # validated_labels - all labels with status VALID
    validated_labels = [
        lbl.label for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]
    
    # invalid_labels - all labels with status INVALID
    invalid_labels = [
        lbl.label for lbl in word_labels
        if lbl.validation_status == ValidationStatus.INVALID.value
    ]
    
    # label_validated_at - timestamp of most recent VALID label
    valid_labels = [
        lbl for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]
    label_validated_at = (
        sorted(valid_labels, key=lambda l: l.timestamp_added)[-1].timestamp_added
        if valid_labels
        else None
    )
    
    # Build metadata dictionary matching step function structure
    label_metadata = {
        "label_status": label_status,
    }
    
    # Add optional fields only if they have values
    if label_confidence is not None:
        label_metadata["label_confidence"] = label_confidence
    if label_proposed_by is not None:
        label_metadata["label_proposed_by"] = label_proposed_by
    
    # Store validated labels with delimiters for exact matching
    if validated_labels:
        label_metadata["validated_labels"] = f",{','.join(validated_labels)},"
    else:
        label_metadata["validated_labels"] = ""
    
    # Store invalid labels with delimiters for exact matching  
    if invalid_labels:
        label_metadata["invalid_labels"] = f",{','.join(invalid_labels)},"
    else:
        label_metadata["invalid_labels"] = ""
    
    if label_validated_at is not None:
        label_metadata["label_validated_at"] = label_validated_at
    
    return label_metadata


def update_word_labels(
    collection, chromadb_id: str, changes: Dict[str, Any]
) -> int:
    """Update label metadata for a specific word embedding using proper metadata structure."""
    try:
        # Parse ChromaDB ID to extract word identifiers
        # Format: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
        parts = chromadb_id.split("#")
        if len(parts) < 8 or "WORD" not in parts:
            logger.error("Invalid ChromaDB ID format for word", chromadb_id=chromadb_id)
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionInvalidChromaDBID", 1)
            return 0
            
        image_id = parts[1]
        receipt_id = int(parts[3])
        line_id = int(parts[5])
        word_id = int(parts[7])
        
        # Get the specific record from ChromaDB
        result = collection.get(ids=[chromadb_id], include=["metadatas"])
        if not result["ids"]:
            logger.warning("Word embedding not found", chromadb_id=chromadb_id)
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionWordEmbeddingNotFound", 1)
            return 0

        # Get DynamoDB client
        dynamo_client = get_dynamo_client()
        
        # Reconstruct complete label metadata using the same logic as step function
        reconstructed_metadata = reconstruct_label_metadata(
            image_id=image_id,
            receipt_id=receipt_id, 
            line_id=line_id,
            word_id=word_id,
            dynamo_client=dynamo_client
        )
        
        # Get existing metadata and update with reconstructed label fields
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()
        
        # Update with all reconstructed label fields (using same field names as step function)
        updated_metadata.update(reconstructed_metadata)
        
        # Add update timestamp
        updated_metadata["last_label_update"] = datetime.now(timezone.utc).isoformat()
        
        # Update the ChromaDB record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])
        
        logger.info("Updated labels for word with reconstructed metadata", 
                   chromadb_id=chromadb_id,
                   label_status=reconstructed_metadata.get("label_status"),
                   validated_labels_count=len(reconstructed_metadata.get("validated_labels", "").split(",")) - 2 if reconstructed_metadata.get("validated_labels") else 0)
        
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionWordLabelUpdated", 1)
            metrics.gauge("CompactionValidatedLabelsCount", 
                         len(reconstructed_metadata.get("validated_labels", "").split(",")) - 2 if reconstructed_metadata.get("validated_labels") else 0)
        
        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error updating word labels",
                    chromadb_id=chromadb_id,
                    error=str(e))
                    
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionWordLabelUpdateError", 1, {
                "error_type": type(e).__name__
            })
        return 0


def remove_word_labels(collection, chromadb_id: str) -> int:
    """Remove label metadata from a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            logger.warning("Word embedding not found", chromadb_id=chromadb_id)
            
            if OBSERVABILITY_AVAILABLE:
                metrics.count("CompactionWordEmbeddingNotFoundForRemoval", 1)
            return 0

        # Remove all label fields from metadata
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()

        # Remove all label-related fields (matching step function structure)
        label_fields_to_remove = [
            "label_status",
            "label_confidence", 
            "label_proposed_by",
            "validated_labels",
            "invalid_labels",
            "label_validated_at",
            # Also remove any legacy prefixed fields for backward compatibility
        ]
        
        # Remove standard label fields
        for field in label_fields_to_remove:
            if field in updated_metadata:
                del updated_metadata[field]
        
        # Remove any remaining fields that start with "label_" (legacy cleanup)
        legacy_label_fields = [
            key for key in updated_metadata.keys() if key.startswith("label_")
        ]
        for field in legacy_label_fields:
            del updated_metadata[field]

        # Add removal timestamp
        updated_metadata["labels_removed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        # Update the record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])

        logger.info("Removed labels from word", chromadb_id=chromadb_id)
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionWordLabelRemoved", 1)
        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error removing word labels",
                    chromadb_id=chromadb_id,
                    error=str(e))
                    
        if OBSERVABILITY_AVAILABLE:
            metrics.count("CompactionWordLabelRemovalError", 1, {
                "error_type": type(e).__name__
            })
        return 0


def process_delta_messages(
    delta_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process traditional delta file messages.

    Currently not implemented - this handler focuses on DynamoDB
    stream messages. Traditional delta processing would be handled
    by a separate compaction system.
    """
    if OBSERVABILITY_AVAILABLE:
        logger.info("Received delta messages (not processed)", count=len(delta_messages))
        logger.warning("Delta message processing not implemented")
        metrics.count("CompactionDeltaMessagesSkipped", len(delta_messages))
    else:
        logger.info(
            "Received %d delta messages (not processed)", len(delta_messages)
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


# Alias for consistent naming with other handlers
handle = lambda_handler