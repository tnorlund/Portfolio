"""
SQS publishing utilities for stream messages.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable, Mapping, Optional, Protocol, cast

import boto3
from receipt_dynamo_stream.models import ChromaDBCollection, StreamMessage

logger = logging.getLogger(__name__)


class MetricsRecorder(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal protocol for metrics clients."""

    def count(
        self,
        name: str,
        value: int,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> object:
        """Record a count metric."""
        return None


def publish_messages(
    messages: Iterable[StreamMessage],
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """
    Send StreamMessage objects to collection-specific SQS queues.
    """
    sqs: Any = boto3.client("sqs")
    sent_count = 0
    lines_messages: list[tuple[dict[str, object], ChromaDBCollection]] = []
    words_messages: list[tuple[dict[str, object], ChromaDBCollection]] = []

    for msg in messages:
        msg_dict = _message_to_dict(msg)
        if ChromaDBCollection.LINES in msg.collections:
            lines_messages.append((msg_dict, ChromaDBCollection.LINES))
        if ChromaDBCollection.WORDS in msg.collections:
            words_messages.append((msg_dict, ChromaDBCollection.WORDS))

    if lines_messages:
        sent_count += send_batch_to_queue(
            sqs,
            lines_messages,
            "LINES_QUEUE_URL",
            ChromaDBCollection.LINES,
            metrics,
        )

    if words_messages:
        sent_count += send_batch_to_queue(
            sqs,
            words_messages,
            "WORDS_QUEUE_URL",
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
        "source": msg.source,
        "entity_type": msg.entity_type,
        "entity_data": dict(msg.entity_data),
        "changes": changes_dict,
        "event_name": msg.event_name,
        "timestamp": msg.timestamp,
        "stream_record_id": msg.stream_record_id,
        "aws_region": msg.aws_region,
    }


def send_batch_to_queue(  # pylint: disable=too-many-locals
    sqs: Any,
    messages: list[tuple[dict[str, object], ChromaDBCollection]],
    queue_env_var: str,
    collection: ChromaDBCollection,
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """
    Send a batch of messages to a specific queue.
    """
    sent_count = 0
    queue_url = os.environ.get(queue_env_var)

    if not queue_url:
        logger.error("Queue URL not found: %s", queue_env_var)
        return 0

    for i in range(0, len(messages), 10):
        batch = messages[i : i + 10]
        entries = []

        for j, (message_dict, _) in enumerate(batch):
            entity_type = message_dict.get("entity_type", "UNKNOWN")
            entity_data = cast(
                dict[str, object], message_dict.get("entity_data", {})
            )

            if entity_type == "COMPACTION_RUN":
                image_id = entity_data.get("image_id") or "unknown"
                message_group_id = (
                    f"COMPACTION_RUN:{image_id}:{collection.value}"
                )
            elif entity_type in {"RECEIPT_PLACE", "RECEIPT_WORD_LABEL"}:
                image_id = entity_data.get("image_id") or "unknown"
                message_group_id = (
                    f"COMPACTION_RUN:{image_id}:{collection.value}"
                )
            else:
                group_key = (
                    entity_data.get("run_id")
                    or entity_data.get("receipt_id")
                    or entity_data.get("image_id")
                    or "default"
                )
                message_group_id = (
                    f"{entity_type}:{group_key}:{collection.value}"
                )

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
                            "StringValue": str(
                                message_dict.get("entity_type")
                            ),
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
            )

        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url, Entries=entries
            )
            successful = len(response.get("Successful", []))
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

        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to send messages to %s queue",
                collection.value,
            )
            if metrics:
                metrics.count(
                    "SQSMessagesFailed",
                    len(batch),
                    {"collection": collection.value},
                )

    return sent_count


__all__ = ["publish_messages", "send_batch_to_queue"]
