"""Line/row metadata creation for receipt embeddings.

The metadata shapes are backend-neutral and feed the native DynamoDB
embedding items.

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

from receipt_dynamo.entities import ReceiptLine, ReceiptWord

from receipt_embeddings.normalize import (
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
)

logger = logging.getLogger(__name__)


class LineMetadata(TypedDict, total=False):
    """Metadata structure for line embeddings."""

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
    # pylint: disable-next=broad-exception-caught
    except Exception:  # noqa: BLE001 - best-effort enrichment
        # CONTRACTUAL never-raise: anchors are optional metadata; a
        # malformed extracted_data blob must not fail the embedding write.
        logger.debug("Anchor enrichment failed for line", exc_info=True)

    return metadata


def create_row_metadata(
    row_lines: Sequence[ReceiptLine],
    merchant_name: Optional[str] = None,
    source: str = "openai_embedding_batch",
    section_label: Optional[str] = None,
) -> LineMetadata:
    """Create metadata for a visual row embedding.

    ``section_label`` (the row's receipt section, e.g. ``TOTAL_LINE``) is
    emitted only when set.
    """
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

    if section_label:
        metadata["section_label"] = section_label

    return metadata


def enrich_row_metadata_with_anchors(
    metadata: LineMetadata,
    row_words: Sequence[ReceiptWord],
) -> LineMetadata:
    """Enrich row metadata with anchor fields from all words in the row."""
    return enrich_line_metadata_with_anchors(metadata, list(row_words))


__all__ = [
    "LineMetadata",
    "create_row_metadata",
    "enrich_line_metadata_with_anchors",
    "enrich_row_metadata_with_anchors",
]
