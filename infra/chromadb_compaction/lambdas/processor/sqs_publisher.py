"""
SQS message publishing logic.

Handles routing and sending messages to collection-specific SQS queues.
"""

import json
import logging
import os
from typing import List

import boto3

from .models import ChromaDBCollection, StreamMessage

# Module-level logger
logger = logging.getLogger(__name__)


def publish_messages(messages: List[StreamMessage], metrics=None) -> int:
    """
    Send messages to appropriate collection-specific SQS queues.

    Each message is sent to the queue(s) for the collections it affects:
    - RECEIPT_METADATA messages go to both lines and words queues
    - RECEIPT_WORD_LABEL messages go only to words queue

    Args:
        messages: List of StreamMessage objects to send
        metrics: Optional metrics collector

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
        msg_dict = _message_to_dict(msg)

        if ChromaDBCollection.LINES in msg.collections:
            lines_messages.append((msg_dict, ChromaDBCollection.LINES))
        if ChromaDBCollection.WORDS in msg.collections:
            words_messages.append((msg_dict, ChromaDBCollection.WORDS))

    # Send to lines queue
    if lines_messages:
        sent_count += send_batch_to_queue(
            sqs,
            lines_messages,
            "LINES_QUEUE_URL",
            ChromaDBCollection.LINES,
            metrics,
        )

    # Send to words queue
    if words_messages:
        sent_count += send_batch_to_queue(
            sqs,
            words_messages,
            "WORDS_QUEUE_URL",
            ChromaDBCollection.WORDS,
            metrics,
        )

    return sent_count


def _message_to_dict(msg: StreamMessage) -> dict:
    """
    Convert StreamMessage to dictionary for JSON serialization.

    Args:
        msg: StreamMessage to convert

    Returns:
        Dictionary representation
    """
    # Convert FieldChange objects to dictionaries
    changes_dict = {}
    for field_name, field_change in msg.changes.items():
        changes_dict[field_name] = {
            "old": field_change.old,
            "new": field_change.new,
        }

    return {
        "source": msg.source,
        "entity_type": msg.entity_type,
        "entity_data": msg.entity_data,
        "changes": changes_dict,
        "event_name": msg.event_name,
        "timestamp": msg.timestamp,
        "stream_record_id": msg.stream_record_id,
        "aws_region": msg.aws_region,
    }


def send_batch_to_queue(
    sqs,
    messages: List[tuple],
    queue_env_var: str,
    collection: ChromaDBCollection,
    metrics=None,
) -> int:
    """
    Send a batch of messages to a specific queue.

    Args:
        sqs: boto3 SQS client
        messages: List[tuple] of (message_dict, collection) tuples
        queue_env_var: Environment variable containing the queue URL
        collection: ChromaDBCollection this queue serves
        metrics: Optional metrics collector

    Returns:
        Number of messages successfully sent
    """
    sent_count = 0
    queue_url = os.environ.get(queue_env_var)

    if not queue_url:
        logger.error(f"Queue URL not found: {queue_env_var}")
        return 0

    # Send in batches of 10 (SQS batch limit)
    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]

        entries = []
        for j, (message_dict, _) in enumerate(batch):
            entity_type = message_dict.get("entity_type", "UNKNOWN")
            entity_data = message_dict.get("entity_data", {})

            # For COMPACTION_RUN: use per-image grouping to allow parallel processing
            # Different images can process in parallel, improving throughput.
            # The per-collection lock still ensures safe snapshot publishing.
            if entity_type == "COMPACTION_RUN":
                image_id = entity_data.get("image_id") or "unknown"
                message_group_id = f"COMPACTION_RUN:{image_id}:{collection.value}"
            elif entity_type in {"RECEIPT_METADATA", "RECEIPT_WORD_LABEL"}:
                # Use same MessageGroupId as COMPACTION_RUN for the same image
                # This ensures metadata updates are processed AFTER delta merge completes,
                # maintaining proper ordering in the FIFO queue.
                image_id = entity_data.get("image_id") or "unknown"
                message_group_id = f"COMPACTION_RUN:{image_id}:{collection.value}"
            else:
                # For other entities, keep existing grouping for parallelism
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
                f"Sending message batch to {collection.value} queue",
                extra={"batch_size": len(entries)},
            )

            response = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)

            # Count successful sends
            successful = len(response.get("Successful", []))
            sent_count += successful

            logger.info(f"Sent {successful} messages to {collection.value} queue")

            if metrics:
                metrics.count(
                    "SQSMessagesSuccessful",
                    successful,
                    {"collection": collection.value},
                )

            # Log any failures
            if "Failed" in response and response["Failed"]:
                failed_count = len(response["Failed"])

                if metrics:
                    metrics.count(
                        "SQSMessagesFailed",
                        failed_count,
                        {"collection": collection.value},
                    )

                for failed in response["Failed"]:
                    logger.error(
                        f"Failed to send message to {collection.value} queue",
                        extra={
                            "message_id": failed["Id"],
                            "error_code": failed.get("Code", "UnknownError"),
                            "error_message": failed.get("Message", "No error details"),
                        },
                    )

        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                f"Error sending SQS batch to {collection.value} queue: {e}",
                extra={"error_type": type(e).__name__},
            )

            if metrics:
                metrics.count(
                    "SQSBatchError",
                    1,
                    {
                        "collection": collection.value,
                        "error_type": type(e).__name__,
                    },
                )

    return sent_count
