"""
Message building logic for DynamoDB stream records.

Constructs StreamMessage objects that can be published to SQS queues.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Protocol, cast

from receipt_dynamo_stream.change_detection import (
    get_chromadb_relevant_changes,
)
from receipt_dynamo_stream.models import ChromaDBCollection, StreamMessage
from receipt_dynamo_stream.parsing import (
    is_compaction_run,
    is_embeddings_completed,
    parse_compaction_run,
    parse_stream_record,
)

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logger = logging.getLogger(__name__)

StreamRecord = Mapping[str, object]


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


def build_messages_from_records(
    records: Iterable[StreamRecord], metrics: Optional[MetricsRecorder] = None
) -> list[StreamMessage]:
    """
    Build StreamMessage objects from DynamoDB stream records.
    """
    messages: list[StreamMessage] = []

    for record in records:
        event_name = record.get("eventName")
        if event_name == "INSERT":
            messages.extend(build_compaction_run_messages(record, metrics))
        elif event_name in {"MODIFY", "REMOVE"}:
            completion_messages = build_compaction_run_completion_messages(
                record, metrics
            )
            if completion_messages:
                messages.extend(completion_messages)
            else:
                entity_message = build_entity_change_message(record, metrics)
                if entity_message:
                    messages.append(entity_message)

    return messages


def build_compaction_run_messages(
    record: StreamRecord, metrics: Optional[MetricsRecorder] = None
) -> list[StreamMessage]:
    """
    Build messages for COMPACTION_RUN INSERT events (one per collection).
    """
    messages: list[StreamMessage] = []

    try:
        dynamodb = cast(dict[str, object], record.get("dynamodb", {}))
        new_image = cast(Optional[dict[str, object]], dynamodb.get("NewImage"))
        keys = cast(dict[str, object], dynamodb.get("Keys", {}))
        pk = cast(dict[str, str], keys.get("PK", {})).get("S", "")
        sk = cast(dict[str, str], keys.get("SK", {})).get("S", "")

        if not (new_image and is_compaction_run(pk, sk)):
            return messages

        compaction_run = parse_compaction_run(
            cast(dict[str, object], new_image), pk, sk
        )
        cr_entity = {
            "run_id": compaction_run.get("run_id"),
            "image_id": compaction_run.get("image_id"),
            "receipt_id": compaction_run.get("receipt_id"),
            "lines_delta_prefix": compaction_run.get("lines_delta_prefix"),
            "words_delta_prefix": compaction_run.get("words_delta_prefix"),
        }

        for collection, prefix_key in (
            (ChromaDBCollection.LINES, "lines_delta_prefix"),
            (ChromaDBCollection.WORDS, "words_delta_prefix"),
        ):
            messages.append(
                StreamMessage(
                    entity_type="COMPACTION_RUN",
                    entity_data={
                        **cr_entity,
                        "delta_s3_prefix": cr_entity[prefix_key],
                    },
                    changes={},
                    event_name="INSERT",
                    collections=(collection,),
                    source="dynamodb_stream",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    stream_record_id=str(record.get("eventID", "unknown")),
                    aws_region=str(record.get("awsRegion", "unknown")),
                    record_snapshot=cast(Mapping[str, object], new_image),
                )
            )

        logger.info(
            "Created compaction run messages",
            extra={
                "run_id": compaction_run.get("run_id"),
                "image_id": compaction_run.get("image_id"),
            },
        )

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to build compaction run message: %s", exc)
        if metrics:
            metrics.count("CompactionRunMessageBuildError", 1)

    return messages


def build_compaction_run_completion_messages(
    record: StreamRecord, metrics: Optional[MetricsRecorder] = None
) -> list[StreamMessage]:
    """
    Build messages for COMPACTION_RUN MODIFY events when embeddings complete.
    """
    messages: list[StreamMessage] = []

    try:
        dynamodb = cast(dict[str, object], record.get("dynamodb", {}))
        new_image = cast(Optional[dict[str, object]], dynamodb.get("NewImage"))
        keys = cast(dict[str, object], dynamodb.get("Keys", {}))
        pk = cast(dict[str, str], keys.get("PK", {})).get("S", "")
        sk = cast(dict[str, str], keys.get("SK", {})).get("S", "")

        if not new_image or not keys:
            return messages
        if not is_compaction_run(pk, sk):
            return messages
        if not is_embeddings_completed(cast(dict[str, object], new_image)):
            return messages

        compaction_run = parse_compaction_run(
            cast(dict[str, object], new_image), pk, sk
        )
        for collection in (ChromaDBCollection.LINES, ChromaDBCollection.WORDS):
            messages.append(
                StreamMessage(
                    entity_type="COMPACTION_RUN",
                    entity_data={
                        "run_id": compaction_run.get("run_id"),
                        "image_id": compaction_run.get("image_id"),
                        "receipt_id": compaction_run.get("receipt_id"),
                    },
                    changes={},
                    event_name=str(record.get("eventName", "MODIFY")),
                    collections=(collection,),
                    source="dynamodb_stream",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    stream_record_id=str(record.get("eventID", "unknown")),
                    aws_region=str(record.get("awsRegion", "unknown")),
                    record_snapshot=cast(Mapping[str, object], new_image),
                )
            )

        if metrics:
            metrics.count("CompactionRunCompletionDetected", 1)

        logger.info(
            "Detected COMPACTION_RUN completion, queuing compaction",
            extra={
                "run_id": compaction_run.get("run_id"),
                "image_id": compaction_run.get("image_id"),
                "receipt_id": compaction_run.get("receipt_id"),
            },
        )

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to build compaction run completion message: %s", exc
        )
        if metrics:
            metrics.count("CompactionRunCompletionMessageBuildError", 1)

    return messages


def build_entity_change_message(
    record: StreamRecord, metrics: Optional[MetricsRecorder] = None
) -> StreamMessage | None:
    """
    Build a StreamMessage from an entity change (MODIFY/REMOVE) record.
    """
    try:
        parsed_record = parse_stream_record(record, metrics)
        if not parsed_record:
            return None

        entity_type = parsed_record.entity_type
        old_entity = parsed_record.old_entity
        new_entity = parsed_record.new_entity

        changes = get_chromadb_relevant_changes(
            entity_type, old_entity, new_entity
        )
        if metrics:
            metrics.count(
                "ChromaDBRelevantChanges",
                len(changes),
                {"entity_type": entity_type},
            )

        if not changes and record.get("eventName") != "REMOVE":
            return None

        entity = old_entity or new_entity
        entity_data, target_collections = _extract_entity_data(
            entity_type, entity
        )
        if not entity_data or not target_collections:
            return None

        if metrics:
            for collection in target_collections:
                metrics.count(
                    "StreamMessageCreated",
                    1,
                    {
                        "entity_type": entity_type,
                        "collection": collection.value,
                    },
                )

        # Convert new_entity to dict for snapshot (current state after change)
        # For MODIFY events, this is the updated entity; for REMOVE, it's the entity being removed
        record_snapshot = asdict(new_entity) if new_entity else None

        return StreamMessage(
            entity_type=entity_type,
            entity_data=entity_data,
            changes=changes,
            event_name=str(record.get("eventName", "UNKNOWN")),
            collections=tuple(target_collections),
            source="dynamodb_stream",
            timestamp=datetime.now(timezone.utc).isoformat(),
            stream_record_id=str(record.get("eventID", "unknown")),
            aws_region=str(record.get("awsRegion", "unknown")),
            record_snapshot=record_snapshot,
        )

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to build entity change message: %s", exc)
        if metrics:
            metrics.count("EntityMessageBuildError", 1)
        return None


def _extract_entity_data(
    entity_type: str,
    entity: Receipt | ReceiptLine | ReceiptPlace | ReceiptWord | ReceiptWordLabel | None,
) -> tuple[dict[str, object], list[ChromaDBCollection]]:
    """
    Extract entity data and determine target collections.
    """
    if not entity:
        return {}, []

    if entity_type == "RECEIPT_PLACE" and isinstance(
        entity, ReceiptPlace
    ):
        entity_data = {
            "entity_type": entity_type,
            "image_id": entity.image_id,
            "receipt_id": entity.receipt_id,
        }
        return entity_data, [
            ChromaDBCollection.LINES,
            ChromaDBCollection.WORDS,
        ]

    if entity_type == "RECEIPT_WORD_LABEL" and isinstance(
        entity, ReceiptWordLabel
    ):
        entity_data = {
            "entity_type": entity_type,
            "image_id": entity.image_id,
            "receipt_id": entity.receipt_id,
            "line_id": entity.line_id,
            "word_id": entity.word_id,
            "label": entity.label,
        }
        return entity_data, [ChromaDBCollection.WORDS]

    # RECEIPT deletion: affects both LINES and WORDS collections
    # All embeddings for this receipt need to be deleted
    if entity_type == "RECEIPT" and isinstance(entity, Receipt):
        entity_data = {
            "entity_type": entity_type,
            "image_id": entity.image_id,
            "receipt_id": entity.receipt_id,
        }
        return entity_data, [
            ChromaDBCollection.LINES,
            ChromaDBCollection.WORDS,
        ]

    # RECEIPT_WORD deletion: delete from WORDS collection only
    if entity_type == "RECEIPT_WORD" and isinstance(entity, ReceiptWord):
        entity_data = {
            "entity_type": entity_type,
            "image_id": entity.image_id,
            "receipt_id": entity.receipt_id,
            "line_id": entity.line_id,
            "word_id": entity.word_id,
        }
        return entity_data, [ChromaDBCollection.WORDS]

    # RECEIPT_LINE deletion: delete from LINES collection only
    if entity_type == "RECEIPT_LINE" and isinstance(entity, ReceiptLine):
        entity_data = {
            "entity_type": entity_type,
            "image_id": entity.image_id,
            "receipt_id": entity.receipt_id,
            "line_id": entity.line_id,
        }
        return entity_data, [ChromaDBCollection.LINES]

    return {}, []


__all__ = ["build_messages_from_records"]
