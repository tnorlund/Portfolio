"""
CompactionRun-specific parsing and handling logic.

Handles fast-path processing of COMPACTION_RUN INSERT events.
"""

from typing import Any, Dict


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
) -> Dict[str, Any]:
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
    # Extract identifiers
    run_id = new_image.get("run_id", {}).get("S") or sk.split("#")[-1]
    image_id = pk.split("#", 1)[-1]

    # receipt_id is typically zero-padded in SK: RECEIPT#00001#...
    receipt_token = sk.split("#")[1] if "#" in sk else ""
    try:
        receipt_id = int(
            receipt_token.replace("RECEIPT", "").replace("#", "") or 0
        )
    except Exception:
        # Fall back to attribute if present
        receipt_id = int(new_image.get("receipt_id", {}).get("N", 0))

    lines_delta_prefix = new_image.get("lines_delta_prefix", {}).get("S")
    words_delta_prefix = new_image.get("words_delta_prefix", {}).get("S")

    return {
        "run_id": run_id,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "lines_delta_prefix": lines_delta_prefix,
        "words_delta_prefix": words_delta_prefix,
    }


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
