"""
Entity parsing logic for DynamoDB stream records.

Handles parsing of stream records into typed entities using receipt_dynamo
parsers so stream handlers can remain lightweight.
"""

# pylint: disable=import-error
# import-error: receipt_dynamo is a monorepo sibling installed at runtime

import logging
from typing import Callable, Optional

from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_line import item_to_receipt_line
from receipt_dynamo.entities.receipt_place import item_to_receipt_place
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)
from receipt_dynamo_stream.models import ParsedStreamRecord, StreamEntity
from receipt_dynamo_stream.stream_types import (
    DynamoDBItem,
    DynamoDBStreamRecord,
    MetricsRecorder,
    StreamRecordDynamoDB,
)

logger = logging.getLogger(__name__)

# SK pattern matchers in order of specificity (most specific first)
_SK_PATTERN_MATCHERS: list[tuple[Callable[[str], bool], str]] = [
    (lambda sk: "#PLACE" in sk, "RECEIPT_PLACE"),
    (lambda sk: "#LABEL#" in sk, "RECEIPT_WORD_LABEL"),
    (lambda sk: "#COMPACTION_RUN#" in sk, "COMPACTION_RUN"),
    (lambda sk: "#WORD#" in sk and "#LINE#" in sk, "RECEIPT_WORD"),
    (lambda sk: "#LINE#" in sk, "RECEIPT_LINE"),
    (lambda sk: sk.startswith("RECEIPT#") and sk.count("#") == 1, "RECEIPT"),
]

# Entity type to parser function mapping
_ENTITY_PARSERS: dict[
    str, Callable[[dict[str, dict[str, object]]], StreamEntity]
] = {
    "RECEIPT_PLACE": item_to_receipt_place,
    "RECEIPT_WORD_LABEL": item_to_receipt_word_label,
    "RECEIPT": item_to_receipt,
    "RECEIPT_WORD": item_to_receipt_word,
    "RECEIPT_LINE": item_to_receipt_line,
}


def detect_entity_type(sk: str) -> Optional[str]:
    """Detect entity type from SK pattern.

    SK patterns (most specific first):
    - RECEIPT_WORD_LABEL: RECEIPT#00001#LINE#00001#WORD#00001#LABEL#...
    - RECEIPT_WORD: RECEIPT#00001#LINE#00001#WORD#00001
    - RECEIPT_LINE: RECEIPT#00001#LINE#00001
    - RECEIPT_PLACE: IMAGE#...#RECEIPT#00001#PLACE
    - COMPACTION_RUN: IMAGE#...#COMPACTION_RUN#...
    - RECEIPT: RECEIPT#00001 (no suffix)
    """
    for matcher, entity_type in _SK_PATTERN_MATCHERS:
        if matcher(sk):
            return entity_type
    return None


def parse_entity(
    image: Optional[DynamoDBItem],
    entity_type: str,
    image_type: str,
    keys: tuple[str, str],
    metrics: Optional[MetricsRecorder] = None,
) -> Optional[StreamEntity]:
    """Parse DynamoDB image into typed entity.

    Args:
        image: DynamoDB image (NewImage or OldImage)
        entity_type: Type of entity being parsed
        image_type: "old" or "new" for logging
        keys: Tuple of (pk, sk) partition and sort keys
        metrics: Optional metrics recorder
    """
    if not image:
        return None

    pk, sk = keys

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

        parser = _ENTITY_PARSERS.get(entity_type)
        if parser:
            return parser(complete_item)

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
    record: DynamoDBStreamRecord, metrics: Optional[MetricsRecorder] = None
) -> Optional[ParsedStreamRecord]:
    """Parse DynamoDB stream record to identify relevant entity changes."""
    try:
        dynamodb: StreamRecordDynamoDB = record["dynamodb"]
        keys = dynamodb["Keys"]
        pk = keys["PK"]["S"]
        sk = keys["SK"]["S"]

        if not pk.startswith("IMAGE#"):
            return None

        entity_type = detect_entity_type(sk)
        if not entity_type:
            return None

        old_image: Optional[DynamoDBItem] = dynamodb.get("OldImage")
        new_image: Optional[DynamoDBItem] = dynamodb.get("NewImage")

        key_tuple = (pk, sk)
        old_entity = parse_entity(
            old_image, entity_type, "old", key_tuple, metrics
        )
        new_entity = parse_entity(
            new_image, entity_type, "new", key_tuple, metrics
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
