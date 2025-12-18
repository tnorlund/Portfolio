"""
Entity parsing logic for DynamoDB stream records.

Handles parsing of stream records into typed entities using receipt_dynamo
parsers so stream handlers can remain lightweight.
"""

import logging
from typing import Mapping, Optional, Protocol, cast

from receipt_dynamo_stream.models import ParsedStreamRecord, StreamEntity

from receipt_dynamo.entities.receipt_metadata import item_to_receipt_metadata
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)

logger = logging.getLogger(__name__)


class MetricsRecorder(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal protocol for metrics clients used in parsing."""

    def count(  # pylint: disable=unnecessary-ellipsis
        self,
        name: str,
        value: int,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> object:
        """Record a count metric."""
        ...


DynamoImage = Mapping[str, Mapping[str, object]]
StreamRecord = Mapping[str, object]


def detect_entity_type(sk: str) -> Optional[str]:
    """Detect entity type from SK pattern."""
    if "#METADATA" in sk:
        return "RECEIPT_METADATA"
    if "#LABEL#" in sk:
        return "RECEIPT_WORD_LABEL"
    if "#COMPACTION_RUN#" in sk:
        return "COMPACTION_RUN"
    return None


def parse_entity(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    image: Optional[DynamoImage],
    entity_type: str,
    image_type: str,
    pk: str,
    sk: str,
    metrics: Optional[MetricsRecorder] = None,
) -> Optional[StreamEntity]:
    """Parse DynamoDB image into typed entity."""
    if not image:
        return None

    try:
        complete_item = dict(image)
        complete_item["PK"] = {"S": pk}
        complete_item["SK"] = {"S": sk}

        if entity_type == "RECEIPT_WORD_LABEL":
            logger.info(
                "Attempting to parse RECEIPT_WORD_LABEL",
                extra={
                    "image_type": image_type,
                    "available_fields": list(complete_item.keys()),
                    "pk": pk,
                    "sk": sk,
                    "has_timestamp_added": "timestamp_added" in complete_item,
                    "has_reasoning": "reasoning" in complete_item,
                    "has_validation_status": "validation_status" in complete_item,
                },
            )

        if entity_type == "RECEIPT_METADATA":
            return item_to_receipt_metadata(complete_item)
        if entity_type == "RECEIPT_WORD_LABEL":
            return item_to_receipt_word_label(complete_item)

    except ValueError as exc:
        logger.exception(
            "Failed to parse entity",
            extra={
                "image_type": image_type,
                "entity_type": entity_type,
                "available_fields": list(image.keys()) if image else None,
                "pk": pk,
                "sk": sk,
            },
        )
        if metrics:
            metrics.count(
                "EntityParsingError",
                1,
                {"entity_type": entity_type, "image_type": image_type},
            )
    except (TypeError, KeyError) as exc:  # pragma: no cover
        logger.exception(
            "Unexpected error parsing entity",
            extra={
                "image_type": image_type,
                "entity_type": entity_type,
                "pk": pk,
                "sk": sk,
            },
        )
        if metrics:
            metrics.count(
                "EntityParsingUnexpectedError",
                1,
                {"entity_type": entity_type, "error_type": type(exc).__name__},
            )

    return None


def parse_stream_record(
    record: StreamRecord, metrics: Optional[MetricsRecorder] = None
) -> Optional[ParsedStreamRecord]:
    """Parse DynamoDB stream record to identify relevant entity changes."""
    try:
        dynamodb = cast(dict[str, object], record["dynamodb"])
        keys = cast(dict[str, object], dynamodb["Keys"])
        pk = cast(dict[str, str], keys["PK"])["S"]
        sk = cast(dict[str, str], keys["SK"])["S"]

        if not pk.startswith("IMAGE#"):
            return None

        entity_type = detect_entity_type(sk)
        if not entity_type:
            return None

        old_image = cast(Optional[DynamoImage], dynamodb.get("OldImage"))
        new_image = cast(Optional[DynamoImage], dynamodb.get("NewImage"))

        old_entity = parse_entity(old_image, entity_type, "old", pk, sk, metrics)
        new_entity = parse_entity(new_image, entity_type, "new", pk, sk, metrics)

        if old_image and not old_entity:
            logger.error(
                "Failed to parse old entity",
                extra={
                    "entity_type": entity_type,
                    "available_keys": list(old_image.keys()),
                    "pk": pk,
                    "sk": sk,
                },
            )

        if new_image and not new_entity:
            logger.error(
                "Failed to parse new entity",
                extra={
                    "entity_type": entity_type,
                    "available_keys": list(new_image.keys()),
                    "pk": pk,
                    "sk": sk,
                },
            )

        return ParsedStreamRecord(
            entity_type=entity_type,
            old_entity=old_entity,
            new_entity=new_entity,
            pk=pk,
            sk=sk,
        )

    except (KeyError, ValueError) as exc:
        logger.warning("Failed to parse stream record", extra={"error": str(exc)})
        if metrics:
            metrics.count("StreamRecordParsingError", 1)
        return None
