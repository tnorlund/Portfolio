"""Line metadata creation for ChromaDB embeddings.

This module provides functions for creating and enriching line metadata
that will be stored in ChromaDB.
"""

from typing import List, Optional, TypedDict

from receipt_dynamo.entities import ReceiptLine, ReceiptWord

from receipt_chroma.embedding.utils.normalize import (
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
)


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
    prev_line: str
    next_line: str
    merchant_name: str
    source: str
    section_label: str  # Optional
    anchor_phone: str  # Optional, only if anchors exist
    anchor_address: str  # Optional, only if anchors exist
    anchor_url: str  # Optional, only if anchors exist
    normalized_phone_10: str  # Optional, only if anchors exist
    normalized_full_address: str  # Optional, only if anchors exist
    normalized_url: str  # Optional, only if anchors exist


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
