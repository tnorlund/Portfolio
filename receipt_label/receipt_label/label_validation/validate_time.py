"""Time label validation logic."""

# pylint: disable=duplicate-code

import re
from typing import Optional

from receipt_dynamo.entities import ReceiptWord  # type: ignore
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import chroma_id_from_label
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

# Time format patterns
TIME_WITH_TZ_ABBR = r"^(\d{1,2}:\d{2}(:\d{2})?( ?[APap][Mm])?) ?([A-Z]{3,4})$"
TIME_WITH_TZ_OFFSET = r"^(\d{1,2}:\d{2}:\d{2})[+-]\d{2}:\d{2}$"
TIME_WITH_Z = r"^(\d{1,2}:\d{2}:\d{2})Z$"
TIME_BASIC = r"^(\d{1,2}):(\d{2})(:\d{2})?( ?[APap][Mm])?$"


def _is_time(text: str) -> bool:
    """Return ``True`` if the text resembles a valid time."""

    # More comprehensive time validation including timezone support
    text = text.strip()

    # Time with timezone patterns
    timezone_patterns = [
        TIME_WITH_TZ_ABBR,
        TIME_WITH_TZ_OFFSET,
        TIME_WITH_Z,
    ]

    # Standard time patterns
    basic_patterns = [
        TIME_BASIC,
    ]

    # Check if it matches any timezone pattern first
    for pattern in timezone_patterns:
        match = re.match(pattern, text)
        if match:
            # Extract the time part for validation
            time_part = match.group(1)
            return _validate_time_components(time_part)

    # Check basic patterns
    for pattern in basic_patterns:
        match = re.match(pattern, text)
        if match:
            return _validate_time_components(text)

    return False


def _validate_time_components(time_str: str) -> bool:
    """Validate the actual time values for range and logic."""
    # Extract components
    am_pm_pattern = r"^(\d{1,2}):(\d{2})(:\d{2})?( ?[APap][Mm])?$"
    match = re.match(am_pm_pattern, time_str.strip())

    if not match:
        return False

    hour = int(match.group(1))
    minute = int(match.group(2))
    second_part = match.group(3)
    am_pm = match.group(4)

    second = 0
    if second_part:
        second = int(second_part[1:])  # Remove the ':'

    # Validate ranges
    if minute >= 60 or second >= 60:
        return False

    if am_pm:  # 12-hour format with AM/PM
        am_pm = am_pm.strip().upper()
        if hour < 1 or hour > 12:
            return False
        # Check for invalid combinations
        if hour == 0 and am_pm in ["AM", "PM"]:  # 00:00 AM is invalid
            return False
    else:  # 24-hour format
        if hour >= 24:
            return False

    return True


def _merged_time_candidate_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
    """Return possible time strings from the word and its neighbors."""

    current = word.text.strip()
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if right and right != "<EDGE>":
        variants.append(f"{current} {right.strip()}")
        variants.append(f"{current}{right.strip()}")

    if left and left != "<EDGE>":
        variants.append(f"{left.strip()} {current}")
        variants.append(f"{left.strip()}{current}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        variants.append(f"{left.strip()} {current} {right.strip()}")
        variants.append(f"{left.strip()}{current}{right.strip()}")

    return variants


def validate_time(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate that a word is a time using Pinecone neighbors."""

    # Get ChromaDB client from client manager
    if client_manager is None:
        client_manager = get_client_manager()
    chroma_client = client_manager.chroma

    chroma_id = chroma_id_from_label(label)
    # Get vector from ChromaDB
    results = chroma_client.get_by_ids(
        "words", [chroma_id], include=["embeddings", "metadatas"]
    )

    # Extract vector data
    vector_data = None
    if results and "ids" in results and len(results["ids"]) > 0:
        idx = (
            results["ids"].index(chroma_id)
            if chroma_id in results["ids"]
            else -1
        )
        if idx >= 0:
            vector_data = {
                "values": (
                    results["embeddings"][idx]
                    if "embeddings" in results
                    else None
                ),
                "metadata": (
                    results["metadatas"][idx] if "metadatas" in results else {}
                ),
            }

    if vector_data is None:
        return LabelValidationResult(
            image_id=label.image_id,
            receipt_id=label.receipt_id,
            line_id=label.line_id,
            word_id=label.word_id,
            label=label.label,
            status="NO_VECTOR",
            is_consistent=False,
            avg_similarity=0.0,
            neighbors=[],
            chroma_id=chroma_id,
        )

    vector = vector_data.values
    query_response = pinecone_index.query(
        vector=vector,
        top_k=10,
        include_metadata=True,
        filter={
            "valid_labels": {"$in": ["TIME"]},
        },
        namespace="words",
    )

    matches = query_response.matches
    avg_similarity = (
        sum(match.score for match in matches) / len(matches)
        if matches
        else 0.0
    )

    variants = _merged_time_candidate_from_text(word, vector_data.metadata)
    looks_like_time = any(_is_time(v) for v in variants)

    is_consistent = avg_similarity > 0.7 and looks_like_time

    return LabelValidationResult(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
        label=label.label,
        status="VALIDATED",
        is_consistent=is_consistent,
        avg_similarity=avg_similarity,
        neighbors=[match.id for match in matches],
        pinecone_id=pinecone_id,
    )
