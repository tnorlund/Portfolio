"""Merchant name label validation logic."""

# pylint: disable=duplicate-code

import re
from typing import Optional

from rapidfuzz.fuzz import ratio
from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import (
    normalize_text,
    pinecone_id_from_label,
)
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

from receipt_dynamo.entities import ReceiptMetadata  # type: ignore
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel


def _merged_merchant_name_candidates_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
    """Return possible merchant name strings from the word and neighbors."""

    current = word.text.strip()
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if left and left != "<EDGE>":
        variants.append(f"{left.strip()} {current}")
        variants.append(f"{left.strip()}{current}")

    if right and right != "<EDGE>":
        variants.append(f"{current} {right.strip()}")
        variants.append(f"{current}{right.strip()}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        variants.append(f"{left.strip()} {current} {right.strip()}")
        variants.append(f"{left.strip()}{current}{right.strip()}")

    return variants


def validate_merchant_name_pinecone(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    merchant_name: str,
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate merchant name using Pinecone search by merchant."""

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
            "valid_labels": {"$in": ["MERCHANT_NAME"]},
            "merchant_name": merchant_name,
        },
        namespace="words",
    )

    matches = query_response.matches
    avg_similarity = (
        sum(match.score for match in matches) / len(matches)
        if matches
        else 0.0
    )

    looks_like_name = (
        word.text.isupper()
        and len(word.text.strip()) > 3
        and not re.search(r"\d", word.text)
    )

    is_consistent = avg_similarity > 0.7 and looks_like_name

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


def validate_merchant_name_google(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    metadata: ReceiptMetadata,
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate merchant name using Google-retrieved canonical name."""

    # Get pinecone index from client manager
    if client_manager is None:
        client_manager = get_client_manager()
    pinecone_index = client_manager.pinecone

    pinecone_id = pinecone_id_from_label(label)
    fetch_response = pinecone_index.fetch(ids=[pinecone_id], namespace="words")
    vector_data = fetch_response.vectors.get(pinecone_id)
    vector_metadata = vector_data.metadata if vector_data else {}

    normalized_canonical = normalize_text(
        metadata.canonical_merchant_name or ""
    )

    variants = _merged_merchant_name_candidates_from_text(
        word, vector_metadata
    )
    best_score = max(
        ratio(normalize_text(v), normalized_canonical) for v in variants
    )
    is_consistent = best_score > 85

    return LabelValidationResult(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
        label=label.label,
        status="VALIDATED",
        is_consistent=is_consistent,
        avg_similarity=best_score / 100.0,
        neighbors=[],
        pinecone_id=pinecone_id,
    )
