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

