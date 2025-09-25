"""
DynamoDB Stream Processor Lambda for ChromaDB Compaction Integration

This module defines the Lambda function that processes DynamoDB stream events
for receipt metadata and word label changes, triggering ChromaDB metadata
updates through the existing compaction SQS queue.

Focuses on:
- RECEIPT_METADATA entities (merchant info changes)
- RECEIPT_WORD_LABEL entities (word label changes)
- Both MODIFY and REMOVE operations
"""

# pylint: disable=duplicate-code,too-many-locals,too-many-branches
# Some duplication with enhanced_compaction_handler is expected for shared
# structures. Stream processing requires handling many event types and validation conditions

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import boto3

# Enhanced observability imports (with fallback)
try:
    from utils import (
        get_operation_logger,
        metrics,
        trace_function,
        start_compaction_lambda_monitoring,
        stop_compaction_lambda_monitoring,
        with_compaction_timeout_protection,
        format_response,
    )

    OBSERVABILITY_AVAILABLE = True
except ImportError as e:
    # Log the specific import error for debugging
    import logging

    logging.error(f"Failed to import utils: {e}")
    # Fallback for development/testing - provide no-op decorators
    OBSERVABILITY_AVAILABLE = False

    # No-op decorator functions for fallback
    def trace_function(operation_name=None, collection=None):
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

# Import receipt_dynamo entity parsers
from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    item_to_receipt_metadata,
)
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
)
from receipt_dynamo.entities.compaction_run import (
    CompactionRun,
    item_to_compaction_run,
)

# Define ChromaDBCollection enum locally since it might not be in the layer
from enum import Enum


class ChromaDBCollection(str, Enum):
    """ChromaDB collection types for receipt embeddings."""

    LINES = "lines"
    WORDS = "words"


@dataclass(frozen=True)
class LambdaResponse:
    """Response from the Lambda handler with processing statistics."""

    status_code: int
    processed_records: int
    queued_messages: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to AWS Lambda-compatible dictionary."""
        return {
            "statusCode": self.status_code,
            "processed_records": self.processed_records,
            "queued_messages": self.queued_messages,
        }


@dataclass(frozen=True)
class ParsedStreamRecord:
    """Parsed DynamoDB stream record with entity information."""

    entity_type: str  # "RECEIPT_METADATA" or "RECEIPT_WORD_LABEL"
    old_entity: Optional[Union[ReceiptMetadata, ReceiptWordLabel]]
    new_entity: Optional[Union[ReceiptMetadata, ReceiptWordLabel]]
    pk: str
    sk: str


@dataclass(frozen=True)
class StreamMessage:  # pylint: disable=too-many-instance-attributes
    """Enhanced stream message with collection targeting."""

    entity_type: str
    entity_data: Dict[str, Any]
    changes: Dict[str, Any]
    event_name: str
    collections: List[ChromaDBCollection]  # Which collections this affects
    source: str = "dynamodb_stream"
    timestamp: Optional[str] = None
    stream_record_id: Optional[str] = None
    aws_region: Optional[str] = None


@dataclass(frozen=True)
class FieldChange:
    """Represents a change in a single field."""

    old: Any
    new: Any


# Configure logging with observability
if OBSERVABILITY_AVAILABLE:
    logger = get_operation_logger(__name__)
else:
    # Pure stdlib fallback logger (avoid any utils dependency)
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@trace_function(operation_name="stream_processor")
@with_compaction_timeout_protection(
    max_duration=60
)  # Stream processing should be fast
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process DynamoDB stream events for ChromaDB metadata synchronization.

    This function is lightweight and focuses only on:
    1. Parsing stream events for relevant entities
    2. Detecting ChromaDB-relevant field changes
    3. Creating SQS messages for the compaction Lambda

    Heavy processing is delegated to the compaction Lambda.

    Args:
        event: DynamoDB stream event
        context: Lambda context

    Returns:
        Response with processing statistics
    """
    correlation_id = None

    # Start monitoring if available
    if OBSERVABILITY_AVAILABLE:
        start_compaction_lambda_monitoring(context)
        correlation_id = getattr(logger, "correlation_id", None)
        logger.info(
            "Starting stream processing",
            event_records=len(event.get("Records", [])),
            correlation_id=correlation_id,
        )

    try:
        # Handle different event types (test events vs real stream events)
        if "Records" not in event:
            logger.info(
                "Received non-stream event (likely test)",
                event_keys=list(event.keys()),
            )
            response = {
                "statusCode": 200,
                "processed_records": 0,
                "queued_messages": 0,
            }

            if OBSERVABILITY_AVAILABLE:
                metrics.count("StreamProcessorTestEvents", 1)
                return format_response(
                    response, event, correlation_id=correlation_id
                )
            return response

        if OBSERVABILITY_AVAILABLE:
            metrics.gauge("StreamRecordsReceived", len(event["Records"]))

        logger.info(
            "Processing DynamoDB stream records",
            record_count=len(event["Records"]),
        )

        # Avoid logging entire event to prevent PII exposure
        if hasattr(logger, "logger") and logger.logger.level <= logging.DEBUG:
            logger.debug("Stream event structure present")

        messages_to_send = []
        processed_records = 0

        for record in event["Records"]:
            try:
                event_id = record.get("eventID", "unknown")
                event_name = record.get("eventName", "unknown")

                logger.info(
                    "Processing stream record",
                    record_id=event_id,
                    event_name=event_name,
                )

                if OBSERVABILITY_AVAILABLE:
                    metrics.count(
                        "StreamRecordProcessed", 1, {"event_name": event_name}
                    )
                # Fast-path: enqueue compaction jobs on COMPACTION_RUN inserts
                if event_name == "INSERT":
                    new_image = record.get("dynamodb", {}).get("NewImage")
                    keys = record.get("dynamodb", {}).get("Keys", {})
                    pk = keys.get("PK", {}).get("S", "")
                    sk = keys.get("SK", {}).get("S", "")
                    if new_image and _is_compaction_run(pk, sk):
                        try:
                            compaction_run = _parse_compaction_run(
                                new_image, pk, sk
                            )
                        except (
                            Exception
                        ) as e:  # pylint: disable=broad-exception-caught
                            logger.error(
                                "Failed to parse COMPACTION_RUN",
                                record_id=event_id,
                                error=str(e),
                            )
                            continue

                        # Build two messages: one for lines, one for words
                        cr_entity = {
                            "run_id": compaction_run.run_id,
                            "image_id": compaction_run.image_id,
                            "receipt_id": compaction_run.receipt_id,
                            "lines_delta_prefix": compaction_run.lines_delta_prefix,
                            "words_delta_prefix": compaction_run.words_delta_prefix,
                        }

                        for collection, prefix_key in (
                            (ChromaDBCollection.LINES, "lines_delta_prefix"),
                            (ChromaDBCollection.WORDS, "words_delta_prefix"),
                        ):
                            stream_msg = StreamMessage(
                                entity_type="COMPACTION_RUN",
                                entity_data={
                                    **cr_entity,
                                    "delta_s3_prefix": cr_entity[prefix_key],
                                },
                                changes={},
                                event_name=event_name,
                                collections=[collection],
                                source="dynamodb_stream",
                                timestamp=datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                stream_record_id=event_id,
                                aws_region=record.get("awsRegion", "unknown"),
                            )
                            messages_to_send.append(stream_msg)

                        processed_records += 1
                        logger.info(
                            "Enqueued compaction run messages",
                            run_id=compaction_run.run_id,
                            image_id=compaction_run.image_id,
                            receipt_id=compaction_run.receipt_id,
                        )
                        # Skip normal entity parsing for this record
                        continue

                # Parse stream record using entity parsers
                parsed_record = parse_stream_record(record)

                if not parsed_record:
                    if OBSERVABILITY_AVAILABLE:
                        metrics.count(
                            "StreamRecordSkipped",
                            1,
                            {"reason": "not_relevant_entity"},
                        )
                    logger.debug(
                        "Skipped record - not relevant entity",
                        record_id=event_id,
                        pk=record.get("dynamodb", {})
                        .get("Keys", {})
                        .get("PK", {})
                        .get("S", "unknown"),
                        sk=record.get("dynamodb", {})
                        .get("Keys", {})
                        .get("SK", {})
                        .get("S", "unknown"),
                    )
                    continue  # Not a receipt entity we care about

                # Log successful entity detection
                logger.info(
                    "Successfully parsed stream record",
                    record_id=event_id,
                    entity_type=parsed_record.entity_type,
                    has_old_entity=parsed_record.old_entity is not None,
                    has_new_entity=parsed_record.new_entity is not None,
                    pk=parsed_record.pk,
                    sk=parsed_record.sk,
                )

                # Process MODIFY and REMOVE events
                if record["eventName"] in ["MODIFY", "REMOVE"]:
                    entity_type = parsed_record.entity_type
                    old_entity = parsed_record.old_entity
                    new_entity = parsed_record.new_entity

                    # Check if any ChromaDB-relevant fields changed
                    changes = get_chromadb_relevant_changes(
                        entity_type, old_entity, new_entity
                    )
                    logger.info(
                        "Found relevant changes",
                        change_count=len(changes),
                        changed_fields=list(changes.keys()),
                        entity_type=entity_type,
                    )

                    if OBSERVABILITY_AVAILABLE:
                        metrics.count(
                            "ChromaDBRelevantChanges",
                            len(changes),
                            {"entity_type": entity_type},
                        )

                    # Always process REMOVE events, even without specific field
                    # changes
                    if changes or record["eventName"] == "REMOVE":
                        # Extract entity identification data
                        entity = old_entity or new_entity
                        entity_data = None
                        target_collections = []

                        if entity_type == "RECEIPT_METADATA":
                            # Metadata changes affect both collections
                            entity_data = {
                                "entity_type": entity_type,
                                "image_id": entity.image_id,
                                "receipt_id": entity.receipt_id,
                            }
                            target_collections = [
                                ChromaDBCollection.LINES,
                                ChromaDBCollection.WORDS,
                            ]
                        elif entity_type == "RECEIPT_WORD_LABEL":
                            # Word label changes only affect words collection
                            entity_data = {
                                "entity_type": entity_type,
                                "image_id": entity.image_id,
                                "receipt_id": entity.receipt_id,
                                "line_id": entity.line_id,
                                "word_id": entity.word_id,
                                "label": entity.label,
                            }
                            target_collections = [ChromaDBCollection.WORDS]

                        if entity_data and target_collections:
                            # Create enhanced stream message
                            stream_msg = StreamMessage(
                                entity_type=entity_type,
                                entity_data=entity_data,
                                changes=changes,
                                event_name=record["eventName"],
                                collections=target_collections,
                                source="dynamodb_stream",
                                timestamp=datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                stream_record_id=record.get(
                                    "eventID", "unknown"
                                ),
                                aws_region=record.get("awsRegion", "unknown"),
                            )
                            messages_to_send.append(stream_msg)
                            processed_records += 1

                            logger.info(
                                "Created stream message",
                                entity_type=entity_type,
                                target_collections=[
                                    c.value for c in target_collections
                                ],
                            )

                            if OBSERVABILITY_AVAILABLE:
                                for collection in target_collections:
                                    metrics.count(
                                        "StreamMessageCreated",
                                        1,
                                        {
                                            "entity_type": entity_type,
                                            "collection": collection.value,
                                        },
                                    )

            except (ValueError, KeyError, TypeError) as e:
                event_id = record.get("eventID", "unknown")
                logger.error(
                    "Error processing stream record",
                    record_id=event_id,
                    error=str(e),
                )

                if OBSERVABILITY_AVAILABLE:
                    metrics.count(
                        "StreamRecordProcessingError",
                        1,
                        {"error_type": type(e).__name__},
                    )
                # Continue processing other records

        # Send all messages to appropriate SQS queues
        if messages_to_send:
            sent_count = send_messages_to_queues(messages_to_send)
            logger.info(
                "Sent messages to compaction queues", message_count=sent_count
            )

            if OBSERVABILITY_AVAILABLE:
                metrics.count("MessagesQueuedForCompaction", sent_count)
        else:
            sent_count = 0

        response = LambdaResponse(
            status_code=200,
            processed_records=processed_records,
            queued_messages=sent_count,
        )

        logger.info(
            "Stream processing completed",
            processed_records=processed_records,
            queued_messages=sent_count,
        )

        if OBSERVABILITY_AVAILABLE:
            metrics.gauge("StreamProcessorProcessedRecords", processed_records)
            metrics.gauge("StreamProcessorQueuedMessages", sent_count)

        # Convert dataclass to dict for AWS Lambda JSON serialization
        result = response.to_dict()

        if OBSERVABILITY_AVAILABLE:
            return format_response(
                result, event, correlation_id=correlation_id
            )
        return result

    except Exception as e:
        logger.error(
            "Stream processor failed",
            error=str(e),
            error_type=type(e).__name__,
        )

        if OBSERVABILITY_AVAILABLE:
            metrics.count(
                "StreamProcessorError", 1, {"error_type": type(e).__name__}
            )

        error_response = {
            "statusCode": 500,
            "processed_records": 0,
            "queued_messages": 0,
            "error": str(e),
        }

        if OBSERVABILITY_AVAILABLE:
            return format_response(
                error_response,
                event,
                is_error=True,
                correlation_id=correlation_id,
            )
        return error_response

    finally:
        # Stop monitoring if available
        if OBSERVABILITY_AVAILABLE:
            stop_compaction_lambda_monitoring()


def _detect_entity_type(sk: str) -> Optional[str]:
    """
    Detect entity type from SK pattern.

    Args:
        sk: Sort key from DynamoDB item

    Returns:
        Entity type string or None if not relevant
    """
    if "#METADATA" in sk:
        return "RECEIPT_METADATA"
    if "#LABEL#" in sk:
        return "RECEIPT_WORD_LABEL"
    if "#COMPACTION_RUN#" in sk:
        return "COMPACTION_RUN"
    return None


def _is_compaction_run(pk: str, sk: str) -> bool:
    """Detect if PK/SK correspond to a CompactionRun item."""
    return pk.startswith("IMAGE#") and "#COMPACTION_RUN#" in sk


def _parse_compaction_run(
    new_image: Dict[str, Any], pk: str, sk: str
) -> CompactionRun:
    """Parse NewImage into a CompactionRun using shared parser."""
    complete_item = dict(new_image)
    complete_item["PK"] = {"S": pk}
    complete_item["SK"] = {"S": sk}
    # TYPE provided in item
    if "TYPE" not in complete_item:
        complete_item["TYPE"] = {"S": "COMPACTION_RUN"}
    return item_to_compaction_run(complete_item)


def _parse_entity(
    image: Optional[Dict[str, Any]],
    entity_type: str,
    image_type: str,
    pk: str,
    sk: str,
) -> Optional[Union[ReceiptMetadata, ReceiptWordLabel]]:
    """
    Parse DynamoDB image into typed entity.

    Args:
        image: DynamoDB image (OldImage or NewImage)
        entity_type: Type of entity to parse
        image_type: Description for logging (old/new)
        pk: Primary key for the item
        sk: Sort key for the item

    Returns:
        Parsed entity or None if parsing fails
    """
    if not image:
        return None

    try:
        # The entity parsers expect a complete DynamoDB item with PK, SK, and other required fields
        # The stream image only contains the item attributes, so we need to add the missing keys
        complete_item = dict(image)  # Copy the image
        complete_item["PK"] = {"S": pk}
        complete_item["SK"] = {"S": sk}

        # Log detailed field information for RECEIPT_WORD_LABEL parsing diagnostics
        if entity_type == "RECEIPT_WORD_LABEL":
            logger.info(
                "Attempting to parse RECEIPT_WORD_LABEL",
                image_type=image_type,
                available_fields=list(complete_item.keys()),
                pk=pk,
                sk=sk,
                has_timestamp_added="timestamp_added" in complete_item,
                has_reasoning="reasoning" in complete_item,
                has_validation_status="validation_status" in complete_item,
                raw_image_fields=list(image.keys()) if image else "None",
            )

        if entity_type == "RECEIPT_METADATA":
            return item_to_receipt_metadata(complete_item)
        if entity_type == "RECEIPT_WORD_LABEL":
            return item_to_receipt_word_label(complete_item)
    except ValueError as e:
        logger.error(
            "Failed to parse entity - DIAGNOSTIC DETAILS",
            image_type=image_type,
            entity_type=entity_type,
            error=str(e),
            available_fields=list(image.keys()) if image else "None",
            complete_item_fields=(
                list(complete_item.keys())
                if "complete_item" in locals()
                else "Not created"
            ),
            pk=pk,
            sk=sk,
            raw_complete_item=(
                complete_item if "complete_item" in locals() else "Not created"
            ),
        )

        if OBSERVABILITY_AVAILABLE:
            metrics.count(
                "EntityParsingError",
                1,
                {"entity_type": entity_type, "image_type": image_type},
            )

    except Exception as e:
        logger.error(
            "Unexpected error parsing entity",
            image_type=image_type,
            entity_type=entity_type,
            error=str(e),
            error_type=type(e).__name__,
            pk=pk,
            sk=sk,
        )

        if OBSERVABILITY_AVAILABLE:
            metrics.count(
                "EntityParsingUnexpectedError",
                1,
                {"entity_type": entity_type, "error_type": type(e).__name__},
            )

    return None


def parse_stream_record(
    record: Dict[str, Any],
) -> Optional[ParsedStreamRecord]:
    """
    Parse DynamoDB stream record to identify relevant entity changes.

    Uses receipt_dynamo entity parsers for proper validation and type safety.
    Only processes entities that affect ChromaDB metadata:
    - RECEIPT_METADATA: merchant info that affects all embeddings
    - RECEIPT_WORD_LABEL: labels that affect specific word embeddings

    Args:
        record: DynamoDB stream record

    Returns:
        Dictionary with parsed entity info or None if not relevant
    """
    try:
        # Extract keys to determine entity type
        keys = record["dynamodb"]["Keys"]
        pk = keys["PK"]["S"]
        sk = keys["SK"]["S"]

        # Only process IMAGE entities
        if not pk.startswith("IMAGE#"):
            return None

        # Determine entity type from SK pattern
        entity_type = _detect_entity_type(sk)
        if not entity_type:
            return None  # Not a relevant entity type

        # Get appropriate DynamoDB item for parsing
        old_image = record["dynamodb"].get("OldImage")
        new_image = record["dynamodb"].get("NewImage")

        # Parse entities using receipt_dynamo parsers via helper function
        old_entity = _parse_entity(old_image, entity_type, "old", pk, sk)
        new_entity = _parse_entity(new_image, entity_type, "new", pk, sk)

        # Enhanced diagnostic logging for parsing failures
        if old_image and not old_entity:
            logger.error(
                "CRITICAL: Failed to parse old entity - FULL DIAGNOSTIC",
                entity_type=entity_type,
                available_keys=list(old_image.keys()) if old_image else "None",
                pk=pk,
                sk=sk,
                full_old_image=(
                    old_image
                    if entity_type == "RECEIPT_WORD_LABEL"
                    else "Not a label entity"
                ),
            )
        if new_image and not new_entity:
            logger.error(
                "CRITICAL: Failed to parse new entity - FULL DIAGNOSTIC",
                entity_type=entity_type,
                available_keys=list(new_image.keys()) if new_image else "None",
                pk=pk,
                sk=sk,
                full_new_image=(
                    new_image
                    if entity_type == "RECEIPT_WORD_LABEL"
                    else "Not a label entity"
                ),
            )

        # Return parsed entity information
        return ParsedStreamRecord(
            entity_type=entity_type,
            old_entity=old_entity,
            new_entity=new_entity,
            pk=pk,
            sk=sk,
        )

    except (KeyError, ValueError) as e:
        logger.warning("Failed to parse stream record", error=str(e))

        if OBSERVABILITY_AVAILABLE:
            metrics.count("StreamRecordParsingError", 1)
        return None


def get_chromadb_relevant_changes(
    entity_type: str,
    old_entity: Optional[Union[ReceiptMetadata, ReceiptWordLabel]],
    new_entity: Optional[Union[ReceiptMetadata, ReceiptWordLabel]],
) -> Dict[str, FieldChange]:
    """
    Identify changes to fields that affect ChromaDB metadata.

    Uses typed entity objects for robust field access and comparison.

    Args:
        entity_type: Type of entity (RECEIPT_METADATA or RECEIPT_WORD_LABEL)
        old_entity: Previous entity state (ReceiptMetadata or ReceiptWordLabel)
        new_entity: Current entity state (None for REMOVE events)

    Returns:
        Dictionary mapping field names to FieldChange objects with
        old/new values
    """
    # Define ChromaDB-relevant fields for each entity type
    relevant_fields = {
        "RECEIPT_METADATA": [
            "canonical_merchant_name",
            "merchant_name",
            "merchant_category",
            "address",
            "phone_number",
            "place_id",
        ],
        "RECEIPT_WORD_LABEL": [
            "label",
            "reasoning",
            "validation_status",
            "label_proposed_by",
            "label_consolidated_from",
        ],
    }

    fields_to_check = relevant_fields.get(entity_type, [])
    changes = {}

    for field in fields_to_check:
        # Use getattr for safe attribute access on typed objects
        old_value = getattr(old_entity, field, None) if old_entity else None
        new_value = getattr(new_entity, field, None) if new_entity else None

        if old_value != new_value:
            changes[field] = FieldChange(old=old_value, new=new_value)

    return changes


def send_messages_to_queues(messages: List[StreamMessage]) -> int:
    """
    Send messages to appropriate collection-specific SQS queues.

    Each message is sent to the queue(s) for the collections it affects:
    - RECEIPT_METADATA messages go to both lines and words queues
    - RECEIPT_WORD_LABEL messages go only to words queue

    Args:
        messages: List of StreamMessage objects to send

    Returns:
        Total number of messages successfully sent across all queues
    """
    sqs = boto3.client("sqs")
    sent_count = 0

    # Group messages by target collections
    lines_messages = []
    words_messages = []

    for msg in messages:
        # Convert StreamMessage to dictionary for JSON serialization
        # Convert FieldChange objects to dictionaries for JSON serialization
        changes_dict = {}
        for field_name, field_change in msg.changes.items():
            changes_dict[field_name] = {
                "old": field_change.old,
                "new": field_change.new,
            }

        msg_dict = {
            "source": msg.source,
            "entity_type": msg.entity_type,
            "entity_data": msg.entity_data,
            "changes": changes_dict,
            "event_name": msg.event_name,
            "timestamp": msg.timestamp,
            "stream_record_id": msg.stream_record_id,
            "aws_region": msg.aws_region,
        }

        if ChromaDBCollection.LINES in msg.collections:
            lines_messages.append((msg_dict, ChromaDBCollection.LINES))
        if ChromaDBCollection.WORDS in msg.collections:
            words_messages.append((msg_dict, ChromaDBCollection.WORDS))

    # Send to lines queue
    if lines_messages:
        sent_count += _send_batch_to_queue(
            sqs, lines_messages, "LINES_QUEUE_URL", ChromaDBCollection.LINES
        )

    # Send to words queue
    if words_messages:
        sent_count += _send_batch_to_queue(
            sqs, words_messages, "WORDS_QUEUE_URL", ChromaDBCollection.WORDS
        )

    return sent_count


def _send_batch_to_queue(
    sqs,
    messages: List[tuple],
    queue_env_var: str,
    collection: ChromaDBCollection,
) -> int:
    """
    Send a batch of messages to a specific queue.

    Args:
        sqs: boto3 SQS client
        messages: List of (message_dict, collection) tuples
        queue_env_var: Environment variable containing the queue URL
        collection: ChromaDBCollection this queue serves

    Returns:
        Number of messages successfully sent
    """
    sent_count = 0
    queue_url = os.environ.get(queue_env_var)

    if not queue_url:
        logger.error("Queue URL not found", queue_env_var=queue_env_var)
        return 0

    # Send in batches of 10 (SQS batch limit)
    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]

        entries = []
        for j, (message_dict, _) in enumerate(batch):
            entity_type = message_dict.get("entity_type", "UNKNOWN")
            entity_data = message_dict.get("entity_data", {})
            # Prefer run_id for compaction runs; fall back to receipt_id, then image_id
            group_key = (
                entity_data.get("run_id")
                or entity_data.get("receipt_id")
                or entity_data.get("image_id")
                or "default"
            )
            message_group_id = f"{entity_type}:{group_key}:{collection.value}"

            entries.append(
                {
                    "Id": str(i + j),
                    "MessageBody": json.dumps(message_dict),
                    "MessageGroupId": message_group_id,
                    "MessageAttributes": {
                        "source": {
                            "StringValue": "dynamodb_stream",
                            "DataType": "String",
                        },
                        "entity_type": {
                            "StringValue": message_dict["entity_type"],
                            "DataType": "String",
                        },
                        "event_name": {
                            "StringValue": message_dict["event_name"],
                            "DataType": "String",
                        },
                        "collection": {
                            "StringValue": collection.value,
                            "DataType": "String",
                        },
                    },
                }
            )

        try:
            logger.info(
                "Sending message batch to queue",
                queue_url=queue_url,
                collection=collection.value,
                batch_size=len(entries),
            )

            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )

            # Count successful sends
            successful = len(response.get("Successful", []))
            sent_count += successful

            logger.info(
                "Sent messages to queue",
                successful_count=successful,
                collection=collection.value,
            )

            if OBSERVABILITY_AVAILABLE:
                metrics.count(
                    "SQSMessagesSuccessful",
                    successful,
                    {"collection": collection.value},
                )

            # Log any failures
            if "Failed" in response and response["Failed"]:
                failed_count = len(response["Failed"])

                if OBSERVABILITY_AVAILABLE:
                    metrics.count(
                        "SQSMessagesFailed",
                        failed_count,
                        {"collection": collection.value},
                    )

                for failed in response["Failed"]:
                    logger.error(
                        "Failed to send message to queue",
                        message_id=failed["Id"],
                        collection=collection.value,
                        error_code=failed.get("Code", "UnknownError"),
                        error_message=failed.get(
                            "Message", "No error details"
                        ),
                    )

        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Error sending SQS batch to queue",
                collection=collection.value,
                error=str(e),
            )

            if OBSERVABILITY_AVAILABLE:
                metrics.count(
                    "SQSBatchError",
                    1,
                    {
                        "collection": collection.value,
                        "error_type": type(e).__name__,
                    },
                )

    return sent_count
