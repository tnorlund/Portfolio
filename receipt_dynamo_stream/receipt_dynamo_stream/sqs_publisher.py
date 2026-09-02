"""
SQS publishing utilities for stream messages.

Publishes to Standard SQS queues for high throughput (batch size 1000).
The compactor Lambda handles ordering by sorting REMOVE first and using
within-batch deduplication to prevent orphaned embeddings.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from receipt_dynamo_stream.exceptions import (
    QueueBatchFailureError,
    QueueConfigurationError,
    QueueServiceError,
)
from receipt_dynamo_stream.models import (
    ChromaDBCollection,
    StreamMessage,
    TargetQueue,
)
from receipt_dynamo_stream.stream_types import MetricsRecorder, SQSBatchClient

logger = logging.getLogger(__name__)


def publish_messages(
    messages: Iterable[StreamMessage],
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """
    Send StreamMessage objects to collection-specific SQS queues.
    """
    sqs: SQSBatchClient = boto3.client("sqs")
    sent_count = 0
    lines_messages: list[tuple[dict[str, object], ChromaDBCollection]] = []
    words_messages: list[tuple[dict[str, object], ChromaDBCollection]] = []
    summary_messages: list[tuple[dict[str, object], TargetQueue]] = []
    line_item_messages: list[tuple[dict[str, object], TargetQueue]] = []

    for msg in messages:
        msg_dict = _message_to_dict(msg)
        if ChromaDBCollection.LINES in msg.collections:
            lines_messages.append((msg_dict, ChromaDBCollection.LINES))
        if ChromaDBCollection.WORDS in msg.collections:
            words_messages.append((msg_dict, ChromaDBCollection.WORDS))
        if TargetQueue.RECEIPT_SUMMARY in msg.collections:
            summary_messages.append((msg_dict, TargetQueue.RECEIPT_SUMMARY))
        if TargetQueue.LINE_ITEMS in msg.collections:
            line_item_messages.append((msg_dict, TargetQueue.LINE_ITEMS))

    # Chroma compaction legs are RETIRED targets: when their queue URLs
    # are absent (the compaction stack is deleted), skip them silently
    # instead of raising — a raise here would abort the whole batch
    # BEFORE the surviving summary/line-item sends and the vector
    # freshening leg run (codex teardown review P1).
    if lines_messages and os.environ.get("LINES_QUEUE_URL"):
        sent_count += send_batch_to_queue(
            sqs,
            lines_messages,
            "LINES_QUEUE_URL",
            ChromaDBCollection.LINES,
            metrics,
        )

    if words_messages and os.environ.get("WORDS_QUEUE_URL"):
        sent_count += send_batch_to_queue(
            sqs,
            words_messages,
            "WORDS_QUEUE_URL",
            ChromaDBCollection.WORDS,
            metrics,
        )

    if summary_messages:
        sent_count += send_batch_to_queue(
            sqs,
            summary_messages,
            "RECEIPT_SUMMARY_QUEUE_URL",
            TargetQueue.RECEIPT_SUMMARY,
            metrics,
        )

    if line_item_messages:
        sent_count += send_batch_to_queue(
            sqs,
            line_item_messages,
            "LINE_ITEM_QUEUE_URL",
            TargetQueue.LINE_ITEMS,
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


def _build_sqs_entry(
    entry_id: str,
    message_dict: dict[str, object],
    collection: ChromaDBCollection | TargetQueue,
) -> dict[str, object]:
    """Build a single SQS batch entry for Standard queues."""
    return {
        "Id": entry_id,
        "MessageBody": json.dumps(message_dict),
        # No MessageGroupId - Standard queues don't support it
        # Lambda handles ordering by sorting REMOVE first within each batch
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


def send_batch_to_queue(
    sqs: SQSBatchClient,
    messages: Sequence[
        tuple[dict[str, object], ChromaDBCollection | TargetQueue]
    ],
    queue_env_var: str,
    collection: ChromaDBCollection | TargetQueue,
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """Send a batch of messages to a specific queue.

    Raises:
        QueueConfigurationError: If the queue URL is not configured.
        QueueServiceError: If SQS cannot process the batch request.
        QueueBatchFailureError: If SQS rejects entries within the batch.
    """
    sent_count = 0
    queue_url = os.environ.get(queue_env_var)

    if not queue_url:
        logger.error("Queue URL not found: %s", queue_env_var)
        raise QueueConfigurationError(
            queue_env_var, queue_name=collection.value
        )

    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]
        entries = [
            _build_sqs_entry(str(i + j), msg_dict, collection)
            for j, (msg_dict, _) in enumerate(batch)
        ]

        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )
            successful = len(response.get("Successful", []))
            failed_entries = [
                dict(entry) for entry in response.get("Failed", [])
            ]
            sent_count += successful

            logger.info(
                "Sent %s messages to %s queue", successful, collection.value
            )

            if metrics:
                metrics.count(
                    "SQSMessagesSuccessful",
                    successful,
                    {"collection": collection.value},
                )

            if failed_entries:
                if metrics:
                    metrics.count(
                        "SQSMessagesFailed",
                        len(failed_entries),
                        {"collection": collection.value},
                    )
                raise QueueBatchFailureError(collection.value, failed_entries)

        except (ClientError, BotoCoreError) as exc:
            logger.exception(
                "Failed to send messages to %s queue: %s",
                collection.value,
                exc,
            )
            if metrics:
                metrics.count(
                    "SQSMessagesFailed",
                    len(batch),
                    {"collection": collection.value},
                )
            raise QueueServiceError(collection.value, len(batch)) from exc

    return sent_count


__all__ = ["publish_messages", "send_batch_to_queue"]
