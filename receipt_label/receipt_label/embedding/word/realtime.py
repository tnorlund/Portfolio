"""Real-time word embedding that matches batch embedding structure."""

import logging
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptWord

from receipt_label.utils import get_client_manager
from receipt_label.merchant_validation.normalize import (
    normalize_phone,
    normalize_address,
    normalize_url,
)
from receipt_label.merchant_validation.normalize import (
    normalize_phone,
    build_full_address_from_words,
    build_full_address_from_lines,
)

logger = logging.getLogger(__name__)


def _format_word_context_embedding_input(
    target_word: ReceiptWord,
    all_words: List[ReceiptWord],
    context_size: int = 2
) -> str:
    """
    Format word with spatial context for embedding.

    New format: simple context-only with multiple words and <EDGE> tags.
    Format: "left_words... word right_words..."

    Example with context_size=2:
    - At edge: "<EDGE> <EDGE> Total Tax Discount"
    - 1 from edge: "<EDGE> Subtotal Total Tax Discount"
    - 2+ from edge: "Items Subtotal Total Tax Discount"

    Args:
        target_word: The word to format
        all_words: All words in the receipt for context
        context_size: Number of words to include on each side (default: 2)

    Returns:
        Formatted string with context words and <EDGE> tags
    """
    left_words, right_words = _get_word_neighbors(target_word, all_words, context_size)

    # Pad left with <EDGE> tags if needed (one per missing position)
    left_padded = (["<EDGE>"] * max(0, context_size - len(left_words)) + left_words)[-context_size:]

    # Pad right with <EDGE> tags if needed (one per missing position)
    right_padded = (right_words + ["<EDGE>"] * max(0, context_size - len(right_words)))[:context_size]

    # Simple format: left_words word right_words
    return " ".join(left_padded + [target_word.text] + right_padded)


def _get_word_neighbors(
    target_word: ReceiptWord,
    all_words: List[ReceiptWord],
    context_size: int = 2
) -> Tuple[List[str], List[str]]:
    """
    Get the left and right neighbor words for the target word with configurable context size.

    Finds words based on horizontal position (x-coordinate) that are on roughly the same
    horizontal space. Uses centroid y-coordinate check against vertical span (between
    bottom_left and top_left) to determine if words are on the same horizontal space.

    Returns multiple neighbors on each side, using <EDGE> tags for missing positions.
    This preserves relative position information and distinguishes words at different
    distances from edges.

    Args:
        target_word: The word to find neighbors for
        all_words: All words in the receipt
        context_size: Number of words to include on each side (default: 2)

    Returns:
        Tuple of (left_words, right_words) where each is a list of context_size words
    """
    # Calculate target word's vertical span (using corners)
    target_bottom = target_word.bottom_left["y"]
    target_top = target_word.top_left["y"]

    # Sort all words by x-coordinate (horizontal position)
    sorted_all = sorted(all_words, key=lambda w: w.calculate_centroid()[0])
    idx = next(
        i
        for i, w in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        )
    )

    # Collect left neighbors (up to context_size)
    # Check if candidate word's centroid y is within target's vertical span
    left_words = []
    for w in reversed(sorted_all[:idx]):
        if w is target_word:
            continue
        # Check if this word is on roughly the same horizontal space
        # by checking if its centroid y is within target's vertical span
        w_centroid_y = w.calculate_centroid()[1]
        if target_bottom <= w_centroid_y <= target_top:
            left_words.append(w.text)
            if len(left_words) >= context_size:
                break

    # Collect right neighbors (up to context_size)
    # Check if candidate word's centroid y is within target's vertical span
    right_words = []
    for w in sorted_all[idx + 1 :]:
        if w is target_word:
            continue
        # Check if this word is on roughly the same horizontal space
        # by checking if its centroid y is within target's vertical span
        w_centroid_y = w.calculate_centroid()[1]
        if target_bottom <= w_centroid_y <= target_top:
            right_words.append(w.text)
            if len(right_words) >= context_size:
                break

    return left_words, right_words


def _get_word_position(word: ReceiptWord) -> str:
    """
    Get word position in 3x3 grid format matching batch system.

    Replicates logic from embedding/word/submit.py
    Uses normalized coordinates (0.0-1.0) from calculate_centroid()
    """
    # Calculate centroid coordinates (normalized 0.0–1.0)
    x_center, y_center = word.calculate_centroid()

    # Determine vertical bucket (y=0 at bottom in receipt coordinate system)
    if y_center > 0.66:
        vertical = "top"
    elif y_center > 0.33:
        vertical = "middle"
    else:
        vertical = "bottom"

    # Determine horizontal bucket
    if x_center < 0.33:
        horizontal = "left"
    elif x_center < 0.66:
        horizontal = "center"
    else:
        horizontal = "right"

    return f"{vertical}-{horizontal}"


def _create_word_metadata(
    word: ReceiptWord,
    left_words: List[str],
    right_words: List[str],
    merchant_name: Optional[str] = None,
    label_status: str = "unvalidated",
) -> dict:
    """
    Create comprehensive metadata for word embedding.

    Args:
        word: The word entity
        left_words: List of left context words (may include <EDGE>)
        right_words: List of right context words (may include <EDGE>)
        merchant_name: Optional merchant name
        label_status: Label validation status

    Returns:
        Metadata dictionary for ChromaDB
    """
    x_center, y_center = word.calculate_centroid()

    metadata = {
        "image_id": word.image_id,
        "receipt_id": str(word.receipt_id),  # Ensure string for consistency
        "line_id": word.line_id,
        "word_id": word.word_id,
        "source": "openai_embedding_realtime",
        "text": word.text,
        "x": x_center,
        "y": y_center,
        "width": word.bounding_box["width"],
        "height": word.bounding_box["height"],
        "confidence": word.confidence,
        "left": left_words[0] if left_words else "<EDGE>",  # First left for backward compatibility
        "right": right_words[0] if right_words else "<EDGE>",  # First right for backward compatibility
        "left_context": " ".join(left_words) if left_words else "<EDGE>",  # Full left context
        "right_context": " ".join(right_words) if right_words else "<EDGE>",  # Full right context
        "label_status": label_status,
        "embedding_type": "word",  # For filtering
    }

    # Add merchant info if available
    if merchant_name:
        metadata["merchant_name"] = merchant_name

    # Add validation info if available
    if hasattr(word, "validated_labels") and word.validated_labels:
        metadata["validated_labels"] = word.validated_labels

    if hasattr(word, "label_validated_at") and word.label_validated_at:
        metadata["label_validated_at"] = word.label_validated_at.isoformat()

    return metadata


def embed_words_realtime(
    words: List[ReceiptWord],
    merchant_name: Optional[str] = None,
    model: str = "text-embedding-3-small",
    context_size: int = 2,
) -> List[Tuple[ReceiptWord, List[float]]]:
    """
    Embed words in real-time using simple context format with configurable context size.

    Args:
        words: List of receipt words to embed
        merchant_name: Optional merchant name for context
        model: OpenAI model to use for embeddings
        context_size: Number of words to include on each side (default: 2)

    Returns:
        List of (word, embedding) tuples
    """
    client_manager = get_client_manager()
    openai_client = client_manager.openai

    # Filter out noise words using stored flag like batch
    meaningful_words = [w for w in words if not getattr(w, "is_noise", False)]

    if not meaningful_words:
        logger.info("No meaningful words to embed")
        return []

    # Build list of (word, formatted_text) pairs
    word_text_pairs = []
    for word in meaningful_words:
        formatted_text = _format_word_context_embedding_input(word, words, context_size)
        word_text_pairs.append((word, formatted_text))

    try:
        # Get embeddings from OpenAI
        response = openai_client.embeddings.create(
            model=model,
            input=[pair[1] for pair in word_text_pairs],
        )

        # Build list of (word, embedding) tuples
        word_embeddings = []
        for i, (word, _) in enumerate(word_text_pairs):
            word_embeddings.append((word, response.data[i].embedding))

        logger.info(
            f"Successfully embedded {len(word_embeddings)} words with {context_size}-word context"
        )
        return word_embeddings

    except Exception as e:
        logger.error(f"Error embedding words: {str(e)}")
        raise


def format_word_for_embedding(
    word_text: str,
    left_words: List[str],
    right_words: List[str],
    context_size: int = 2,
) -> str:
    """
    Format a word with context for on-the-fly embedding.

    This function allows embedding a word without needing the full ReceiptWord entity.
    Just provide the word text and its left/right neighbors (or <EDGE> for missing ones).

    Args:
        word_text: The word to embed
        left_words: List of words to the left (use ["<EDGE>"] if at edge)
        right_words: List of words to the right (use ["<EDGE>"] if at edge)
        context_size: Number of words expected on each side (default: 2)

    Returns:
        Formatted string ready for embedding

    Example:
        >>> format_word_for_embedding("Total", ["Subtotal"], ["Tax", "Discount"], context_size=2)
        "<EDGE> Subtotal Total Tax Discount"

        >>> format_word_for_embedding("Total", [], ["Tax"], context_size=2)
        "<EDGE> <EDGE> Total Tax <EDGE>"
    """
    # Pad left with <EDGE> tags if needed (one per missing position)
    left_padded = (["<EDGE>"] * max(0, context_size - len(left_words)) + left_words)[-context_size:]

    # Pad right with <EDGE> tags if needed (one per missing position)
    right_padded = (right_words + ["<EDGE>"] * max(0, context_size - len(right_words)))[:context_size]

    # Simple format: left_words word right_words
    return " ".join(left_padded + [word_text] + right_padded)


def embed_receipt_words_realtime(
    receipt_id: str,
    merchant_name: Optional[str] = None,
) -> List[Tuple[ReceiptWord, List[float]]]:
    """
    Embed all words from a receipt and store to ChromaDB using batch-compatible structure.

    Args:
        receipt_id: ID of the receipt to process
        merchant_name: Optional merchant name for context

    Returns:
        List of (word, embedding) tuples
    """
    client_manager = get_client_manager()
    dynamo_client = client_manager.dynamo
    chroma_client = client_manager.chroma

    # Get receipt words from DynamoDB
    words = dynamo_client.list_receipt_words_by_receipt(receipt_id)

    if not words:
        logger.warning(f"No words found for receipt {receipt_id}")
        return []

    # Get embeddings with spatial context
    word_embeddings = embed_words_realtime(words, merchant_name)

    # Prepare data for ChromaDB
    ids = []
    embeddings = []
    metadatas = []
    documents = []
    word_embedding_pairs = []

    # Anchor-only: attach normalized metadata only on words with extracted_data

    # Create a mapping for quick lookup of embeddings by word
    embedding_map = {(w.word_id, w.line_id): emb for w, emb in word_embeddings}

    for word in words:
        word_key = (word.word_id, word.line_id)
        if word_key in embedding_map:
            # Get left and right words with configurable context size
            left_words, right_words = _get_word_neighbors(word, words, context_size=2)

            # Create vector ID matching batch format
            # IDs are always integers per entity definitions
            vector_id = f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"

            # Create metadata matching batch structure
            metadata = _create_word_metadata(
                word=word,
                left_words=left_words,
                right_words=right_words,
                merchant_name=merchant_name,
                label_status="unvalidated",
            )

            # Anchor-only enrichment based on this word's extracted_data
            try:
                ext = getattr(word, "extracted_data", None) or {}
                etype = str(ext.get("type", "")).lower() if ext else ""
                val = ext.get("value") if ext else None
                if etype == "phone":
                    ph = normalize_phone(val or word.text)
                    if ph:
                        metadata["normalized_phone_10"] = ph
                elif etype == "address":
                    addr = normalize_address(val or word.text)
                    if addr:
                        metadata["normalized_full_address"] = addr
                elif etype == "url":
                    url_norm = normalize_url(val or word.text)
                    if url_norm:
                        metadata["normalized_url"] = url_norm
            except Exception:
                pass

            # Prepare data for ChromaDB
            ids.append(vector_id)
            embeddings.append(embedding_map[word_key])
            metadatas.append(metadata)
            documents.append(word.text)  # Store word text as document

            word_embedding_pairs.append((word, embedding_map[word_key]))

    # Store to ChromaDB using correct collection
    if ids:
        try:
            chroma_client.upsert_vectors(
                collection_name="words",
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

            logger.info(
                f"Stored {len(ids)} word embeddings to ChromaDB for receipt {receipt_id}"
            )

            # Update embedding status in DynamoDB
            try:
                updated_words = []
                current_time = datetime.utcnow()

                for word, _ in word_embedding_pairs:
                    word.embedding_status = EmbeddingStatus.SUCCESS
                    word.embedded_at = current_time
                    updated_words.append(word)

                # Update words individually with error handling
                failed_updates = []
                for word in updated_words:
                    try:
                        dynamo_client.put_receipt_word(word)
                    except Exception as update_error:
                        failed_updates.append((word, update_error))
                        logger.error(
                            f"Failed to update embedding status for word {word.word_id}: {update_error}"
                        )

                if failed_updates:
                    logger.warning(
                        f"Failed to update {len(failed_updates)} out of {len(updated_words)} words"
                    )

            except Exception as batch_error:
                logger.error(
                    f"Error during DynamoDB batch update: {batch_error}"
                )
                raise RuntimeError(
                    f"Embeddings stored to ChromaDB but DynamoDB update failed: {batch_error}"
                )

        except Exception as e:
            logger.error(f"Error storing to ChromaDB: {str(e)}")
            raise

    return word_embedding_pairs
