"""
Message building logic for stream processor.

Constructs StreamMessage objects from parsed entities and changes.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from receipt_dynamo.entities.compaction_run import CompactionRun

from .change_detector import get_chromadb_relevant_changes
from .models import ChromaDBCollection, ParsedStreamRecord, StreamMessage

# Module-level logger
logger = logging.getLogger(__name__)


def build_messages_from_records(
    records: List[Dict[str, Any]], metrics=None
) -> List[StreamMessage]:
    """
    Build StreamMessage objects from DynamoDB stream records.

    Args:
        records: List of DynamoDB stream records
        metrics: Optional metrics collector

    Returns:
        List of StreamMessage objects to send to SQS
    """
    messages = []

    for record in records:
        # Handle COMPACTION_RUN INSERT events (fast-path)
        if record.get("eventName") == "INSERT":
            compaction_messages = build_compaction_run_messages(record, metrics)
            messages.extend(compaction_messages)
        # Handle MODIFY and REMOVE events
        elif record.get("eventName") in ["MODIFY", "REMOVE"]:
            message = build_entity_change_message(record, metrics)
            if message:
                messages.append(message)

    return messages


def build_compaction_run_messages(
    record: Dict[str, Any], metrics=None
) -> List[StreamMessage]:
    """
    Build messages for COMPACTION_RUN INSERT events.

    Creates two messages: one for lines collection, one for words collection.

    Args:
        record: DynamoDB stream record
        metrics: Optional metrics collector

    Returns:
        List of StreamMessage objects (empty if not a compaction run)
    """
    from .compaction_run import is_compaction_run, parse_compaction_run

    messages: List[StreamMessage] = []

    try:
        new_image = record.get("dynamodb", {}).get("NewImage")
        keys = record.get("dynamodb", {}).get("Keys", {})
        pk = keys.get("PK", {}).get("S", "")
        sk = keys.get("SK", {}).get("S", "")

        if not (new_image and is_compaction_run(pk, sk)):
            return messages

        # Parse the compaction run
        compaction_run = parse_compaction_run(new_image, pk, sk)

        # Build entity data
        cr_entity = {
            "run_id": compaction_run.run_id,
            "image_id": compaction_run.image_id,
            "receipt_id": compaction_run.receipt_id,
            "lines_delta_prefix": compaction_run.lines_delta_prefix,
            "words_delta_prefix": compaction_run.words_delta_prefix,
        }

        # Create one message per collection
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
                event_name="INSERT",
                collections=[collection],
                source="dynamodb_stream",
                timestamp=datetime.now(timezone.utc).isoformat(),
                stream_record_id=record.get("eventID", "unknown"),
                aws_region=record.get("awsRegion", "unknown"),
            )
            messages.append(stream_msg)

        logger.info(
            f"Created compaction run messages",
            extra={
                "run_id": compaction_run.run_id,
                "image_id": compaction_run.image_id,
            },
        )

    except Exception as e:
        logger.error(f"Failed to build compaction run message: {e}")
        if metrics:
            metrics.count("CompactionRunMessageBuildError", 1)

    return messages


def build_entity_change_message(
    record: Dict[str, Any], metrics=None
) -> StreamMessage | None:
    """
    Build a StreamMessage from an entity change (MODIFY/REMOVE) record.

    Args:
        record: DynamoDB stream record with parsed entity
        metrics: Optional metrics collector

    Returns:
        StreamMessage or None if no relevant changes
    """
    from .parsers import parse_stream_record

    try:
        # Parse the stream record
        parsed_record = parse_stream_record(record, metrics)
        if not parsed_record:
            return None

        entity_type = parsed_record.entity_type
        old_entity = parsed_record.old_entity
        new_entity = parsed_record.new_entity

        # Check for ChromaDB-relevant changes
        changes = get_chromadb_relevant_changes(entity_type, old_entity, new_entity)

        if metrics:
            metrics.count(
                "ChromaDBRelevantChanges",
                len(changes),
                {"entity_type": entity_type},
            )

        # Always process REMOVE events, even without specific field changes
        if not changes and record.get("eventName") != "REMOVE":
            return None

        # Extract entity identification data and determine target collections
        entity = old_entity or new_entity
        entity_data, target_collections = _extract_entity_data(entity_type, entity)

        if not entity_data or not target_collections:
            return None

        # Create enhanced stream message
        stream_msg = StreamMessage(
            entity_type=entity_type,
            entity_data=entity_data,
            changes=changes,
            event_name=record.get("eventName", "UNKNOWN"),
            collections=target_collections,
            source="dynamodb_stream",
            timestamp=datetime.now(timezone.utc).isoformat(),
            stream_record_id=record.get("eventID", "unknown"),
            aws_region=record.get("awsRegion", "unknown"),
        )

        logger.info(
            f"Created {entity_type} message",
            extra={
                "target_collections": [c.value for c in target_collections],
                "change_count": len(changes),
            },
        )

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

        return stream_msg

    except Exception as e:
        logger.error(f"Failed to build entity change message: {e}")
        if metrics:
            metrics.count("EntityMessageBuildError", 1)
        return None


def _extract_entity_data(
    entity_type: str,
    entity: ReceiptMetadata | ReceiptWordLabel,
) -> Tuple[Dict[str, Any], List[ChromaDBCollection]]:
    """
    Extract entity data and determine target collections.

    Args:
        entity_type: Type of entity
        entity: Parsed entity object

    Returns:
        Tuple of (entity_data dict, list of target collections)
    """
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
    else:
        return {}, []

    return entity_data, target_collections

