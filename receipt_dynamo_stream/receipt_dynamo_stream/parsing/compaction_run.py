# pylint: disable=duplicate-code
"""
CompactionRun-specific parsing and handling logic.

Handles fast-path processing of COMPACTION_RUN INSERT/MODIFY events.
"""

from typing import Any, Dict


def is_compaction_run(pk: str, sk: str) -> bool:
    """Detect if PK/SK correspond to a CompactionRun item."""
    return pk.startswith("IMAGE#") and "#COMPACTION_RUN#" in sk


def parse_compaction_run(
    new_image: Dict[str, Any], pk: str, sk: str
) -> Dict[str, Any]:
    """
    Parse NewImage into a CompactionRun using shared parser.

    Raises:
        ValueError: If parsing fails
    """
    if not pk or not sk:
        raise ValueError("PK and SK are required to parse compaction run")

    run_id = new_image.get("run_id", {}).get("S") or sk.split("#")[-1]
    image_id = pk.split("#", 1)[-1]

    receipt_token = sk.split("#")[1] if "#" in sk else ""
    try:
        receipt_id = int(
            receipt_token.replace("RECEIPT", "").replace("#", "") or 0
        )
    except (TypeError, ValueError):
        receipt_id = int(new_image.get("receipt_id", {}).get("N", 0))
        if receipt_id == 0:
            raise ValueError(
                f"Could not parse receipt_id from SK: {sk} or new_image"
            )

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
    """
    if not new_image:
        return False

    lines_state = new_image.get("lines_state", {}).get("S")
    words_state = new_image.get("words_state", {}).get("S")

    lines_finished = bool(
        new_image.get("lines_finished_at")
        and "S" in new_image.get("lines_finished_at", {})
    )
    words_finished = bool(
        new_image.get("words_finished_at")
        and "S" in new_image.get("words_finished_at", {})
    )

    return (lines_state == "COMPLETED" and words_state == "COMPLETED") or (
        lines_finished and words_finished
    )


__all__ = [
    "is_compaction_run",
    "is_embeddings_completed",
    "parse_compaction_run",
]
