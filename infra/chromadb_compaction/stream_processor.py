"""
DynamoDB Stream Processor Lambda for ChromaDB Compaction Integration

This module defines the Lambda function that processes DynamoDB stream events
for receipt metadata and word label changes, triggering ChromaDB metadata updates
through the existing compaction SQS queue.

Focuses on:
- RECEIPT_METADATA entities (merchant info changes)
- RECEIPT_WORD_LABEL entities (word label changes)
- Both MODIFY and REMOVE operations
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

# Import receipt_dynamo entity parsers
from receipt_dynamo.entities.receipt_metadata import item_to_receipt_metadata
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)

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
    logger.info(f"Processing {len(event['Records'])} DynamoDB stream records")

    # Avoid logging entire event to prevent PII exposure
    if logger.level <= logging.DEBUG:
        logger.debug("Stream event structure present")

    messages_to_send = []
    processed_records = 0

    for record in event["Records"]:
        try:
            # Parse stream record using entity parsers
            parsed_record = parse_stream_record(record)

            if not parsed_record:
                continue  # Not a receipt entity we care about

            # Process MODIFY and REMOVE events
            if record["eventName"] in ["MODIFY", "REMOVE"]:
                entity_type = parsed_record["entity_type"]
                old_entity = parsed_record["old_entity"]
                new_entity = parsed_record["new_entity"]

                # Check if any ChromaDB-relevant fields changed
                changes = get_chromadb_relevant_changes(
                    entity_type, old_entity, new_entity
                )

                # Always process REMOVE events, even without specific field changes
                if changes or record["eventName"] == "REMOVE":
                    # Extract entity identification data
                    if entity_type == "RECEIPT_METADATA":
                        entity = old_entity or new_entity
                        entity_data = {
                            "entity_type": entity_type,
                            "image_id": entity.image_id,
                            "receipt_id": entity.receipt_id,
                        }
                    elif entity_type == "RECEIPT_WORD_LABEL":
                        entity = old_entity or new_entity
                        entity_data = {
                            "entity_type": entity_type,
                            "image_id": entity.image_id,
                            "receipt_id": entity.receipt_id,
                            "line_id": entity.line_id,
                            "word_id": entity.word_id,
                            "label": entity.label,
                        }

                    # Create SQS message for the compaction Lambda
                    message = {
                        "source": "dynamodb_stream",
                        "entity_type": entity_type,
                        "entity_data": entity_data,
                        "changes": changes,
                        "event_name": record["eventName"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stream_record_id": record.get("eventID", "unknown"),
                        "aws_region": record.get("awsRegion", "unknown"),
                    }
                    messages_to_send.append(message)
                    processed_records += 1

        except Exception as e:
            logger.error(
                f"Error processing stream record {record.get('eventID', 'unknown')}: {e}"
            )
            # Continue processing other records

    # Send all messages to existing SQS queue in batches
    if messages_to_send:
        sent_count = send_messages_to_sqs(messages_to_send)
        logger.info(f"Sent {sent_count} messages to compaction queue")

    return {
        "statusCode": 200,
        "processed_records": processed_records,
        "queued_messages": len(messages_to_send),
    }


def parse_stream_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
        entity_type = None
        if "#METADATA" in sk:
            entity_type = "RECEIPT_METADATA"
        elif "#LABEL#" in sk:
            entity_type = "RECEIPT_WORD_LABEL"
        else:
            return None  # Not a relevant entity type

        # Get appropriate DynamoDB item for parsing
        old_image = record["dynamodb"].get("OldImage")
        new_image = record["dynamodb"].get("NewImage")

        # Parse entities using receipt_dynamo parsers
        old_entity = None
        new_entity = None

        if entity_type == "RECEIPT_METADATA":
            if old_image:
                try:
                    old_entity = item_to_receipt_metadata(old_image)
                except ValueError as e:
                    logger.warning(f"Failed to parse old ReceiptMetadata: {e}")
            if new_image:
                try:
                    new_entity = item_to_receipt_metadata(new_image)
                except ValueError as e:
                    logger.warning(f"Failed to parse new ReceiptMetadata: {e}")

        elif entity_type == "RECEIPT_WORD_LABEL":
            if old_image:
                try:
                    old_entity = item_to_receipt_word_label(old_image)
                except ValueError as e:
                    logger.warning(
                        f"Failed to parse old ReceiptWordLabel: {e}"
                    )
            if new_image:
                try:
                    new_entity = item_to_receipt_word_label(new_image)
                except ValueError as e:
                    logger.warning(
                        f"Failed to parse new ReceiptWordLabel: {e}"
                    )

        # Return parsed entity information
        return {
            "entity_type": entity_type,
            "old_entity": old_entity,
            "new_entity": new_entity,
            "pk": pk,
            "sk": sk,
        }

    except (KeyError, ValueError) as e:
        logger.warning(f"Failed to parse stream record: {e}")
        return None


def get_chromadb_relevant_changes(
    entity_type: str,
    old_entity: Optional[object],
    new_entity: Optional[object],
) -> Dict[str, Any]:
    """
    Identify changes to fields that affect ChromaDB metadata.

    Uses typed entity objects for robust field access and comparison.

    Args:
        entity_type: Type of entity (RECEIPT_METADATA or RECEIPT_WORD_LABEL)
        old_entity: Previous entity state (ReceiptMetadata or ReceiptWordLabel)
        new_entity: Current entity state (None for REMOVE events)

    Returns:
        Dictionary of changed fields with old/new values
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
            changes[field] = {"old": old_value, "new": new_value}

    return changes


def extract_dynamodb_value(dynamo_value: Dict[str, Any]) -> Any:
    """
    Extract the actual value from DynamoDB attribute format.

    Args:
        dynamo_value: DynamoDB attribute (e.g., {'S': 'string'}, {'N': '123'})

    Returns:
        The actual value
    """
    if not dynamo_value:
        return None

    # Handle different DynamoDB attribute types
    try:
        if "S" in dynamo_value:
            return dynamo_value["S"]
        elif "N" in dynamo_value:
            # Try int first, then float
            try:
                return int(dynamo_value["N"])
            except ValueError:
                return float(dynamo_value["N"])
        elif "BOOL" in dynamo_value:
            return dynamo_value["BOOL"]
        elif "M" in dynamo_value:
            # Map type - convert to dict
            return {
                k: extract_dynamodb_value(v)
                for k, v in dynamo_value["M"].items()
            }
        elif "L" in dynamo_value:
            # List type - convert to list
            return [extract_dynamodb_value(item) for item in dynamo_value["L"]]
        elif "NULL" in dynamo_value:
            return None
        elif "SS" in dynamo_value:
            # String set
            return list(dynamo_value["SS"])
        elif "NS" in dynamo_value:
            # Number set
            return [float(n) for n in dynamo_value["NS"]]
        elif "BS" in dynamo_value:
            # Binary set - return as is
            return dynamo_value["BS"]
        else:
            logger.warning(
                f"Unknown DynamoDB attribute type in: {list(dynamo_value.keys())}"
            )
            return dynamo_value
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Error extracting DynamoDB value: {e}")
        return dynamo_value


def send_messages_to_sqs(messages: List[Dict[str, Any]]) -> int:
    """
    Send messages to the existing compaction SQS queue.

    Args:
        messages: List of message dictionaries to send

    Returns:
        Number of messages successfully sent
    """
    sqs = boto3.client("sqs")
    queue_url = os.environ["COMPACTION_QUEUE_URL"]
    sent_count = 0

    # Send in batches of 10 (SQS batch limit)
    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]

        entries = []
        for j, message in enumerate(batch):
            entries.append(
                {
                    "Id": str(i + j),
                    "MessageBody": json.dumps(message),
                    "MessageAttributes": {
                        "source": {
                            "StringValue": "dynamodb_stream",
                            "DataType": "String",
                        },
                        "entity_type": {
                            "StringValue": message["entity_type"],
                            "DataType": "String",
                        },
                        "event_name": {
                            "StringValue": message["event_name"],
                            "DataType": "String",
                        },
                    },
                }
            )

        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )

            # Count successful sends
            sent_count += len(response.get("Successful", []))

            # Log any failures
            if "Failed" in response and response["Failed"]:
                for failed in response["Failed"]:
                    logger.error(
                        f"Failed to send message {failed['Id']}: "
                        f"{failed.get('Code', 'UnknownError')} - {failed.get('Message', 'No error details')}"
                    )

        except Exception as e:
            logger.error(f"Error sending SQS batch: {e}")

    return sent_count
