"""Message ordering and deduplication for Standard SQS queue processing.

Standard queues don't guarantee message ordering, so we sort within each batch:
1. REMOVE operations first (priority 0)
2. INSERT/MODIFY operations after (priority 1)

Within-batch deduplication prevents orphaned embeddings when a delete and
insert for the same entity arrive in the same batch.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from receipt_dynamo_stream.models import StreamMessage

logger = logging.getLogger(__name__)


def _get_entity_key(msg: "StreamMessage") -> tuple[str, ...]:
    """Extract entity key for deduplication.

    Returns a tuple identifying the entity being operated on:
    - For RECEIPT/RECEIPT_PLACE: (image_id, receipt_id)
    - For RECEIPT_LINE/RECEIPT_LINE_LABEL: (image_id, receipt_id, line_id)
    - For RECEIPT_WORD/RECEIPT_WORD_LABEL: (image_id, receipt_id, line_id, word_id)
    - For others: (entity_type, str(entity_data))

    Note:
        Entity data comes from DynamoDB streams where required fields (image_id,
        receipt_id, etc.) are always present per the table schema. Empty string
        defaults are defensive but not expected in practice. The fallback for
        unknown entity types uses sorted items which handles simple dict values.

    Args:
        msg: StreamMessage to extract key from

    Returns:
        Tuple uniquely identifying the entity
    """
    entity_data = msg.entity_data
    entity_type = msg.entity_type

    if entity_type in ("RECEIPT", "RECEIPT_PLACE"):
        return (
            str(entity_data.get("image_id", "")),
            str(entity_data.get("receipt_id", "")),
        )

    if entity_type in ("RECEIPT_LINE", "RECEIPT_LINE_LABEL"):
        return (
            str(entity_data.get("image_id", "")),
            str(entity_data.get("receipt_id", "")),
            str(entity_data.get("line_id", "")),
        )

    if entity_type in ("RECEIPT_WORD", "RECEIPT_WORD_LABEL"):
        return (
            str(entity_data.get("image_id", "")),
            str(entity_data.get("receipt_id", "")),
            str(entity_data.get("line_id", "")),
            str(entity_data.get("word_id", "")),
        )

    # Fallback for other entity types
    return (entity_type, str(sorted(entity_data.items())))


def sort_and_deduplicate_messages(
    messages: list["StreamMessage"],
) -> list["StreamMessage"]:
    """Sort messages (REMOVE first) and deduplicate within batch.

    Standard queues don't guarantee ordering, so we sort within each batch:
    1. REMOVE operations first (priority 0)
    2. INSERT/MODIFY operations after (priority 1)

    Within-batch deduplication: If a REMOVE and INSERT/MODIFY exist for the
    same entity in the same batch, we drop the INSERT/MODIFY since the entity
    is being deleted anyway.

    Args:
        messages: List of StreamMessage objects

    Returns:
        Sorted and deduplicated list of StreamMessage objects
    """
    if not messages:
        return []

    # Sort: REMOVE first, then INSERT/MODIFY (secondary sort by timestamp)
    # Use empty string as fallback for None timestamps to avoid comparison crash
    sorted_messages = sorted(
        messages,
        key=lambda m: (
            0 if m.event_name == "REMOVE" else 1,
            m.context.timestamp or "",
        ),
    )

    # Track entities being deleted for within-batch deduplication
    deleted_entities: set[tuple[str, ...]] = set()
    result: list["StreamMessage"] = []

    for msg in sorted_messages:
        entity_key = _get_entity_key(msg)

        if msg.event_name == "REMOVE":
            # Process deletes and track the entity
            result.append(msg)
            deleted_entities.add(entity_key)
        else:
            # Skip INSERT/MODIFY if entity was deleted in same batch
            if entity_key in deleted_entities:
                logger.info(
                    "Skipping %s for entity deleted in same batch",
                    msg.event_name,
                    extra={
                        "entity_type": msg.entity_type,
                        "entity_key": str(entity_key),
                    },
                )
                continue
            result.append(msg)

    skipped_count = len(messages) - len(result)
    if skipped_count > 0:
        logger.info(
            "Within-batch deduplication",
            extra={
                "original_count": len(messages),
                "result_count": len(result),
                "skipped_count": skipped_count,
            },
        )

    return result
