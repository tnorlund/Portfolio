import re
from datetime import datetime, timezone

from rapidfuzz.fuzz import partial_ratio, ratio
from receipt_dynamo.entities import (
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

from receipt_label.label_validation.data import (
    LabelValidationResult,
)
from receipt_label.label_validation.utils import (
    normalize_text,
    pinecone_id_from_label,
)
from receipt_label.utils import get_clients

_, _, pinecone_index = get_clients()

SUFFIXES = {
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "trl": "trail",
    "hwy": "highway",
}

STATE_MAP = {
    "california": "ca",
    "new york": "ny",
    "texas": "tx",
    "florida": "fl",
    "illinois": "il",
}


def _fuzzy_in(text: str, target: str, threshold: int = 90) -> bool:
    return partial_ratio(text, target) >= threshold or any(
        ratio(text, token) >= threshold for token in target.split()
    )


def _merged_address_variants(word: ReceiptWord, metadata: dict) -> list[str]:
    current = normalize_text(word.text)
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if left and left != "<EDGE>":
        left_clean = normalize_text(left)
        variants.append(f"{left_clean} {current}")
        variants.append(f"{left_clean}{current}")

    if right and right != "<EDGE>":
        right_clean = normalize_text(right)
        variants.append(f"{current} {right_clean}")
        variants.append(f"{current}{right_clean}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        left_clean = normalize_text(left)
        right_clean = normalize_text(right)
        variants.append(f"{left_clean} {current} {right_clean}")
        variants.append(f"{left_clean}{current}{right_clean}")

    return variants


def validate_address(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    receipt_metadata: ReceiptMetadata,
) -> LabelValidationResult:
    pinecone_id = pinecone_id_from_label(label)
    canonical_address = (
        normalize_text(receipt_metadata.canonical_address)
        if receipt_metadata.canonical_address
        else ""
    )

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

    variants = _merged_address_variants(word, vector_data.metadata)

    looks_like_address = False
    for v in variants:
        if v in canonical_address:
            looks_like_address = True
            break
        if v in SUFFIXES and SUFFIXES[v] in canonical_address:
            looks_like_address = True
            break
        if re.match(r"\d{5}-\d{4}", v) and v[:5] in canonical_address:
            looks_like_address = True
            break
        if v in STATE_MAP and STATE_MAP[v] in canonical_address:
            looks_like_address = True
            break
        if v in STATE_MAP.values() and v in canonical_address:
            looks_like_address = True
            break
        if _fuzzy_in(v, canonical_address):
            looks_like_address = True
            break

    return LabelValidationResult(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
        label=label.label,
        status="VALIDATED",
        is_consistent=looks_like_address,
        avg_similarity=1.0 if looks_like_address else 0.0,
        neighbors=[],
        pinecone_id=pinecone_id,
    )
