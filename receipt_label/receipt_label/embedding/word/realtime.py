"""Real-time word embedding that matches batch embedding structure."""

import logging
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptWord

from receipt_label.client_manager import get_client_manager
from receipt_label.utils.noise_detection import is_noise_text

logger = logging.getLogger(__name__)


def _format_word_context_embedding_input(
    target_word: ReceiptWord, all_words: List[ReceiptWord]
) -> str:
    """
    Format word with spatial context matching batch embedding structure.

    Replicates the format from embedding/word/submit.py:
    <TARGET>word</TARGET> <POS>position</POS> <CONTEXT>left right</CONTEXT>
    """
    # Calculate position using same logic as batch system
    position = _get_word_position(target_word)

    # Find neighboring words on the same line (vertical span overlap)
    target_top = target_word.top_left["y"]
    target_bottom = target_word.bottom_left["y"]
    target_center_x = target_word.calculate_centroid()[
        0
    ]  # x-coordinate from tuple

    left_word = "<EDGE>"
    right_word = "<EDGE>"

    # Find closest words to left and right with vertical overlap
    left_candidates = []
    right_candidates = []

    for word in all_words:
        if word.word_id == target_word.word_id:
            continue

        # Check vertical overlap (same logic as batch)
        word_top = word.top_left["y"]
        word_bottom = word.bottom_left["y"]

        # Overlap condition: max(tops) < min(bottoms)
        if max(target_top, word_top) < min(target_bottom, word_bottom):
            word_center_x = word.calculate_centroid()[
                0
            ]  # x-coordinate from tuple

            if word_center_x < target_center_x:
                left_candidates.append((word, target_center_x - word_center_x))
            elif word_center_x > target_center_x:
                right_candidates.append(
                    (word, word_center_x - target_center_x)
                )

    # Get closest neighbors
    if left_candidates:
        left_word = min(left_candidates, key=lambda x: x[1])[0].text
    if right_candidates:
        right_word = min(right_candidates, key=lambda x: x[1])[0].text

    return f"<TARGET>{target_word.text}</TARGET> <POS>{position}</POS> <CONTEXT>{left_word} {right_word}</CONTEXT>"


def _get_word_neighbors(
    target_word: ReceiptWord, all_words: List[ReceiptWord]
) -> Tuple[str, str]:
    """
    Get the left and right neighbor words for the target word.

    This is the same logic as _format_word_context_embedding_input but
    returns the neighbors directly instead of formatting them.

    Returns:
        Tuple of (left_word, right_word)
    """
    # Find neighboring words on the same line (vertical span overlap)
    target_top = target_word.top_left["y"]
    target_bottom = target_word.bottom_left["y"]
    target_center_x = target_word.calculate_centroid()[
        0
    ]  # x-coordinate from tuple

    left_word = "<EDGE>"
    right_word = "<EDGE>"

    # Find closest words to left and right with vertical overlap
    left_candidates = []
    right_candidates = []

    for word in all_words:
        if word.word_id == target_word.word_id:
            continue

        # Check vertical overlap (same logic as batch)
        word_top = word.top_left["y"]
        word_bottom = word.bottom_left["y"]

        # Overlap condition: max(tops) < min(bottoms)
        if max(target_top, word_top) < min(target_bottom, word_bottom):
            word_center_x = word.calculate_centroid()[
                0
            ]  # x-coordinate from tuple

            if word_center_x < target_center_x:
                left_candidates.append((word, target_center_x - word_center_x))
            elif word_center_x > target_center_x:
                right_candidates.append(
                    (word, word_center_x - target_center_x)
                )

    # Get closest neighbors
    if left_candidates:
        left_word = min(left_candidates, key=lambda x: x[1])[0].text
    if right_candidates:
        right_word = min(right_candidates, key=lambda x: x[1])[0].text

    return left_word, right_word


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
    left_word: str,
    right_word: str,
    merchant_name: Optional[str] = None,
    label_status: str = "unvalidated",
) -> dict:
    """
    Create comprehensive metadata matching batch embedding structure.

    Replicates metadata from embedding/word/poll.py
    """
    x_center, y_center = word.calculate_centroid()

    metadata = {
        "image_id": word.image_id,
        "receipt_id": str(word.receipt_id),  # Ensure string for consistency
        "line_id": word.line_id,
        "word_id": word.word_id,
        "source": "openai_embedding_realtime",  # Different from batch
        "text": word.text,
        "x": x_center,
        "y": y_center,
        "width": word.bounding_box["width"],
        "height": word.bounding_box["height"],
        "confidence": word.confidence,
        "left": left_word,
        "right": right_word,
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
) -> List[Tuple[ReceiptWord, List[float]]]:
    """
    Embed words in real-time using batch-compatible formatting.

    Args:
        words: List of receipt words to embed
        merchant_name: Optional merchant name for context
        model: OpenAI model to use for embeddings

    Returns:
        List of (word, embedding) tuples
    """
    client_manager = get_client_manager()
    openai_client = client_manager.openai

    # Filter out noise words
    meaningful_words = [w for w in words if not is_noise_text(w.text)]

    if not meaningful_words:
        logger.info("No meaningful words to embed")
        return []

    # Build list of (word, formatted_text) pairs
    word_text_pairs = []
    for word in meaningful_words:
        formatted_text = _format_word_context_embedding_input(word, words)
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
            f"Successfully embedded {len(word_embeddings)} words with spatial context"
        )
        return word_embeddings

    except Exception as e:
        logger.error(f"Error embedding words: {str(e)}")
        raise


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

    # Create a mapping for quick lookup of embeddings by word
    embedding_map = {(w.word_id, w.line_id): emb for w, emb in word_embeddings}

    for word in words:
        word_key = (word.word_id, word.line_id)
        if word_key in embedding_map:
            # Get left and right words directly using the same logic as formatting
            # This avoids parsing issues with spaces in words
            left_word, right_word = _get_word_neighbors(word, words)

            # Create vector ID matching batch format
            # IDs are always integers per entity definitions
            vector_id = f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"

            # Create metadata matching batch structure
            metadata = _create_word_metadata(
                word=word,
                left_word=left_word,
                right_word=right_word,
                merchant_name=merchant_name,
                label_status="unvalidated",
            )

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
