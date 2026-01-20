"""Line embedding delta creation.

This module provides functionality for saving line embedding results
as ChromaDB delta files for compaction.

Supports row-based embeddings where multiple ReceiptLine entities that appear
on the same visual row are grouped into a single embedding.
"""

from typing import Dict, List, Optional, TypedDict

from receipt_chroma.embedding.delta.producer import produce_embedding_delta
from receipt_chroma.embedding.formatting.line_format import (
    format_visual_row,
    get_primary_line_id,
    group_lines_into_visual_rows,
)
from receipt_chroma.embedding.metadata.line_metadata import (
    create_row_metadata,
    enrich_row_metadata_with_anchors,
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
    Save row-based line embedding results as a delta file to S3.

    With row-based embeddings, each result's custom_id contains the primary
    (leftmost) line's ID. This function finds all lines in that visual row
    and creates metadata for the entire row.

    Args:
        results: The list of embedding results, each containing:
            - custom_id (str) - format: IMAGE#X#RECEIPT#Y#LINE#Z where Z is
              the primary line_id of a visual row
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

    # Cache visual rows per receipt to avoid recomputing
    visual_rows_cache: Dict[tuple, Dict[int, list]] = {}

    def get_visual_rows_map(
        image_id: str, receipt_id: int, lines: list
    ) -> Dict[int, list]:
        """Get mapping of primary_line_id -> visual row for a receipt."""
        cache_key = (image_id, receipt_id)
        if cache_key not in visual_rows_cache:
            visual_rows = group_lines_into_visual_rows(lines)
            visual_rows_cache[cache_key] = {
                get_primary_line_id(row): row
                for row in visual_rows
                if row
            }
        return visual_rows_cache[cache_key]

    for result in results:
        # Parse metadata from custom_id
        meta = _parse_metadata_from_line_id(result["custom_id"])
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        primary_line_id = meta["line_id"]

        # Get receipt details
        receipt_details = descriptions[image_id][receipt_id]
        lines = receipt_details["lines"]
        words = receipt_details["words"]
        place = receipt_details["place"]

        # Get visual rows and find the target row
        rows_map = get_visual_rows_map(image_id, receipt_id, lines)
        target_row = rows_map.get(primary_line_id)
        if not target_row:
            raise ValueError(
                f"No visual row found with primary_line_id={primary_line_id} "
                f"for image_id={image_id}, receipt_id={receipt_id}"
            )

        # Get all words for lines in this row
        row_line_ids = {line.line_id for line in target_row}
        row_words = [w for w in words if w.line_id in row_line_ids]

        if not place.merchant_name:
            raise ValueError(
                f"No merchant name available for image_id={image_id}, "
                f"receipt_id={receipt_id}"
            )
        merchant_name = place.merchant_name

        # Build row metadata for ChromaDB
        row_metadata = create_row_metadata(
            row_lines=target_row,
            merchant_name=merchant_name,
            source="openai_embedding_batch",
        )

        # Anchor-only enrichment: attach anchor fields from all words in row
        row_metadata = enrich_row_metadata_with_anchors(row_metadata, row_words)

        # Document is the formatted visual row text
        document = format_visual_row(target_row)

        # Add to delta arrays
        ids.append(result["custom_id"])
        embeddings.append(result["embedding"])
        metadatas.append(row_metadata)
        documents.append(document)

    # Produce the delta file
    delta_result = produce_embedding_delta(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
        bucket_name=bucket_name,
        collection_name="lines",  # Must match ChromaDBCollection.LINES
        database_name="lines",  # Separate database for line embeddings
        sqs_queue_url=sqs_queue_url,
        batch_id=batch_id,
    )

    return delta_result
