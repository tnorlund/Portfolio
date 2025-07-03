"""Currency label validation logic."""

# pylint: disable=duplicate-code

import re
from typing import Optional

from receipt_dynamo.entities import ReceiptWord  # type: ignore
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import pinecone_id_from_label
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def _is_currency(text: str) -> bool:
    """Return ``True`` if the text resembles a currency amount."""

    # Accept standard US currency formats like $1,234.56 or $10.00
    # Also accept without dollar sign like 1234.56
    return bool(
        re.match(r"^\$?\d{1,3}(,\d{3})*(\.\d{2})?$", text)
        or re.match(r"^\$?\d+(\.\d{2})?$", text)
    )


def _merged_currency_candidates_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
    """Return possible currency strings from the word and its neighbors."""

    current = word.text.strip()
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if left and left != "<EDGE>":
        variants.append(f"{left.strip()}{current}")

    if right and right != "<EDGE>":
        variants.append(f"{current}{right.strip()}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        variants.append(f"{left.strip()}{current}{right.strip()}")

    return variants


def validate_currency(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate that a word is a currency amount using Pinecone neighbors."""

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
            "valid_labels": {"$in": [label.label]},
        },
        namespace="words",
    )

    matches = query_response.matches
    avg_similarity = (
        sum(match.score for match in matches) / len(matches)
        if matches
        else 0.0
    )
    variants = _merged_currency_candidates_from_text(
        word, vector_data.metadata
    )
    looks_like_currency = any(_is_currency(v) for v in variants)

    is_consistent = avg_similarity > 0.75 and looks_like_currency

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
