"""
Entity parsing logic for DynamoDB stream records.

Handles parsing of stream records into typed entities using receipt_dynamo
parsers so stream handlers can remain lightweight.
"""

import logging
from typing import Mapping, Optional, Protocol, cast

from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_line import item_to_receipt_line
from receipt_dynamo.entities.receipt_place import item_to_receipt_place
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)

from receipt_dynamo_stream.models import ParsedStreamRecord, StreamEntity

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


def detect_entity_type(  # pylint: disable=too-many-return-statements
    sk: str,
) -> Optional[str]:
    """Detect entity type from SK pattern.

    SK patterns (most specific first):
    - RECEIPT_WORD_LABEL: RECEIPT#00001#LINE#00001#WORD#00001#LABEL#...
    - RECEIPT_WORD: RECEIPT#00001#LINE#00001#WORD#00001
    - RECEIPT_LINE: RECEIPT#00001#LINE#00001
    - RECEIPT_PLACE: IMAGE#...#RECEIPT#00001#PLACE
    - COMPACTION_RUN: IMAGE#...#COMPACTION_RUN#...
    - RECEIPT: RECEIPT#00001 (no suffix)
    """
    if "#PLACE" in sk:
        return "RECEIPT_PLACE"
    if "#LABEL#" in sk:
        return "RECEIPT_WORD_LABEL"
    if "#COMPACTION_RUN#" in sk:
        return "COMPACTION_RUN"
    # Check for RECEIPT_WORD before RECEIPT_LINE (more specific first)
    # RECEIPT_WORD: RECEIPT#00001#LINE#00001#WORD#00001
    if "#WORD#" in sk and "#LINE#" in sk:
        return "RECEIPT_WORD"
    # RECEIPT_LINE: RECEIPT#00001#LINE#00001
    if "#LINE#" in sk:
        return "RECEIPT_LINE"
    # RECEIPT entity: SK is just "RECEIPT#{receipt_id:05d}" without any suffix
    # Must check after more specific patterns to avoid false matches
    if sk.startswith("RECEIPT#") and sk.count("#") == 1:
        return "RECEIPT"
    return None


def parse_entity(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-return-statements
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
                    "has_validation_status": "validation_status"
                    in complete_item,
                },
            )

        if entity_type == "RECEIPT_PLACE":
            return item_to_receipt_place(complete_item)
        if entity_type == "RECEIPT_WORD_LABEL":
            return item_to_receipt_word_label(complete_item)
        if entity_type == "RECEIPT":
            return item_to_receipt(complete_item)
        if entity_type == "RECEIPT_WORD":
            return item_to_receipt_word(complete_item)
        if entity_type == "RECEIPT_LINE":
            return item_to_receipt_line(complete_item)

    except ValueError:
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
    except (TypeError, KeyError, AttributeError) as exc:  # pragma: no cover
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

        old_entity = parse_entity(
            old_image, entity_type, "old", pk, sk, metrics
        )
        new_entity = parse_entity(
            new_image, entity_type, "new", pk, sk, metrics
        )

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
        logger.warning(
            "Failed to parse stream record", extra={"error": str(exc)}
        )
        if metrics:
            metrics.count("StreamRecordParsingError", 1)
        return None
