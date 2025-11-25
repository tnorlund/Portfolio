"""Word metadata creation for ChromaDB embeddings.

This module provides functions for creating and enriching word metadata
that will be stored in ChromaDB.
"""

from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_chroma.embedding.utils.normalize import (
    normalize_address,
    normalize_phone,
    normalize_url,
)


def create_word_metadata(
    word: ReceiptWord,
    left_word: str,
    right_word: str,
    merchant_name: Optional[str] = None,
    label_status: str = "unvalidated",
    source: str = "openai_embedding_batch",
) -> Dict[str, Any]:
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

    # Standardize merchant name format
    if merchant_name:
        merchant_name = merchant_name.strip().title()

    metadata = {
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
        "merchant_name": merchant_name,
        "label_status": label_status,
    }

    return metadata


def enrich_word_metadata_with_labels(
    metadata: Dict[str, Any],
    word_labels: List[ReceiptWordLabel],
) -> Dict[str, Any]:
    """
    Enrich word metadata with label information from DynamoDB.

    Args:
        metadata: Base metadata dictionary to enrich
        word_labels: List of ReceiptWordLabel entities for this word

    Returns:
        Enriched metadata dictionary
    """
    # Determine label status
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

    # Get auto suggestions
    auto_suggestions = [
        lbl
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.PENDING.value
    ]

    # label_confidence & label_proposed_by
    if auto_suggestions:
        last = sorted(auto_suggestions, key=lambda l: l.timestamp_added)[-1]
        label_confidence = getattr(last, "confidence", None)
        label_proposed_by = last.label_proposed_by
        if label_confidence is not None:
            metadata["label_confidence"] = label_confidence
        if label_proposed_by is not None:
            metadata["label_proposed_by"] = label_proposed_by

    # validated_labels — all labels with status VALID
    validated_labels = [
        lbl.label
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]

    # invalid_labels — all labels with status INVALID
    invalid_labels = [
        lbl.label
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.INVALID.value
    ]

    # Store validated labels with delimiters for exact matching
    if validated_labels:
        # Use comma delimiters to enable exact matching with $contains
        metadata["validated_labels"] = f",{','.join(validated_labels)},"
    else:
        metadata["validated_labels"] = ""

    # Store invalid labels with delimiters for exact matching
    if invalid_labels:
        # Use comma delimiters to enable exact matching with $contains
        metadata["invalid_labels"] = f",{','.join(invalid_labels)},"
    else:
        metadata["invalid_labels"] = ""

    # label_validated_at — timestamp of the most recent VALID
    valids = [
        lbl
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]
    if valids:
        label_validated_at = sorted(valids, key=lambda l: l.timestamp_added)[
            -1
        ].timestamp_added
        metadata["label_validated_at"] = label_validated_at

    return metadata


def enrich_word_metadata_with_anchors(
    metadata: Dict[str, Any],
    word: ReceiptWord,
) -> Dict[str, Any]:
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
        ext = getattr(word, "extracted_data", None) or {}
        etype = str(ext.get("type", "")).lower() if ext else ""
        val = ext.get("value") if ext else None
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
