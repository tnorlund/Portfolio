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
from receipt_label.utils import get_clients

_, _, pinecone_index = get_clients()


def _is_phone_number(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return (
        len(digits) == 10
        or (len(digits) == 11 and digits.startswith("1"))
        or (len(digits) == 12 and digits.startswith("01"))  # rare edge
    )


def _merged_phone_candidate_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
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
    word: ReceiptWord, label: ReceiptWordLabel
) -> LabelValidationResult:
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
            looks_like_phone=False,
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
