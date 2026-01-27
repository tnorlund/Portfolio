"""
Message building logic for DynamoDB stream records.

Constructs StreamMessage objects that can be published to SQS queues.
"""

# pylint: disable=import-error
# import-error: receipt_dynamo is a monorepo sibling installed at runtime

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo_stream.change_detection import (
    get_chromadb_relevant_changes,
)
from receipt_dynamo_stream.models import (
    ChromaDBCollection,
    StreamMessage,
    StreamRecordContext,
    TargetQueue,
)
from receipt_dynamo_stream.parsing import (
    is_compaction_run,
    is_embeddings_completed,
    parse_compaction_run,
    parse_stream_record,
)
from receipt_dynamo_stream.stream_types import (
    DynamoDBStreamRecord,
    MetricsRecorder,
)

logger = logging.getLogger(__name__)


def build_messages_from_records(
    records: Iterable[DynamoDBStreamRecord],
    metrics: Optional[MetricsRecorder] = None,
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
    record: DynamoDBStreamRecord, metrics: Optional[MetricsRecorder] = None
) -> list[StreamMessage]:
    """
    Build messages for COMPACTION_RUN INSERT events (one per collection).
    """
    messages: list[StreamMessage] = []

    try:
        dynamodb = record["dynamodb"]
        new_image = dynamodb.get("NewImage")
        keys = dynamodb["Keys"]
        pk = keys["PK"]["S"]
        sk = keys["SK"]["S"]

        if not (new_image and is_compaction_run(pk, sk)):
            return messages

        compaction_run = parse_compaction_run(new_image, pk, sk)
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
                    context=StreamRecordContext(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        record_id=str(record.get("eventID", "unknown")),
                        aws_region=str(record.get("awsRegion", "unknown")),
                    ),
                    record_snapshot=new_image,
                )
            )

        logger.info(
            "Created compaction run messages",
            extra={
                "run_id": compaction_run.get("run_id"),
                "image_id": compaction_run.get("image_id"),
            },
        )

    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.exception("Failed to build compaction run message: %s", exc)
        if metrics:
            metrics.count("CompactionRunMessageBuildError", 1)

    return messages


def build_compaction_run_completion_messages(
    record: DynamoDBStreamRecord, metrics: Optional[MetricsRecorder] = None
) -> list[StreamMessage]:
    """
    Build messages for COMPACTION_RUN MODIFY events when embeddings complete.
    """
    messages: list[StreamMessage] = []

    try:
        dynamodb = record["dynamodb"]
        new_image = dynamodb.get("NewImage")
        keys = dynamodb["Keys"]
        pk = keys["PK"]["S"]
        sk = keys["SK"]["S"]

        if not new_image:
            return messages
        if not is_compaction_run(pk, sk):
            return messages
        if not is_embeddings_completed(new_image):
            return messages

        compaction_run = parse_compaction_run(new_image, pk, sk)
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
                    context=StreamRecordContext(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        record_id=str(record.get("eventID", "unknown")),
                        aws_region=str(record.get("awsRegion", "unknown")),
                    ),
                    record_snapshot=new_image,
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

    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.exception(
            "Failed to build compaction run completion message: %s", exc
        )
        if metrics:
            metrics.count("CompactionRunCompletionMessageBuildError", 1)

    return messages


def build_entity_change_message(
    record: DynamoDBStreamRecord, metrics: Optional[MetricsRecorder] = None
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

        # Convert new_entity to dict for snapshot (current state after
        # change). For MODIFY events, this is the updated entity; for
        # REMOVE, it's the entity being removed
        record_snapshot = asdict(new_entity) if new_entity else None

        return StreamMessage(
            entity_type=entity_type,
            entity_data=entity_data,
            changes=changes,
            event_name=str(record.get("eventName", "UNKNOWN")),
            collections=tuple(target_collections),
            context=StreamRecordContext(
                timestamp=datetime.now(timezone.utc).isoformat(),
                record_id=str(record.get("eventID", "unknown")),
                aws_region=str(record.get("awsRegion", "unknown")),
            ),
            record_snapshot=record_snapshot,
        )

    except (KeyError, TypeError, ValueError, AttributeError):
        logger.exception("Failed to build entity change message")
        if metrics:
            metrics.count("EntityMessageBuildError", 1)
        return None


def _extract_receipt_place(
    entity: ReceiptPlace,
) -> tuple[dict[str, object], list[ChromaDBCollection | TargetQueue]]:
    """Extract place data and target collections including summary queue.

    Place changes affect merchant_name in ReceiptSummary.
    """
    return {
        "entity_type": "RECEIPT_PLACE",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
    }, [
        ChromaDBCollection.LINES,
        ChromaDBCollection.WORDS,
        TargetQueue.RECEIPT_SUMMARY,
    ]


def _extract_receipt_word_label(
    entity: ReceiptWordLabel,
) -> tuple[dict[str, object], list[ChromaDBCollection | TargetQueue]]:
    """Extract word label data and target collections including summary queue.

    Label changes affect:
    - WORDS: Direct label metadata updates on word embeddings
    - LINES: Label aggregation on row-based line embeddings
    - RECEIPT_SUMMARY: Totals, tax, dates extracted from labels
    """
    return {
        "entity_type": "RECEIPT_WORD_LABEL",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
        "line_id": entity.line_id,
        "word_id": entity.word_id,
        "label": entity.label,
    }, [
        ChromaDBCollection.WORDS,
        ChromaDBCollection.LINES,
        TargetQueue.RECEIPT_SUMMARY,
    ]


def _extract_receipt(
    entity: Receipt,
) -> tuple[dict[str, object], list[ChromaDBCollection]]:
    return {
        "entity_type": "RECEIPT",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
    }, [ChromaDBCollection.LINES, ChromaDBCollection.WORDS]


def _extract_receipt_word(
    entity: ReceiptWord,
) -> tuple[dict[str, object], list[ChromaDBCollection]]:
    return {
        "entity_type": "RECEIPT_WORD",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
        "line_id": entity.line_id,
        "word_id": entity.word_id,
    }, [ChromaDBCollection.WORDS]


def _extract_receipt_line(
    entity: ReceiptLine,
) -> tuple[dict[str, object], list[ChromaDBCollection]]:
    return {
        "entity_type": "RECEIPT_LINE",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
        "line_id": entity.line_id,
    }, [ChromaDBCollection.LINES]


# Entity type to (expected class, extractor function) mapping
_ENTITY_EXTRACTORS: dict[
    str,
    tuple[
        type,
        Callable[
            ..., tuple[dict[str, object], list[ChromaDBCollection | TargetQueue]]
        ],
    ],
] = {
    "RECEIPT_PLACE": (ReceiptPlace, _extract_receipt_place),
    "RECEIPT_WORD_LABEL": (ReceiptWordLabel, _extract_receipt_word_label),
    "RECEIPT": (Receipt, _extract_receipt),
    "RECEIPT_WORD": (ReceiptWord, _extract_receipt_word),
    "RECEIPT_LINE": (ReceiptLine, _extract_receipt_line),
}


def _extract_entity_data(
    entity_type: str,
    entity: (
        Receipt
        | ReceiptLine
        | ReceiptPlace
        | ReceiptWord
        | ReceiptWordLabel
        | None
    ),
) -> tuple[dict[str, object], list[ChromaDBCollection | TargetQueue]]:
    """Extract entity data and determine target collections/queues."""
    if not entity:
        return {}, []

    extractor_info = _ENTITY_EXTRACTORS.get(entity_type)
    if extractor_info:
        expected_class, extractor = extractor_info
        if isinstance(entity, expected_class):
            return extractor(entity)

    return {}, []


__all__ = ["build_messages_from_records"]
