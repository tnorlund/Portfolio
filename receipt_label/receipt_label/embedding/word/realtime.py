"""Real-time word embedding that matches batch embedding structure."""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pinecone.grpc import Vector
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptWord

from receipt_label.client_manager import get_client_manager
from receipt_label.utils.noise_detection import is_noise_word

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
    target_center_x = target_word.calculate_centroid()["x"]
    
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
            word_center_x = word.calculate_centroid()["x"]
            
            if word_center_x < target_center_x:
                left_candidates.append((word, target_center_x - word_center_x))
            elif word_center_x > target_center_x:
                right_candidates.append((word, word_center_x - target_center_x))
    
    # Get closest neighbors
    if left_candidates:
        left_word = min(left_candidates, key=lambda x: x[1])[0].text
    if right_candidates:
        right_word = min(right_candidates, key=lambda x: x[1])[0].text
    
    return f"<TARGET>{target_word.text}</TARGET> <POS>{position}</POS> <CONTEXT>{left_word} {right_word}</CONTEXT>"


def _get_word_position(word: ReceiptWord) -> str:
    """
    Get word position in 3x3 grid format matching batch system.
    
    Replicates logic from embedding/word/submit.py
    """
    centroid = word.calculate_centroid()
    x, y = centroid["x"], centroid["y"]
    
    # Assume receipt bounds (adjust based on actual receipt dimensions)
    # For now, use simple thirds division
    RECEIPT_WIDTH = 1000  # Typical receipt width
    RECEIPT_HEIGHT = 1500  # Typical receipt height
    
    # Horizontal position
    if x < RECEIPT_WIDTH / 3:
        h_pos = "left"
    elif x < 2 * RECEIPT_WIDTH / 3:
        h_pos = "center"
    else:
        h_pos = "right"
    
    # Vertical position  
    if y < RECEIPT_HEIGHT / 3:
        v_pos = "top"
    elif y < 2 * RECEIPT_HEIGHT / 3:
        v_pos = "middle"
    else:
        v_pos = "bottom"
    
    return f"{v_pos}-{h_pos}"


def _create_word_metadata(
    word: ReceiptWord,
    left_word: str,
    right_word: str,
    merchant_name: Optional[str] = None,
    label_status: str = "unvalidated"
) -> dict:
    """
    Create comprehensive metadata matching batch embedding structure.
    
    Replicates metadata from embedding/word/poll.py
    """
    centroid = word.calculate_centroid()
    
    metadata = {
        "image_id": word.image_id,
        "receipt_id": str(word.receipt_id),  # Ensure string for consistency
        "line_id": word.line_id,
        "word_id": word.word_id,
        "source": "openai_embedding_realtime",  # Different from batch
        "text": word.text,
        "x": centroid["x"],
        "y": centroid["y"],
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
) -> Dict[str, List[float]]:
    """
    Embed words in real-time using batch-compatible formatting.
    
    Args:
        words: List of receipt words to embed
        merchant_name: Optional merchant name for context
        model: OpenAI model to use for embeddings
        
    Returns:
        Dictionary mapping word text to embedding vectors
    """
    client_manager = get_client_manager()
    openai_client = client_manager.openai
    
    # Filter out noise words
    meaningful_words = [w for w in words if not is_noise_word(w.text)]
    
    if not meaningful_words:
        logger.info("No meaningful words to embed")
        return {}
    
    # Format texts using batch embedding structure
    formatted_texts = []
    for word in meaningful_words:
        formatted_text = _format_word_context_embedding_input(word, words)
        formatted_texts.append(formatted_text)
    
    try:
        # Get embeddings from OpenAI
        response = openai_client.embeddings.create(
            model=model,
            input=formatted_texts,
        )
        
        # Map embeddings back to words
        embeddings = {}
        for i, word in enumerate(meaningful_words):
            embeddings[word.text] = response.data[i].embedding
        
        logger.info(f"Successfully embedded {len(embeddings)} words with spatial context")
        return embeddings
        
    except Exception as e:
        logger.error(f"Error embedding words: {str(e)}")
        raise


def embed_receipt_words_realtime(
    receipt_id: str,
    merchant_name: Optional[str] = None,
) -> List[Tuple[ReceiptWord, List[float]]]:
    """
    Embed all words from a receipt and store to Pinecone using batch-compatible structure.
    
    Args:
        receipt_id: ID of the receipt to process
        merchant_name: Optional merchant name for context
        
    Returns:
        List of (word, embedding) tuples
    """
    client_manager = get_client_manager()
    dynamo_client = client_manager.dynamo
    pinecone_client = client_manager.pinecone
    
    # Get receipt words from DynamoDB
    words = dynamo_client.list_receipt_words_by_receipt(receipt_id)
    
    if not words:
        logger.warning(f"No words found for receipt {receipt_id}")
        return []
    
    # Get embeddings with spatial context
    embeddings = embed_words_realtime(words, merchant_name)
    
    # Prepare vectors for Pinecone using batch structure
    vectors = []
    word_embedding_pairs = []
    
    for word in words:
        if word.text in embeddings:
            # Extract spatial context for metadata
            formatted_input = _format_word_context_embedding_input(word, words)
            
            # Parse left/right words from formatted input
            import re
            context_match = re.search(r'<CONTEXT>([^<]*)</CONTEXT>', formatted_input)
            if context_match:
                context_parts = context_match.group(1).strip().split()
                left_word = context_parts[0] if len(context_parts) > 0 else "<EDGE>"
                right_word = context_parts[1] if len(context_parts) > 1 else "<EDGE>"
            else:
                left_word = right_word = "<EDGE>"
            
            # Create vector ID matching batch format
            vector_id = f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
            
            # Create metadata matching batch structure
            metadata = _create_word_metadata(
                word=word,
                left_word=left_word,
                right_word=right_word,
                merchant_name=merchant_name,
                label_status="unvalidated"
            )
            
            # Create Pinecone vector
            vectors.append(
                Vector(
                    id=vector_id,
                    values=embeddings[word.text],
                    metadata=metadata,
                )
            )
            
            word_embedding_pairs.append((word, embeddings[word.text]))
    
    # Store to Pinecone using correct namespace
    if vectors:
        try:
            index = pinecone_client.Index("receipt-embeddings")
            index.upsert(vectors=vectors, namespace="words")  # Match batch namespace
            
            logger.info(f"Stored {len(vectors)} word embeddings to Pinecone for receipt {receipt_id}")
            
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
                        logger.error(f"Failed to update embedding status for word {word.word_id}: {update_error}")
                
                if failed_updates:
                    logger.warning(f"Failed to update {len(failed_updates)} out of {len(updated_words)} words")
                    
            except Exception as batch_error:
                logger.error(f"Error during DynamoDB batch update: {batch_error}")
                raise RuntimeError(f"Embeddings stored to Pinecone but DynamoDB update failed: {batch_error}")
            
        except Exception as e:
            logger.error(f"Error storing to Pinecone: {str(e)}")
            raise
    
    return word_embedding_pairs