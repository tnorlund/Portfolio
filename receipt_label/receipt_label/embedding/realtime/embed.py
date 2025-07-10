"""Real-time embedding functions for immediate receipt processing.

This module provides a unified interface for real-time embedding that maintains
compatibility with the batch embedding system structure and metadata format.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities import ReceiptMetadata, ReceiptWord

from receipt_label.embedding.word.realtime import (
    embed_receipt_words_realtime,
    embed_words_realtime,
)
from receipt_label.embedding.line.realtime import (
    embed_receipt_lines_realtime,
    embed_lines_realtime,
)

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


def embed_words_realtime_simple(
    words: List[ReceiptWord],
    context: EmbeddingContext,
    model: str = "text-embedding-3-small",
) -> Dict[str, List[float]]:
    """
    Simple word embedding interface for backward compatibility.
    
    This function delegates to the batch-compatible word embedding
    implementation while maintaining the original API.
    
    Args:
        words: List of receipt words to embed
        context: Embedding context with merchant information
        model: OpenAI model to use for embeddings

    Returns:
        Dictionary mapping word text to embedding vectors
    """
    merchant_name = context.canonical_merchant_name or context.merchant_name
    return embed_words_realtime(words, merchant_name, model)


def embed_receipt_realtime(
    receipt_id: str,
    merchant_metadata: Optional[ReceiptMetadata] = None,
    embed_words: bool = True,
    embed_lines: bool = False,
) -> Tuple[List[Tuple[ReceiptWord, List[float]]], List[Tuple]]:
    """
    Embed words and/or lines from a receipt using batch-compatible structure.
    
    This function delegates to the specialized word and line embedding
    implementations that maintain compatibility with the batch system.
    
    Args:
        receipt_id: ID of the receipt to process
        merchant_metadata: Optional merchant metadata for context
        embed_words: Whether to embed words (default: True)
        embed_lines: Whether to embed lines (default: False)

    Returns:
        Tuple of (word_embeddings, line_embeddings) where each is a list of (entity, embedding) tuples
    """
    from receipt_label.client_manager import get_client_manager
    
    # Extract merchant name from metadata
    merchant_name = None
    if merchant_metadata:
        merchant_name = (
            merchant_metadata.canonical_merchant_name or 
            merchant_metadata.merchant_name
        )
    else:
        # Try to fetch merchant metadata if not provided
        try:
            client_manager = get_client_manager()
            dynamo_client = client_manager.dynamo
            metadata_list = dynamo_client.list_receipt_metadatas_by_receipt(receipt_id)
            if metadata_list:
                merchant_metadata = metadata_list[0]
                merchant_name = (
                    merchant_metadata.canonical_merchant_name or 
                    merchant_metadata.merchant_name
                )
        except Exception as e:
            logger.warning(f"Could not fetch merchant metadata: {str(e)}")
    
    word_embeddings = []
    line_embeddings = []
    
    # Embed words using batch-compatible structure
    if embed_words:
        try:
            word_embeddings = embed_receipt_words_realtime(receipt_id, merchant_name)
            logger.info(f"Embedded {len(word_embeddings)} words for receipt {receipt_id}")
        except Exception as e:
            logger.error(f"Error embedding words for receipt {receipt_id}: {str(e)}")
            raise
    
    # Embed lines using batch-compatible structure
    if embed_lines:
        try:
            line_embeddings = embed_receipt_lines_realtime(receipt_id, merchant_name)
            logger.info(f"Embedded {len(line_embeddings)} lines for receipt {receipt_id}")
        except Exception as e:
            logger.error(f"Error embedding lines for receipt {receipt_id}: {str(e)}")
            raise
    
    return word_embeddings, line_embeddings
