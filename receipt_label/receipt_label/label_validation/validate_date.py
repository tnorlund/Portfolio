import re
from datetime import datetime, timezone

from rapidfuzz.fuzz import partial_ratio, ratio
from receipt_dynamo.entities import (
    ReceiptWord,
    ReceiptWordLabel,
)

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import (
    normalize_text,
    pinecone_id_from_label,
)
from typing import Optional
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def _is_date(text: str) -> bool:
    # Match MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD, MM/YYYY, MM-YYYY
    return bool(
        re.search(
            r"\b("
            r"\d{1,2}[/-]\d{1,2}[/-]?\d{2,4}"  # MM/DD/YYYY or MM-DD-YYYY or MM/DD/YY
            r"|"
            r"\d{4}[/-]\d{1,2}[/-]?\d{1,2}"  # YYYY-MM-DD or YYYY/MM/DD
            r"|"
            r"\d{1,2}[/-]\d{4}"  # MM/YYYY or MM-YYYY
            r")\b",
            text.strip(),
        )
    )


# Merge left and right words with current word to create date candidates
def _merged_date_candidates_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
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


def validate_date(
    word: ReceiptWord, 
    label: ReceiptWordLabel,
    client_manager: Optional[ClientManager] = None
) -> LabelValidationResult:
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
            "valid_labels": {"$in": ["DATE"]},
        },
        namespace="words",
    )

    matches = query_response.matches
    avg_similarity = (
        sum(match.score for match in matches) / len(matches)
        if matches
        else 0.0
    )

    # Try merged variants for date detection
    variants = _merged_date_candidates_from_text(word, vector_data.metadata)
    looks_like_date = any(_is_date(v) for v in variants)

    is_consistent = avg_similarity > 0.7 and looks_like_date

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
