"""Word metadata creation for ChromaDB embeddings.

This module provides functions for creating and enriching word metadata
that will be stored in ChromaDB.

Label metadata uses boolean fields for each CORE_LABEL:
- `label_{NAME}: True` means the word is validated as that label
- `label_{NAME}: False` means the word was marked invalid for that label
- Field absent means the label hasn't been evaluated for this word

This enables efficient ChromaDB metadata filtering for RAG queries:
    collection.query(where={"label_GRAND_TOTAL": True})
"""

from typing import Any, Dict, List, Optional, TypedDict

from receipt_dynamo.constants import CORE_LABELS, ValidationStatus
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_chroma.embedding.utils.normalize import (
    normalize_address,
    normalize_phone,
    normalize_url,
)


def _label_field_name(label: str) -> str:
    """Generate the metadata field name for a label.

    Args:
        label: The label name (e.g., "GRAND_TOTAL")

    Returns:
        Field name with prefix (e.g., "label_GRAND_TOTAL")
    """
    return f"label_{label}"


class WordMetadata(TypedDict, total=False):
    """Metadata structure for word embeddings in ChromaDB.

    Label fields use boolean values with the naming convention `label_{NAME}`:
    - True = word is validated as this label
    - False = word was marked invalid for this label
    - Absent = label not evaluated

    Available label fields (from CORE_LABELS):
    - label_MERCHANT_NAME, label_STORE_HOURS, label_PHONE_NUMBER, etc.
    - label_ADDRESS_LINE
    - label_DATE, label_TIME, label_PAYMENT_METHOD, label_COUPON, label_DISCOUNT
    - label_PRODUCT_NAME, label_QUANTITY, label_UNIT_PRICE, label_LINE_TOTAL
    - label_SUBTOTAL, label_TAX, label_GRAND_TOTAL
    """

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
    label_validated_at: str  # Optional, only if labels exist
    # Boolean label fields are added dynamically: label_GRAND_TOTAL, label_TAX, etc.


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
    metadata: WordMetadata,
    word_labels: List[ReceiptWordLabel],
) -> WordMetadata:
    """
    Enrich word metadata with label information from DynamoDB.

    Sets boolean fields for each label:
    - `label_{NAME}: True` for validated labels
    - `label_{NAME}: False` for invalid labels
    - Field omitted for pending/unevaluated labels

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

    # Set boolean fields for each label
    # True = validated, False = invalid, absent = not evaluated
    for lbl in word_labels:
        field_name = _label_field_name(lbl.label)
        if lbl.validation_status == ValidationStatus.VALID.value:
            metadata[field_name] = True  # type: ignore[literal-required]
        elif lbl.validation_status == ValidationStatus.INVALID.value:
            metadata[field_name] = False  # type: ignore[literal-required]
        # PENDING labels are not added - they're "not yet validated"

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

    return metadata  # type: ignore[return-value]
    # Dict operations return Dict[str, Any], but structure matches TypedDict


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
