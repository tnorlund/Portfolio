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

# Import receipt_dynamo entity parsers
from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    item_to_receipt_metadata,
)
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
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


# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
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
    # Handle different event types (test events vs real stream events)
    if "Records" not in event:
        logger.info(
            "Received non-stream event (likely test): %s", list(event.keys())
        )
        return {
            "statusCode": 200,
            "processed_records": 0,
            "queued_messages": 0,
        }

    logger.info("event: %s", event)
    logger.info("Processing %s DynamoDB stream records", len(event["Records"]))

    # Avoid logging entire event to prevent PII exposure
    if logger.level <= logging.DEBUG:
        logger.debug("Stream event structure present")

    messages_to_send = []
    processed_records = 0

    for record in event["Records"]:
        try:
            logger.info(
                "Processing record: %s", record.get("eventID", "unknown")
            )
            logger.info("Event Name: %s", record.get("eventName", "unknown"))
            # Parse stream record using entity parsers
            parsed_record = parse_stream_record(record)

            if not parsed_record:
                continue  # Not a receipt entity we care about

            # Process MODIFY and REMOVE events
            if record["eventName"] in ["MODIFY", "REMOVE"]:
                entity_type = parsed_record.entity_type
                old_entity = parsed_record.old_entity
                new_entity = parsed_record.new_entity

                # Check if any ChromaDB-relevant fields changed
                changes = get_chromadb_relevant_changes(
                    entity_type, old_entity, new_entity
                )
                logger.info("Found %d relevant changes: %s", len(changes), list(changes.keys()))

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
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            stream_record_id=record.get("eventID", "unknown"),
                            aws_region=record.get("awsRegion", "unknown"),
                        )
                        messages_to_send.append(stream_msg)
                        processed_records += 1

        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Error processing stream record %s: %s",
                record.get("eventID", "unknown"),
                e,
            )
            # Continue processing other records

    # Send all messages to appropriate SQS queues
    if messages_to_send:
        sent_count = send_messages_to_queues(messages_to_send)
        logger.info("Sent %s messages to compaction queues", sent_count)

    response = LambdaResponse(
        status_code=200,
        processed_records=processed_records,
        queued_messages=len(messages_to_send),
    )

    # Convert dataclass to dict for AWS Lambda JSON serialization
    return response.to_dict()


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
    return None


def _parse_entity(
    image: Optional[Dict[str, Any]], entity_type: str, image_type: str
) -> Optional[Union[ReceiptMetadata, ReceiptWordLabel]]:
    """
    Parse DynamoDB image into typed entity.

    Args:
        image: DynamoDB image (OldImage or NewImage)
        entity_type: Type of entity to parse
        image_type: Description for logging (old/new)

    Returns:
        Parsed entity or None if parsing fails
    """
    if not image:
        return None

    try:
        if entity_type == "RECEIPT_METADATA":
            return item_to_receipt_metadata(image)
        if entity_type == "RECEIPT_WORD_LABEL":
            return item_to_receipt_word_label(image)
    except ValueError as e:
        logger.warning("Failed to parse %s %s: %s", image_type, entity_type, e)

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
        old_entity = _parse_entity(old_image, entity_type, "old")
        new_entity = _parse_entity(new_image, entity_type, "new")

        # Log parsing results for debugging
        if old_image and not old_entity:
            logger.warning(
                "Failed to parse old %s, available keys: %s",
                entity_type,
                list(old_image.keys()) if old_image else "None",
            )
        if new_image and not new_entity:
            logger.warning(
                "Failed to parse new %s, available keys: %s",
                entity_type,
                list(new_image.keys()) if new_image else "None",
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
        logger.warning("Failed to parse stream record: %s", e)
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
        logger.error("Queue URL not found for %s", queue_env_var)
        return 0

    # Send in batches of 10 (SQS batch limit)
    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]

        entries = []
        for j, (message_dict, _) in enumerate(batch):
            entries.append(
                {
                    "Id": str(i + j),
                    "MessageBody": json.dumps(message_dict),
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
            logger.info("Sending message to %s", queue_url)
            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )

            # Count successful sends
            successful = len(response.get("Successful", []))
            sent_count += successful

            logger.info(
                "Sent %s messages to %s queue", successful, collection.value
            )

            # Log any failures
            if "Failed" in response and response["Failed"]:
                for failed in response["Failed"]:
                    logger.error(
                        "Failed to send message %s to %s queue: %s - %s",
                        failed["Id"],
                        collection.value,
                        failed.get("Code", "UnknownError"),
                        failed.get("Message", "No error details"),
                    )

        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Error sending SQS batch to %s queue: %s", collection.value, e
            )

    return sent_count
