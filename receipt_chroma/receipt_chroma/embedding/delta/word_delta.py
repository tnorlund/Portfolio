"""Word embedding delta creation.

This module provides functionality for saving word embedding results
as ChromaDB delta files for compaction.
"""

from typing import Any, Dict, List, Optional

from receipt_chroma.embedding.delta.producer import (
    _produce_delta_for_collection,
)
from receipt_chroma.embedding.formatting.word_format import get_word_neighbors
from receipt_chroma.embedding.metadata.word_metadata import (
    create_word_metadata,
    enrich_word_metadata_with_anchors,
    enrich_word_metadata_with_labels,
)


def _parse_metadata_from_custom_id(custom_id: str) -> Dict[str, Any]:
    """
    Parse metadata from a word ID formatted as
    IMAGE#uuid#RECEIPT#00001#LINE#00001#WORD#00001.

    Args:
        custom_id: Custom ID string in the format
            IMAGE#<id>#RECEIPT#<id>#LINE#<id>#WORD#<id>

    Returns:
        Dictionary with image_id, receipt_id, line_id, word_id, and source

    Raises:
        ValueError: If the format is invalid
    """
    parts = custom_id.split("#")

    # Validate we have the expected format for word embeddings
    if len(parts) < 8:
        raise ValueError(
            f"Invalid word custom_id {custom_id}: expected 8 parts "
            f"but got {len(parts)}"
        )

    # Additional validation: check for WORD component
    if "WORD" not in parts:
        raise ValueError(
            "Custom ID appears to be for a line embedding, not a word "
            f"embedding: {custom_id}"
        )

    return {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "word_id": int(parts[7]),
        "source": "openai_embedding_batch",
    }


def save_word_embeddings_as_delta(  # pylint: disable=too-many-statements
    results: List[dict],
    descriptions: Dict[str, Dict[int, dict]],
    batch_id: str,
    bucket_name: str,
    sqs_queue_url: Optional[str] = None,
) -> dict:
    """
    Save word embedding results as a delta file to S3 for ChromaDB compaction.

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
        # parse our metadata fields
        _meta = _parse_metadata_from_custom_id(result["custom_id"])
        image_id = _meta["image_id"]
        receipt_id = _meta["receipt_id"]
        line_id = _meta["line_id"]
        word_id = _meta["word_id"]
        source = "openai_embedding_batch"

        # From the descriptions, get the receipt details for this result
        receipt_details = descriptions[image_id][receipt_id]
        words = receipt_details["words"]
        labels = receipt_details["labels"]
        metadata = receipt_details["metadata"]

        # Get the target word from the list of words
        target_word = next(
            (
                w
                for w in words
                if w.line_id == line_id and w.word_id == word_id
            ),
            None,
        )
        if target_word is None:
            raise ValueError(
                f"No ReceiptWord found for image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}, "
                f"word_id={word_id}"
            )

        # Filter labels to only those for this specific word
        word_labels = [
            lbl
            for lbl in labels
            if lbl.image_id == image_id
            and lbl.receipt_id == receipt_id
            and lbl.line_id == line_id
            and lbl.word_id == word_id
        ]

        # Get word context directly (more efficient than parsing)
        left_words, right_words = get_word_neighbors(
            target_word, words, context_size=2
        )
        # Extract first neighbor on each side for backward compatibility
        left_text = left_words[0] if left_words else "<EDGE>"
        right_text = right_words[0] if right_words else "<EDGE>"

        # Priority: canonical name > regular merchant name
        if (
            hasattr(metadata, "canonical_merchant_name")
            and metadata.canonical_merchant_name
        ):
            merchant_name = metadata.canonical_merchant_name
        else:
            merchant_name = metadata.merchant_name

        # Build metadata for ChromaDB using consolidated metadata creation
        word_metadata = create_word_metadata(
            word=target_word,
            left_word=left_text,
            right_word=right_text,
            merchant_name=merchant_name,
            label_status="unvalidated",  # Will be enriched below
            source=source,
        )

        # Enrich with label information
        word_metadata = enrich_word_metadata_with_labels(
            word_metadata, word_labels
        )

        # Anchor-only enrichment based on this target word's extracted_data
        word_metadata = enrich_word_metadata_with_anchors(
            word_metadata, target_word
        )

        # Add to delta arrays
        ids.append(result["custom_id"])
        embeddings.append(result["embedding"])
        metadatas.append(word_metadata)
        documents.append(target_word.text)

    # Produce the delta file
    delta_result = _produce_delta_for_collection(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
        bucket_name=bucket_name,
        collection_name="words",
        database_name="words",
        sqs_queue_url=sqs_queue_url,
        batch_id=batch_id,
    )

    return delta_result
