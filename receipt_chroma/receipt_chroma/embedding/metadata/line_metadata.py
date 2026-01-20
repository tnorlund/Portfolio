"""Line metadata creation for ChromaDB embeddings.

This module provides functions for creating and enriching line metadata
that will be stored in ChromaDB.

Row-based approach (v2):
Visual rows may contain multiple ReceiptLine entities when Apple Vision OCR
splits a row (e.g., product name on left, price on right). The row_line_ids
field tracks all line IDs in a visual row, while the primary line_id is the
first (leftmost) line in the row.
"""

from collections.abc import Sequence
from typing import List, Optional, TypedDict

from receipt_chroma.embedding.utils.normalize import (
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
)
from receipt_dynamo.entities import ReceiptLine, ReceiptWord


class LineMetadata(TypedDict, total=False):
    """Metadata structure for line embeddings in ChromaDB.

    For row-based embeddings (v2):
    - line_id: Primary line ID (first/leftmost line in visual row)
    - row_line_ids: JSON string of all line IDs in the visual row
    - text: Combined text of all lines in the visual row

    Legacy fields (for backward compatibility):
    - prev_line, next_line: Legacy context fields
    """

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
    # Row-based fields (v2)
    row_line_ids: str  # JSON array of line IDs in the visual row


def create_line_metadata(
    line: ReceiptLine,
    prev_line: str,
    next_line: str,
    merchant_name: Optional[str] = None,
    avg_word_confidence: Optional[float] = None,
    section_label: Optional[str] = None,
    source: str = "openai_embedding_batch",
) -> LineMetadata:
    """
    Create comprehensive metadata for a line embedding.

    Args:
        line: The ReceiptLine entity
        prev_line: Previous line text (or "<EDGE>")
        next_line: Next line text (or "<EDGE>")
        merchant_name: Optional merchant name
        avg_word_confidence: Optional average word confidence
        section_label: Optional section label
        source: Source identifier (default: "openai_embedding_batch")

    Returns:
        Dictionary of metadata for ChromaDB
    """
    # Use line confidence if avg_word_confidence not provided
    if avg_word_confidence is None:
        avg_word_confidence = line.confidence

    # Standardize merchant name format
    if merchant_name:
        merchant_name = merchant_name.strip().title()

    metadata = {
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
        "merchant_name": merchant_name,
        "source": source,
    }

    # Add section label if available
    if section_label:
        metadata["section_label"] = section_label

    return metadata


def enrich_line_metadata_with_anchors(
    metadata: LineMetadata,
    line_words: List[ReceiptWord],
) -> LineMetadata:
    """
    Enrich line metadata with anchor fields (phone, address, URL) if available.

    Anchor-only enrichment: attach fields only if this line has anchor words.

    Args:
        metadata: Base metadata dictionary to enrich
        line_words: List of ReceiptWord entities for this line

    Returns:
        Enriched metadata dictionary
    """
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
                # Build from this single word; function will normalize
                addr = build_full_address_from_words([w])
                if addr:
                    anchor_address = addr
            elif etype == "url" and not anchor_url:
                u = normalize_url(val or getattr(w, "text", ""))
                if u:
                    anchor_url = u
            if anchor_phone and anchor_address and anchor_url:
                break

        if anchor_phone:
            metadata["normalized_phone_10"] = anchor_phone
        if anchor_address:
            metadata["normalized_full_address"] = anchor_address
        if anchor_url:
            metadata["normalized_url"] = anchor_url
    except Exception:
        # Silently fail - anchor enrichment is optional
        pass

    return metadata  # type: ignore[return-value]
    # Dict operations return Dict[str, Any], but structure matches TypedDict


def create_row_metadata(
    row_lines: Sequence[ReceiptLine],
    merchant_name: Optional[str] = None,
    source: str = "openai_embedding_batch",
) -> LineMetadata:
    """Create metadata for a visual row embedding.

    For row-based embeddings, the metadata includes:
    - Primary identifiers from the first (leftmost) line
    - Combined text from all lines in the row
    - row_line_ids tracking all line IDs in the visual row

    Args:
        row_lines: Lines in the visual row, sorted left-to-right
        merchant_name: Optional merchant name
        source: Source identifier (default: "openai_embedding_batch")

    Returns:
        Dictionary of metadata for ChromaDB

    Raises:
        ValueError: If row_lines is empty
    """
    import json

    if not row_lines:
        raise ValueError("Cannot create metadata for empty row")

    # Primary line is the first (leftmost) line
    primary_line = row_lines[0]

    # Combine text from all lines in the row
    combined_text = " ".join(line.text for line in row_lines)

    # Calculate bounding box spanning all lines
    min_x = min(line.bounding_box["x"] for line in row_lines)
    max_x = max(
        line.bounding_box["x"] + line.bounding_box["width"] for line in row_lines
    )
    min_y = min(line.bounding_box["y"] for line in row_lines)
    max_y = max(
        line.bounding_box["y"] + line.bounding_box["height"] for line in row_lines
    )

    # Calculate average confidence
    avg_confidence = sum(line.confidence for line in row_lines) / len(row_lines)

    # Standardize merchant name format
    if merchant_name:
        merchant_name = merchant_name.strip().title()

    # Collect all line IDs
    line_ids = [line.line_id for line in row_lines]

    metadata: LineMetadata = {
        "image_id": primary_line.image_id,
        "receipt_id": primary_line.receipt_id,
        "line_id": primary_line.line_id,  # Primary line ID
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
    """Enrich row metadata with anchor fields from all words in the row.

    Similar to enrich_line_metadata_with_anchors but operates on all words
    across all lines in the visual row.

    Args:
        metadata: Base metadata dictionary to enrich
        row_words: List of ReceiptWord entities for all lines in the row

    Returns:
        Enriched metadata dictionary
    """
    # Reuse the existing enrichment logic
    return enrich_line_metadata_with_anchors(metadata, list(row_words))
