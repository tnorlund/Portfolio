"""
CompactionRun-specific parsing and handling logic.

Handles fast-path processing of COMPACTION_RUN INSERT events.
"""

from typing import Any, Dict

from receipt_dynamo.entities.compaction_run import (
    CompactionRun,
    item_to_compaction_run,
)


def is_compaction_run(pk: str, sk: str) -> bool:
    """
    Detect if PK/SK correspond to a CompactionRun item.

    Args:
        pk: Primary key
        sk: Sort key

    Returns:
        True if this is a CompactionRun item
    """
    return pk.startswith("IMAGE#") and "#COMPACTION_RUN#" in sk


def parse_compaction_run(
    new_image: Dict[str, Any], pk: str, sk: str
) -> CompactionRun:
    """
    Parse NewImage into a CompactionRun using shared parser.

    Args:
        new_image: DynamoDB NewImage from stream record
        pk: Primary key
        sk: Sort key

    Returns:
        Parsed CompactionRun entity

    Raises:
        ValueError: If parsing fails
    """
    complete_item = dict(new_image)
    complete_item["PK"] = {"S": pk}
    complete_item["SK"] = {"S": sk}

    # TYPE provided in item
    if "TYPE" not in complete_item:
        complete_item["TYPE"] = {"S": "COMPACTION_RUN"}

    return item_to_compaction_run(complete_item)


def is_embeddings_completed(new_image: Dict[str, Any]) -> bool:
    """
    Check if both lines and words embeddings are completed.

    Detects completion by checking:
    1. lines_state == "COMPLETED" AND words_state == "COMPLETED"
    2. OR both lines_finished_at and words_finished_at timestamps exist

    Args:
        new_image: DynamoDB NewImage from stream record

    Returns:
        True if embeddings are complete for both collections
    """
    if not new_image:
        return False

    # Check state fields (CompactionState: PENDING/PROCESSING/COMPLETED/FAILED)
    lines_state = new_image.get("lines_state", {}).get("S")
    words_state = new_image.get("words_state", {}).get("S")

    # Check timestamp fields (presence indicates completion)
    lines_finished = bool(
        new_image.get("lines_finished_at")
        and "S" in new_image.get("lines_finished_at", {})
    )
    words_finished = bool(
        new_image.get("words_finished_at")
        and "S" in new_image.get("words_finished_at", {})
    )

    # Both states are COMPLETED, or both timestamps exist
    return (lines_state == "COMPLETED" and words_state == "COMPLETED") or (
        lines_finished and words_finished
    )

