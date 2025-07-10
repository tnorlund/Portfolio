"""Real-time embedding functions for immediate receipt processing."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pinecone.grpc import Vector
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptMetadata, ReceiptWord

from receipt_label.client_manager import get_client_manager
from receipt_label.utils.noise_detection import is_noise_word

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingContext:
    """Context for real-time embeddings."""

    receipt_id: str
    image_id: str
    merchant_name: Optional[str] = None
    merchant_category: Optional[str] = None
    canonical_merchant_name: Optional[str] = None
    validation_status: Optional[str] = None
    requires_immediate_response: bool = False
    is_user_facing: bool = False


def embed_words_realtime(
    words: List[ReceiptWord],
    context: EmbeddingContext,
    model: str = "text-embedding-3-small",
) -> Dict[str, List[float]]:
    """
    Embed receipt words in real-time using OpenAI API.

    Args:
        words: List of receipt words to embed
        context: Embedding context with merchant information
        model: OpenAI model to use for embeddings

    Returns:
        Dictionary mapping word text to embedding vectors
    """
    client_manager = get_client_manager()
    openai_client = client_manager.openai

    # Filter out noise words
    meaningful_words = [w for w in words if not is_noise_word(w.text)]

    if not meaningful_words:
        logger.info(
            f"No meaningful words to embed for receipt {context.receipt_id}"
        )
        return {}

    # Prepare texts for embedding
    texts = [word.text for word in meaningful_words]

    # Add merchant context if available
    if context.canonical_merchant_name:
        # Prepend merchant context to improve embedding quality
        texts = [
            f"{context.canonical_merchant_name}: {text}" for text in texts
        ]

    try:
        # Get embeddings from OpenAI
        response = openai_client.embeddings.create(
            model=model,
            input=texts,
        )

        # Map embeddings back to original words
        embeddings = {}
        for i, word in enumerate(meaningful_words):
            embeddings[word.text] = response.data[i].embedding

        logger.info(
            f"Successfully embedded {len(embeddings)} words for receipt {context.receipt_id}"
        )

        return embeddings

    except Exception as e:
        logger.error(f"Error embedding words: {str(e)}")
        raise


def embed_receipt_realtime(
    receipt_id: str,
    merchant_metadata: Optional[ReceiptMetadata] = None,
) -> List[Tuple[ReceiptWord, List[float]]]:
    """
    Embed all words from a receipt in real-time and store to Pinecone.

    Args:
        receipt_id: ID of the receipt to process
        merchant_metadata: Optional merchant metadata for context

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

    # Get image_id from first word
    image_id = words[0].image_id

    # Create embedding context
    context = EmbeddingContext(
        receipt_id=receipt_id,
        image_id=image_id,
    )

    # Add merchant context if available
    if merchant_metadata:
        context.merchant_name = merchant_metadata.merchant_name
        context.merchant_category = merchant_metadata.merchant_category
        context.canonical_merchant_name = (
            merchant_metadata.canonical_merchant_name
        )
        context.validation_status = merchant_metadata.validation_status
    else:
        # Try to fetch merchant metadata if not provided
        try:
            metadata_list = dynamo_client.list_receipt_metadatas_by_receipt(
                receipt_id
            )
            if metadata_list:
                merchant_metadata = metadata_list[0]
                context.merchant_name = merchant_metadata.merchant_name
                context.merchant_category = merchant_metadata.merchant_category
                context.canonical_merchant_name = (
                    merchant_metadata.canonical_merchant_name
                )
                context.validation_status = merchant_metadata.validation_status
        except Exception as e:
            logger.warning(f"Could not fetch merchant metadata: {str(e)}")

    # Get embeddings
    embeddings = embed_words_realtime(words, context)

    # Prepare vectors for Pinecone
    vectors = []
    word_embedding_pairs = []

    for word in words:
        if word.text in embeddings:
            # Create unique ID for Pinecone
            vector_id = (
                f"IMAGE#{image_id}#RECEIPT#{receipt_id}#WORD#{word.word_id}"
            )

            # Prepare metadata
            metadata = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "text": word.text,
                "confidence": word.confidence,
                "embedding_type": "word",
                "embedding_status": "realtime",
                "embedded_at": datetime.utcnow().isoformat(),
            }

            # Add merchant metadata if available
            if context.canonical_merchant_name:
                metadata["merchant_name"] = context.canonical_merchant_name
                metadata["canonical_merchant_name"] = (
                    context.canonical_merchant_name
                )
            elif context.merchant_name:
                metadata["merchant_name"] = context.merchant_name

            if context.merchant_category:
                metadata["merchant_category"] = context.merchant_category

            if context.validation_status:
                metadata["validation_status"] = context.validation_status

            # Add validated labels if available
            if hasattr(word, "validated_labels") and word.validated_labels:
                metadata["validated_labels"] = word.validated_labels

            # Create Pinecone vector
            vectors.append(
                Vector(
                    id=vector_id,
                    values=embeddings[word.text],
                    metadata=metadata,
                )
            )

            word_embedding_pairs.append((word, embeddings[word.text]))

    # Store to Pinecone
    if vectors:
        try:
            # Use word namespace to match batch processing
            namespace = "word"
            index = pinecone_client.Index("receipt-embeddings")
            index.upsert(vectors=vectors, namespace=namespace)

            logger.info(
                f"Stored {len(vectors)} embeddings to Pinecone for receipt {receipt_id}"
            )

            # Update embedding status in DynamoDB atomically
            # Prepare all updates first, then batch them
            try:
                updated_words = []
                current_time = datetime.utcnow()
                
                for word, _ in word_embedding_pairs:
                    # Ensure the word has the embedded_at attribute
                    if not hasattr(word, 'embedded_at'):
                        word.embedded_at = None
                    
                    word.embedding_status = EmbeddingStatus.SUCCESS
                    word.embedded_at = current_time
                    updated_words.append(word)
                
                # Batch update all words at once
                # TODO: Implement batch_put_receipt_words for true atomicity
                # For now, update individually but with proper error handling
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
                    # If some updates failed, log the issue but don't fail the entire operation
                    # The embeddings are already in Pinecone, so this is a data consistency issue
                    logger.warning(
                        f"Failed to update {len(failed_updates)} out of {len(updated_words)} words. "
                        f"Embeddings are stored in Pinecone but DynamoDB status may be inconsistent."
                    )
                    # Could implement retry logic or compensation transaction here
                    
            except Exception as batch_error:
                logger.error(f"Error during DynamoDB batch update: {batch_error}")
                # The embeddings are already in Pinecone, so we don't want to fail completely
                # But we should notify about the inconsistent state
                raise RuntimeError(
                    f"Embeddings stored to Pinecone but DynamoDB update failed: {batch_error}"
                )

        except Exception as e:
            logger.error(f"Error storing to Pinecone: {str(e)}")
            raise

    return word_embedding_pairs
