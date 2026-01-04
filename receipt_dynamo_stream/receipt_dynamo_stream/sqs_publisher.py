"""
SQS publishing utilities for stream messages.

Routes messages to appropriate queues based on event type:
- INSERT/MODIFY events -> Standard queues (high throughput)
- REMOVE events -> FIFO queues (ordered deletes)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from receipt_dynamo_stream.models import ChromaDBCollection, StreamMessage
from receipt_dynamo_stream.stream_types import MetricsRecorder

logger = logging.getLogger(__name__)


def publish_messages(
    messages: Iterable[StreamMessage],
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """
    Send StreamMessage objects to appropriate SQS queues based on event type.

    Routing strategy:
    - INSERT/MODIFY events -> Standard queues (high throughput)
    - REMOVE events -> FIFO queues (ordered deletes)
    """
    sqs: Any = boto3.client("sqs")
    sent_count = 0

    # Separate messages by collection AND event type
    lines_standard: list[tuple[dict[str, object], ChromaDBCollection]] = []
    lines_delete: list[tuple[dict[str, object], ChromaDBCollection]] = []
    words_standard: list[tuple[dict[str, object], ChromaDBCollection]] = []
    words_delete: list[tuple[dict[str, object], ChromaDBCollection]] = []

    for msg in messages:
        msg_dict = _message_to_dict(msg)
        is_remove = msg.event_name == "REMOVE"

        if ChromaDBCollection.LINES in msg.collections:
            if is_remove:
                lines_delete.append((msg_dict, ChromaDBCollection.LINES))
            else:
                lines_standard.append((msg_dict, ChromaDBCollection.LINES))

        if ChromaDBCollection.WORDS in msg.collections:
            if is_remove:
                words_delete.append((msg_dict, ChromaDBCollection.WORDS))
            else:
                words_standard.append((msg_dict, ChromaDBCollection.WORDS))

    # Send to Standard queues (INSERT/MODIFY)
    if lines_standard:
        sent_count += send_batch_to_standard_queue(
            sqs,
            lines_standard,
            "LINES_STANDARD_QUEUE_URL",
            ChromaDBCollection.LINES,
            metrics,
        )

    if words_standard:
        sent_count += send_batch_to_standard_queue(
            sqs,
            words_standard,
            "WORDS_STANDARD_QUEUE_URL",
            ChromaDBCollection.WORDS,
            metrics,
        )

    # Send to FIFO queues (REMOVE only)
    if lines_delete:
        sent_count += send_batch_to_fifo_queue(
            sqs,
            lines_delete,
            "LINES_DELETE_QUEUE_URL",
            ChromaDBCollection.LINES,
            metrics,
        )

    if words_delete:
        sent_count += send_batch_to_fifo_queue(
            sqs,
            words_delete,
            "WORDS_DELETE_QUEUE_URL",
            ChromaDBCollection.WORDS,
            metrics,
        )

    return sent_count


def _message_to_dict(msg: StreamMessage) -> dict[str, object]:
    """
    Convert StreamMessage to dictionary for JSON serialization.
    """
    changes_dict: dict[str, dict[str, object | None]] = {}
    for field_name, field_change in msg.changes.items():
        changes_dict[field_name] = {
            "old": field_change.old,
            "new": field_change.new,
        }

    return {
        "source": msg.context.source,
        "entity_type": msg.entity_type,
        "entity_data": dict(msg.entity_data),
        "changes": changes_dict,
        "event_name": msg.event_name,
        "timestamp": msg.context.timestamp,
        "stream_record_id": msg.context.record_id,
        "aws_region": msg.context.aws_region,
    }


def _build_standard_sqs_entry(
    entry_id: str,
    message_dict: dict[str, object],
    collection: ChromaDBCollection,
) -> dict[str, object]:
    """Build a single SQS batch entry for Standard queues (no MessageGroupId)."""
    return {
        "Id": entry_id,
        "MessageBody": json.dumps(message_dict),
        # No MessageGroupId for Standard queues
        "MessageAttributes": {
            "source": {
                "StringValue": "dynamodb_stream",
                "DataType": "String",
            },
            "entity_type": {
                "StringValue": str(message_dict.get("entity_type")),
                "DataType": "String",
            },
            "event_name": {
                "StringValue": str(message_dict.get("event_name")),
                "DataType": "String",
            },
            "collection": {
                "StringValue": collection.value,
                "DataType": "String",
            },
        },
    }


def _build_fifo_sqs_entry(
    entry_id: str,
    message_dict: dict[str, object],
    collection: ChromaDBCollection,
) -> dict[str, object]:
    """Build a single SQS batch entry for FIFO queues (with MessageGroupId)."""
    return {
        "Id": entry_id,
        "MessageBody": json.dumps(message_dict),
        "MessageGroupId": f"compaction:{collection.value}",
        "MessageAttributes": {
            "source": {
                "StringValue": "dynamodb_stream",
                "DataType": "String",
            },
            "entity_type": {
                "StringValue": str(message_dict.get("entity_type")),
                "DataType": "String",
            },
            "event_name": {
                "StringValue": str(message_dict.get("event_name")),
                "DataType": "String",
            },
            "collection": {
                "StringValue": collection.value,
                "DataType": "String",
            },
        },
    }


def send_batch_to_standard_queue(
    sqs: Any,
    messages: list[tuple[dict[str, object], ChromaDBCollection]],
    queue_env_var: str,
    collection: ChromaDBCollection,
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """Send a batch of messages to a Standard SQS queue."""
    sent_count = 0
    queue_url = os.environ.get(queue_env_var)

    if not queue_url:
        logger.error("Queue URL not found: %s", queue_env_var)
        return 0

    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]
        entries = [
            _build_standard_sqs_entry(str(i + j), msg_dict, collection)
            for j, (msg_dict, _) in enumerate(batch)
        ]

        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )
            successful = len(response.get("Successful", []))
            sent_count += successful

            logger.info(
                "Sent %s messages to %s standard queue",
                successful,
                collection.value,
            )

            if metrics:
                metrics.count(
                    "SQSMessagesSuccessful",
                    successful,
                    {"collection": collection.value, "queue_type": "standard"},
                )

        except (ClientError, BotoCoreError) as exc:
            logger.exception(
                "Failed to send messages to %s standard queue: %s",
                collection.value,
                exc,
            )
            if metrics:
                metrics.count(
                    "SQSMessagesFailed",
                    len(batch),
                    {"collection": collection.value, "queue_type": "standard"},
                )

    return sent_count


def send_batch_to_fifo_queue(
    sqs: Any,
    messages: list[tuple[dict[str, object], ChromaDBCollection]],
    queue_env_var: str,
    collection: ChromaDBCollection,
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """Send a batch of messages to a FIFO SQS queue."""
    sent_count = 0
    queue_url = os.environ.get(queue_env_var)

    if not queue_url:
        logger.error("Queue URL not found: %s", queue_env_var)
        return 0

    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]
        entries = [
            _build_fifo_sqs_entry(str(i + j), msg_dict, collection)
            for j, (msg_dict, _) in enumerate(batch)
        ]

        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )
            successful = len(response.get("Successful", []))
            sent_count += successful

            logger.info(
                "Sent %s messages to %s FIFO queue",
                successful,
                collection.value,
            )

            if metrics:
                metrics.count(
                    "SQSMessagesSuccessful",
                    successful,
                    {"collection": collection.value, "queue_type": "fifo"},
                )

        except (ClientError, BotoCoreError) as exc:
            logger.exception(
                "Failed to send messages to %s FIFO queue: %s",
                collection.value,
                exc,
            )
            if metrics:
                metrics.count(
                    "SQSMessagesFailed",
                    len(batch),
                    {"collection": collection.value, "queue_type": "fifo"},
                )

    return sent_count


__all__ = [
    "publish_messages",
    "send_batch_to_standard_queue",
    "send_batch_to_fifo_queue",
]
