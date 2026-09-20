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

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo_stream.change_detection import (
    get_update_relevant_changes,
)
from receipt_dynamo_stream.models import (
    StreamMessage,
    StreamRecordContext,
    TargetQueue,
)
from receipt_dynamo_stream.parsing import parse_stream_record
from receipt_dynamo_stream.stream_types import (
    DynamoDBStreamRecord,
    MetricsRecorder,
)

logger = logging.getLogger(__name__)

# Entity types whose INSERT events must also produce change messages.
# ReceiptSection INSERTs matter because creating a canonical ITEMS
# section must (re)compute the receipt's line items; ReceiptSummary
# INSERTs are the line-item trigger. Other entity INSERTs are covered
# by the ingest pipeline itself.
_INSERT_SYNCED_ENTITY_TYPES = frozenset({"RECEIPT_SECTION", "RECEIPT_SUMMARY"})


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
        if event_name in {"INSERT", "MODIFY", "REMOVE"}:
            # build_entity_change_message drops INSERTs for every entity
            # type outside _INSERT_SYNCED_ENTITY_TYPES.
            entity_message = build_entity_change_message(record, metrics)
            if entity_message:
                messages.append(entity_message)

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

        if (
            record.get("eventName") == "INSERT"
            and entity_type not in _INSERT_SYNCED_ENTITY_TYPES
        ):
            return None

        changes = get_update_relevant_changes(
            entity_type, old_entity, new_entity
        )
        if metrics:
            metrics.count(
                "UpdateRelevantChanges",
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
) -> tuple[dict[str, object], list[TargetQueue]]:
    """Extract place data targeting the summary queue.

    Place changes affect merchant_name in ReceiptSummary.
    """
    return {
        "entity_type": "RECEIPT_PLACE",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
    }, [TargetQueue.RECEIPT_SUMMARY]


def _extract_receipt_word_label(
    entity: ReceiptWordLabel,
) -> tuple[dict[str, object], list[TargetQueue]]:
    """Extract word label data targeting the summary queue.

    Label changes affect the totals, tax and dates the summary extracts
    from labels.
    """
    return {
        "entity_type": "RECEIPT_WORD_LABEL",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
        "line_id": entity.line_id,
        "word_id": entity.word_id,
        "label": entity.label,
    }, [TargetQueue.RECEIPT_SUMMARY]


def _extract_receipt_summary(
    entity: ReceiptSummaryRecord,
) -> tuple[dict[str, object], list[TargetQueue]]:
    """Route summary changes to the LINE_ITEMS queue.

    The summary is written when a receipt's words, sections, labels and
    merchant all exist, so it is the correct trigger for line-item
    recomputation -- and resegmentation rewrites it, which regenerates
    line items automatically. The consumer refetches current state from
    DynamoDB; the event image is not trusted.
    """
    return {
        "entity_type": "RECEIPT_SUMMARY",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
    }, [TargetQueue.LINE_ITEMS]


def _extract_receipt_section(
    entity: ReceiptSection,
) -> tuple[dict[str, object], list[TargetQueue]]:
    """Route canonical ITEMS section changes to the LINE_ITEMS queue.

    A section invalidation or line_ids edit must recompute (or clear)
    the receipt's line items even when no summary rewrite follows. The
    message carries only (image_id, receipt_id): the consumer refetches
    the receipt's *current* sections from DynamoDB, so the event image
    is deliberately not trusted. ``section_type`` is included only for
    within-batch deduplication. Non-ITEMS sections produce no message;
    their ``section_type`` embedding attribute is refreshed inline by
    the vector-freshening leg.
    """
    targets: list[TargetQueue] = []
    if str(entity.section_type).upper() == "ITEMS":
        targets.append(TargetQueue.LINE_ITEMS)
    return {
        "entity_type": "RECEIPT_SECTION",
        "image_id": entity.image_id,
        "receipt_id": entity.receipt_id,
        "section_type": str(entity.section_type),
    }, targets


# Type alias for entity extractor functions
_EntityType = (
    ReceiptPlace | ReceiptSection | ReceiptSummaryRecord | ReceiptWordLabel
)
_ExtractorFunc = Callable[
    [_EntityType],
    tuple[dict[str, object], list[TargetQueue]],
]

# Entity type to (expected class, extractor function) mapping
_ENTITY_EXTRACTORS: dict[str, tuple[type, _ExtractorFunc]] = {
    "RECEIPT_PLACE": (ReceiptPlace, _extract_receipt_place),
    "RECEIPT_WORD_LABEL": (ReceiptWordLabel, _extract_receipt_word_label),
    "RECEIPT_SECTION": (ReceiptSection, _extract_receipt_section),
    "RECEIPT_SUMMARY": (ReceiptSummaryRecord, _extract_receipt_summary),
}


def _extract_entity_data(
    entity_type: str,
    entity: object,
) -> tuple[dict[str, object], list[TargetQueue]]:
    """Extract entity data and determine target queues."""
    if not entity:
        return {}, []

    extractor_info = _ENTITY_EXTRACTORS.get(entity_type)
    if extractor_info:
        expected_class, extractor = extractor_info
        if isinstance(entity, expected_class):
            return extractor(entity)

    return {}, []


__all__ = ["build_messages_from_records"]
