"""Line embedding delta creation.

This module provides functionality for saving line embedding results
as ChromaDB delta files for compaction.
"""

from typing import Dict, List, Optional, TypedDict

from receipt_chroma.embedding.delta.producer import produce_embedding_delta
from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
    parse_prev_next_from_formatted,
)
from receipt_chroma.embedding.metadata.line_metadata import (
    create_line_metadata,
    enrich_line_metadata_with_anchors,
)


class LineMetadataBase(TypedDict):
    """Base metadata structure for line embeddings."""

    image_id: str
    receipt_id: int
    line_id: int
    source: str


def _parse_metadata_from_line_id(custom_id: str) -> LineMetadataBase:
    """
    Parse metadata from a line ID in the format
    IMAGE#uuid#RECEIPT#00001#LINE#00001.

    Args:
        custom_id: Custom ID string in format IMAGE#<id>#RECEIPT#<id>#LINE#<id>

    Returns:
        Dictionary with image_id, receipt_id, line_id, and source

    Raises:
        ValueError: If the format is invalid
    """
    parts = custom_id.split("#")

    # Validate we have the expected format for line embeddings
    if len(parts) != 6:
        raise ValueError(
            f"Invalid custom_id format for line embedding: {custom_id}. "
            f"Expected format: IMAGE#<id>#RECEIPT#<id>#LINE#<id> (6 parts), "
            f"but got {len(parts)} parts"
        )

    # Additional validation: check that this is NOT a word embedding
    if "WORD" in parts:
        raise ValueError(
            f"Custom ID appears to be for word embedding, not line "
            f"embedding: {custom_id}"
        )

    return {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "source": "openai_line_embedding_batch",
    }


def save_line_embeddings_as_delta(
    results: List[dict],
    descriptions: Dict[str, Dict[int, dict]],
    batch_id: str,
    bucket_name: str,
    sqs_queue_url: Optional[str] = None,
) -> dict:
    """
    Save line embedding results as a delta file to S3 for ChromaDB compaction.

    This replaces the direct Pinecone upsert with a delta file that will be
    processed later by the compaction job.

    Args:
        results: The list of embedding results, each containing:
            - custom_id (str)
            - embedding (List[float])
        descriptions: A nested dict of receipt details keyed by
            image_id and receipt_id.
        batch_id: The identifier of the batch.
        bucket_name: S3 bucket name for storing the delta.
        sqs_queue_url: SQS queue URL for compaction notification.
            If None, skips SQS notification.

    Returns:
        dict: Delta creation result with keys:
            - delta_id: Unique identifier for the delta
            - delta_key: S3 key where delta was saved
            - embedding_count: Number of embeddings in the delta
    """
    # Prepare ChromaDB-compatible data
    ids = []
    embeddings = []
    metadatas = []
    documents = []

    for result in results:
        # Parse metadata from custom_id
        meta = _parse_metadata_from_line_id(result["custom_id"])
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]

        # Get receipt details
        receipt_details = descriptions[image_id][receipt_id]
        lines = receipt_details["lines"]
        words = receipt_details["words"]
        place = receipt_details["place"]

        # Find the target line
        target_line = next((l for l in lines if l.line_id == line_id), None)
        if not target_line:
            raise ValueError(
                f"No ReceiptLine found for image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}"
            )

        # Get line words for confidence calculation
        line_words = [w for w in words if w.line_id == line_id]
        avg_confidence = (
            sum(w.confidence for w in line_words) / len(line_words)
            if line_words
            else target_line.confidence
        )

        # Get line context
        embedding_input = format_line_context_embedding_input(
            target_line, lines
        )
        prev_line, next_line = parse_prev_next_from_formatted(embedding_input)

        # Priority: canonical name > regular merchant name
        if (
            hasattr(place, "canonical_merchant_name")
            and place.canonical_merchant_name
        ):
            merchant_name = place.canonical_merchant_name
        elif hasattr(place, "merchant_name") and place.merchant_name:
            merchant_name = place.merchant_name
        else:
            raise ValueError(
                f"No merchant name available for image_id={image_id}, "
                f"receipt_id={receipt_id}"
            )

        # Build metadata for ChromaDB using consolidated metadata creation
        section_label = getattr(target_line, "section_label", None) or None
        line_metadata = create_line_metadata(
            line=target_line,
            prev_line=prev_line,
            next_line=next_line,
            merchant_name=merchant_name,
            avg_word_confidence=avg_confidence,
            section_label=section_label,
            source="openai_embedding_batch",
        )

        # Anchor-only enrichment for lines: attach fields only if this line
        # has anchor words
        line_metadata = enrich_line_metadata_with_anchors(
            line_metadata, line_words
        )

        # Add to delta arrays
        ids.append(result["custom_id"])
        embeddings.append(result["embedding"])
        metadatas.append(line_metadata)
        documents.append(target_line.text)

    # Produce the delta file
    delta_result = produce_embedding_delta(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
        bucket_name=bucket_name,
        collection_name="lines",  # Must match ChromaDBCollection.LINES and
        # database_name
        database_name="lines",  # Separate database for line embeddings
        sqs_queue_url=sqs_queue_url,
        batch_id=batch_id,
    )

    return delta_result
