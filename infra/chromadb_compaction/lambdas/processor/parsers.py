"""
Entity parsing logic for DynamoDB stream records.

Handles parsing of stream records into typed entities using receipt_dynamo parsers.
"""

import logging
from typing import Any, Dict, Optional, Union

from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    item_to_receipt_metadata,
)
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
)

from .models import ParsedStreamRecord

# Module-level logger - will be replaced by tests if needed
logger = logging.getLogger(__name__)


def detect_entity_type(sk: str) -> Optional[str]:
    """
    Detect entity type from SK pattern.

    Args:
        sk: Sort key from DynamoDB item

    Returns:
        Entity type string or None if not relevant
    """
    if "#METADATA" in sk:
        return "RECEIPT_METADATA"
    if "#LABEL#" in sk:
        return "RECEIPT_WORD_LABEL"
    if "#COMPACTION_RUN#" in sk:
        return "COMPACTION_RUN"
    return None


def parse_entity(
    image: Optional[Dict[str, Any]],
    entity_type: str,
    image_type: str,
    pk: str,
    sk: str,
    metrics=None,
) -> Optional[Union[ReceiptMetadata, ReceiptWordLabel]]:
    """
    Parse DynamoDB image into typed entity.

    Args:
        image: DynamoDB image (OldImage or NewImage)
        entity_type: Type of entity to parse
        image_type: Description for logging (old/new)
        pk: Primary key for the item
        sk: Sort key for the item
        metrics: Optional metrics collector

    Returns:
        Parsed entity or None if parsing fails
    """
    if not image:
        return None

    try:
        # The entity parsers expect a complete DynamoDB item with PK, SK
        complete_item = dict(image)
        complete_item["PK"] = {"S": pk}
        complete_item["SK"] = {"S": sk}

        # Log detailed field information for RECEIPT_WORD_LABEL parsing diagnostics
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

    except ValueError as e:
        logger.error(
            f"Failed to parse entity - {image_type} {entity_type}: {e}",
            extra={
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

    except Exception as e:
        logger.error(
            f"Unexpected error parsing {image_type} {entity_type}: {e}",
            extra={
                "error_type": type(e).__name__,
                "pk": pk,
                "sk": sk,
            },
        )

        if metrics:
            metrics.count(
                "EntityParsingUnexpectedError",
                1,
                {"entity_type": entity_type, "error_type": type(e).__name__},
            )

    return None


def parse_stream_record(
    record: Dict[str, Any], metrics=None
) -> Optional[ParsedStreamRecord]:
    """
    Parse DynamoDB stream record to identify relevant entity changes.

    Uses receipt_dynamo entity parsers for proper validation and type safety.
    Only processes entities that affect ChromaDB metadata:
    - RECEIPT_METADATA: merchant info that affects all embeddings
    - RECEIPT_WORD_LABEL: labels that affect specific word embeddings

    Args:
        record: DynamoDB stream record
        metrics: Optional metrics collector

    Returns:
        ParsedStreamRecord or None if not relevant
    """
    try:
        # Extract keys to determine entity type
        keys = record["dynamodb"]["Keys"]
        pk = keys["PK"]["S"]
        sk = keys["SK"]["S"]

        # Only process IMAGE entities
        if not pk.startswith("IMAGE#"):
            return None

        # Determine entity type from SK pattern
        entity_type = detect_entity_type(sk)
        if not entity_type:
            return None

        # Get appropriate DynamoDB item for parsing
        old_image = record["dynamodb"].get("OldImage")
        new_image = record["dynamodb"].get("NewImage")

        # Parse entities using receipt_dynamo parsers
        old_entity = parse_entity(old_image, entity_type, "old", pk, sk, metrics)
        new_entity = parse_entity(new_image, entity_type, "new", pk, sk, metrics)

        # Enhanced diagnostic logging for parsing failures
        if old_image and not old_entity:
            logger.error(
                f"Failed to parse old {entity_type}",
                extra={
                    "available_keys": list(old_image.keys()),
                    "pk": pk,
                    "sk": sk,
                },
            )

        if new_image and not new_entity:
            logger.error(
                f"Failed to parse new {entity_type}",
                extra={
                    "available_keys": list(new_image.keys()),
                    "pk": pk,
                    "sk": sk,
                },
            )

        # Return parsed entity information
        return ParsedStreamRecord(
            entity_type=entity_type,
            old_entity=old_entity,
            new_entity=new_entity,
            pk=pk,
            sk=sk,
        )

    except (KeyError, ValueError) as e:
        logger.warning(f"Failed to parse stream record: {e}")

        if metrics:
            metrics.count("StreamRecordParsingError", 1)

        return None
