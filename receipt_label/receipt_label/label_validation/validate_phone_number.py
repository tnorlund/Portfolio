"""Phone number label validation logic."""

# pylint: disable=duplicate-code

import re
from typing import Optional

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import pinecone_id_from_label
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

from receipt_dynamo.entities import ReceiptWord  # type: ignore
from receipt_dynamo.entities import ReceiptWordLabel


def _is_phone_number(text: str) -> bool:
    """Return ``True`` if the text resembles a phone number."""

    digits = re.sub(r"\D", "", text)

    # Check basic length requirements
    if not (
        len(digits) == 10
        or (len(digits) == 11 and digits.startswith("1"))
        or len(digits) == 12
    ):
        return False

    # Additional format validation - check common patterns
    # Valid: 1234567890, 123-456-7890, (123) 456-7890, 1-123-456-7890, etc.
    # Invalid: 555-12-34567 (wrong grouping)
    common_patterns = [
        r"^\d{10}$",  # 1234567890
        r"^\d{3}-\d{3}-\d{4}$",  # 123-456-7890
        r"^\(\d{3}\) \d{3}-\d{4}$",  # (123) 456-7890
        r"^1\d{10}$",  # 11234567890
        r"^1-\d{3}-\d{3}-\d{4}$",  # 1-123-456-7890
        r"^\d{12}$",  # 123456789012 (international)
        r"^\+\d{11}$",  # +11234567890
    ]

    return any(re.match(pattern, text.strip()) for pattern in common_patterns)


def _merged_phone_candidate_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
    """Return possible phone number strings from the word and neighbors."""

    current = word.text.strip()
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if right and right != "<EDGE>":
        variants.append(f"{current}{right.strip()}")

    if left and left != "<EDGE>":
        variants.append(f"{left.strip()}{current}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        variants.append(f"{left.strip()}{current}{right.strip()}")

    return variants


def validate_phone_number(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate that a word is a phone number using Pinecone neighbors."""

    # Get pinecone index from client manager
    if client_manager is None:
        client_manager = get_client_manager()
    pinecone_index = client_manager.pinecone

    pinecone_id = pinecone_id_from_label(label)
    fetch_response = pinecone_index.fetch(ids=[pinecone_id], namespace="words")
    vector_data = fetch_response.vectors.get(pinecone_id)
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
            pinecone_id=pinecone_id,
        )
    vector = vector_data.values
    query_response = pinecone_index.query(
        vector=vector,
        top_k=10,
        include_metadata=True,
        filter={
            "valid_labels": {"$in": ["PHONE_NUMBER"]},
        },
        namespace="words",
    )

    matches = query_response.matches
    avg_similarity = (
        sum(match.score for match in matches) / len(matches)
        if matches
        else 0.0
    )

    variants = _merged_phone_candidate_from_text(word, vector_data.metadata)
    looks_like_phone = any(_is_phone_number(v) for v in variants)

    is_consistent = avg_similarity > 0.7 and looks_like_phone

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
