"""Line metadata creation for ChromaDB embeddings.

This module provides functions for creating and enriching line metadata
that will be stored in ChromaDB.

Row-based approach (v2):
Visual rows may contain multiple ReceiptLine entities when Apple Vision OCR
splits a row (e.g., product name on left, price on right). The row_line_ids
field tracks all line IDs in a visual row, while the primary line_id is the
first (leftmost) line in the row.
"""

import json
import logging
from collections.abc import Sequence
from typing import List, Optional, TypedDict

from receipt_chroma.embedding.utils.normalize import (
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
)
from receipt_dynamo.constants import CORE_LABELS, ValidationStatus
from receipt_dynamo.entities import ReceiptLine, ReceiptWord, ReceiptWordLabel

logger = logging.getLogger(__name__)


class LineMetadata(TypedDict, total=False):
    """Metadata structure for line embeddings in ChromaDB."""

    image_id: str
    receipt_id: int
    line_id: int
    text: str
    confidence: float
    avg_word_confidence: float
    x: float
    y: float
    width: float
    height: float
    prev_line: str  # Legacy
    next_line: str  # Legacy
    merchant_name: str
    source: str
    section_label: str  # Optional
    anchor_phone: str  # Optional, only if anchors exist
    anchor_address: str  # Optional, only if anchors exist
    anchor_url: str  # Optional, only if anchors exist
    normalized_phone_10: str  # Optional, only if anchors exist
    normalized_full_address: str  # Optional, only if anchors exist
    normalized_url: str  # Optional, only if anchors exist
    row_line_ids: str  # JSON array of line IDs in the visual row
    label_status: str  # Optional: validated, auto_suggested, unvalidated
    valid_labels_array: list[str]  # Canonical valid label array
    invalid_labels_array: list[str]  # Canonical invalid label array


def create_line_metadata(
    line: ReceiptLine,
    prev_line: str,
    next_line: str,
    merchant_name: Optional[str] = None,
    avg_word_confidence: Optional[float] = None,
    section_label: Optional[str] = None,
    source: str = "openai_embedding_batch",
) -> LineMetadata:
    """Create comprehensive metadata for a line embedding."""
    if avg_word_confidence is None:
        avg_word_confidence = line.confidence

    if merchant_name:
        merchant_name = merchant_name.strip().title()

    metadata: LineMetadata = {
        "image_id": line.image_id,
        "receipt_id": line.receipt_id,
        "line_id": line.line_id,
        "text": line.text,
        "confidence": line.confidence,
        "avg_word_confidence": avg_word_confidence,
        "x": line.bounding_box["x"],
        "y": line.bounding_box["y"],
        "width": line.bounding_box["width"],
        "height": line.bounding_box["height"],
        "prev_line": prev_line,
        "next_line": next_line,
        "merchant_name": merchant_name or "",
        "source": source,
    }

    if section_label:
        metadata["section_label"] = section_label

    return metadata


def enrich_line_metadata_with_anchors(
    metadata: LineMetadata,
    line_words: List[ReceiptWord],
) -> LineMetadata:
    """Enrich line metadata with anchor fields (phone, address, URL)."""
    try:
        anchor_phone = ""
        anchor_address = ""
        anchor_url = ""
        for w in line_words:
            ext = getattr(w, "extracted_data", None) or {}
            etype = str(ext.get("type", "")).lower() if ext else ""
            val = ext.get("value") if ext else None
            if etype == "phone" and not anchor_phone:
                ph = normalize_phone(val or getattr(w, "text", ""))
                if ph:
                    anchor_phone = ph
            elif etype == "address" and not anchor_address:
                addr = build_full_address_from_words([w])
                if addr:
                    anchor_address = addr
            elif etype == "url" and not anchor_url:
                url_norm = normalize_url(val or getattr(w, "text", ""))
                if url_norm:
                    anchor_url = url_norm
            if anchor_phone and anchor_address and anchor_url:
                break

        if anchor_phone:
            metadata["normalized_phone_10"] = anchor_phone
        if anchor_address:
            metadata["normalized_full_address"] = anchor_address
        if anchor_url:
            metadata["normalized_url"] = anchor_url
    except Exception:
        logger.debug("Anchor enrichment failed for line", exc_info=True)

    return metadata


def create_row_metadata(
    row_lines: Sequence[ReceiptLine],
    merchant_name: Optional[str] = None,
    source: str = "openai_embedding_batch",
) -> LineMetadata:
    """Create metadata for a visual row embedding."""
    if not row_lines:
        raise ValueError("Cannot create metadata for empty row")

    primary_line = row_lines[0]
    combined_text = " ".join(line.text for line in row_lines)

    min_x = min(line.bounding_box["x"] for line in row_lines)
    max_x = max(
        line.bounding_box["x"] + line.bounding_box["width"]
        for line in row_lines
    )
    min_y = min(line.bounding_box["y"] for line in row_lines)
    max_y = max(
        line.bounding_box["y"] + line.bounding_box["height"]
        for line in row_lines
    )
    avg_confidence = sum(line.confidence for line in row_lines) / len(
        row_lines
    )

    if merchant_name:
        merchant_name = merchant_name.strip().title()

    line_ids = [line.line_id for line in row_lines]

    metadata: LineMetadata = {
        "image_id": primary_line.image_id,
        "receipt_id": primary_line.receipt_id,
        "line_id": primary_line.line_id,
        "text": combined_text,
        "confidence": avg_confidence,
        "avg_word_confidence": avg_confidence,
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
        "source": source,
        "row_line_ids": json.dumps(line_ids),
    }

    if merchant_name:
        metadata["merchant_name"] = merchant_name

    return metadata


def enrich_row_metadata_with_anchors(
    metadata: LineMetadata,
    row_words: Sequence[ReceiptWord],
) -> LineMetadata:
    """Enrich row metadata with anchor fields from all words in the row."""
    return enrich_line_metadata_with_anchors(metadata, list(row_words))


def enrich_row_metadata_with_labels(
    metadata: LineMetadata,
    row_words: Sequence[ReceiptWord],
    all_labels: Sequence[ReceiptWordLabel],
) -> LineMetadata:
    """Enrich row metadata with aggregated valid/invalid label arrays."""
    row_word_keys = {
        (w.image_id, w.receipt_id, w.line_id, w.word_id) for w in row_words
    }

    row_labels = [
        lbl
        for lbl in all_labels
        if (lbl.image_id, lbl.receipt_id, lbl.line_id, lbl.word_id)
        in row_word_keys
    ]

    has_validated = False
    has_pending = False
    valid_labels: set[str] = set()
    invalid_labels: set[str] = set()

    for lbl in row_labels:
        if not lbl.label:
            continue
        normalized_label = lbl.label.strip().upper()
        if not normalized_label:
            continue
        if normalized_label not in CORE_LABELS:
            continue

        status = lbl.validation_status
        if status == ValidationStatus.PENDING.value:
            has_pending = True
            continue
        if status == ValidationStatus.VALID.value:
            valid_labels.add(normalized_label)
            invalid_labels.discard(normalized_label)
            has_validated = True
        elif status == ValidationStatus.INVALID.value:
            if normalized_label not in valid_labels:
                invalid_labels.add(normalized_label)
            has_validated = True

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

    if has_validated:
        metadata["label_status"] = "validated"
    elif has_pending:
        metadata["label_status"] = "auto_suggested"
    else:
        metadata["label_status"] = "unvalidated"

    return metadata
