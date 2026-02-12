"""Word metadata creation for ChromaDB embeddings.

This module provides functions for creating and enriching word metadata
that will be stored in ChromaDB.
"""

from typing import List, Optional, TypedDict

from receipt_chroma.embedding.utils.normalize import (
    normalize_address,
    normalize_phone,
    normalize_url,
)
from receipt_dynamo.constants import CORE_LABELS, ValidationStatus
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel


class WordMetadata(TypedDict, total=False):
    """Metadata structure for word embeddings in ChromaDB."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    source: str
    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float
    left: str
    right: str
    merchant_name: str
    label_status: str
    label_confidence: float  # Optional, only if labels exist
    label_proposed_by: str  # Optional, only if labels exist
    valid_labels_array: list[str]  # Canonical valid label array
    invalid_labels_array: list[str]  # Canonical invalid label array
    label_validated_at: str  # Optional, only if labels exist
    normalized_phone_10: str  # Optional, only if anchors exist
    normalized_full_address: str  # Optional, only if anchors exist
    normalized_url: str  # Optional, only if anchors exist


def create_word_metadata(
    word: ReceiptWord,
    left_word: str,
    right_word: str,
    merchant_name: Optional[str] = None,
    label_status: str = "unvalidated",
    source: str = "openai_embedding_batch",
) -> WordMetadata:
    """
    Create comprehensive metadata for a word embedding.

    Args:
        word: The ReceiptWord entity
        left_word: Left neighbor word text (or "<EDGE>")
        right_word: Right neighbor word text (or "<EDGE>")
        merchant_name: Optional merchant name
        label_status: Label validation status (default: "unvalidated")
        source: Source identifier (default: "openai_embedding_batch")

    Returns:
        Dictionary of metadata for ChromaDB
    """
    x_center, y_center = word.calculate_centroid()

    if merchant_name:
        merchant_name = merchant_name.strip().title()

    metadata: WordMetadata = {
        "image_id": word.image_id,
        "receipt_id": word.receipt_id,
        "line_id": word.line_id,
        "word_id": word.word_id,
        "source": source,
        "text": word.text,
        "x": x_center,
        "y": y_center,
        "width": word.bounding_box["width"],
        "height": word.bounding_box["height"],
        "confidence": word.confidence,
        "left": left_word,
        "right": right_word,
        "merchant_name": merchant_name or "",
        "label_status": label_status,
    }

    return metadata


def enrich_word_metadata_with_labels(
    metadata: WordMetadata,
    word_labels: List[ReceiptWordLabel],
) -> WordMetadata:
    """Enrich word metadata with label information from DynamoDB."""
    if any(
        lbl.validation_status == ValidationStatus.VALID.value
        for lbl in word_labels
    ):
        label_status = "validated"
    elif any(
        lbl.validation_status == ValidationStatus.PENDING.value
        for lbl in word_labels
    ):
        label_status = "auto_suggested"
    else:
        label_status = "unvalidated"

    metadata["label_status"] = label_status

    auto_suggestions = [
        lbl
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.PENDING.value
    ]

    if auto_suggestions:
        latest = sorted(auto_suggestions, key=lambda l: l.timestamp_added)[-1]
        label_confidence = getattr(latest, "confidence", None)
        label_proposed_by = latest.label_proposed_by
        if label_confidence is not None:
            metadata["label_confidence"] = label_confidence
        else:
            metadata.pop("label_confidence", None)
        if label_proposed_by is not None:
            metadata["label_proposed_by"] = label_proposed_by
        else:
            metadata.pop("label_proposed_by", None)
    else:
        metadata.pop("label_confidence", None)
        metadata.pop("label_proposed_by", None)

    valid_labels = {
        lbl.label.strip().upper()
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
        and lbl.label
        and lbl.label.strip().upper() in CORE_LABELS
    }
    invalid_labels = {
        lbl.label.strip().upper()
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.INVALID.value
        and lbl.label
        and lbl.label.strip().upper() in CORE_LABELS
    }
    invalid_labels -= valid_labels

    canonical_valid = sorted(valid_labels)
    canonical_invalid = sorted(invalid_labels)

    if canonical_valid:
        metadata["valid_labels_array"] = canonical_valid
    else:
        metadata.pop("valid_labels_array", None)

    if canonical_invalid:
        metadata["invalid_labels_array"] = canonical_invalid
    else:
        metadata.pop("invalid_labels_array", None)

    valids = [
        lbl
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]
    if valids:
        label_validated_at = sorted(valids, key=lambda l: l.timestamp_added)[
            -1
        ].timestamp_added
        if hasattr(label_validated_at, "isoformat"):
            metadata["label_validated_at"] = label_validated_at.isoformat()
        else:
            metadata["label_validated_at"] = str(label_validated_at)
    else:
        metadata.pop("label_validated_at", None)

    return metadata


def enrich_word_metadata_with_anchors(
    metadata: WordMetadata,
    word: ReceiptWord,
) -> WordMetadata:
    """
    Enrich word metadata with anchor fields (phone, address, URL) if available.

    Anchor-only enrichment based on this target word's extracted_data.

    Args:
        metadata: Base metadata dictionary to enrich
        word: The ReceiptWord entity with extracted_data

    Returns:
        Enriched metadata dictionary
    """
    try:
        extracted_data = getattr(word, "extracted_data", None)
        ext = extracted_data if isinstance(extracted_data, dict) else {}
        etype = str(ext.get("type", "")).lower() if ext else ""
        raw_value = ext.get("value") if ext else None
        val = str(raw_value) if raw_value is not None else None
        text = word.text

        if etype == "phone":
            ph = normalize_phone(val or text)
            if ph:
                metadata["normalized_phone_10"] = ph
        elif etype == "address":
            addr = normalize_address(val or text)
            if addr:
                metadata["normalized_full_address"] = addr
        elif etype == "url":
            url_norm = normalize_url(val or text)
            if url_norm:
                metadata["normalized_url"] = url_norm
    except Exception:
        # Silently fail - anchor enrichment is optional
        pass

    return metadata
