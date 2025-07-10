"""Integration functions for real-time embedding with merchant validation.

This module provides integration between the existing merchant validation agent
and the new real-time embedding capabilities.
"""

import logging
from typing import Optional, Tuple

from receipt_dynamo.entities import ReceiptMetadata

from receipt_label.embedding.line.realtime import embed_receipt_lines_realtime
from receipt_label.embedding.word.realtime import embed_receipt_words_realtime
from receipt_label.merchant_validation.handler import create_validation_handler

logger = logging.getLogger(__name__)


def process_receipt_with_realtime_embedding(
    receipt_id: str,
    merchant_metadata: Optional[ReceiptMetadata] = None,
    embed_words: bool = True,
    embed_lines: bool = False,
) -> Tuple[ReceiptMetadata, dict]:
    """
    Process a receipt with real-time embedding after merchant validation.
    
    This function integrates the existing merchant validation agent with
    the new real-time embedding capabilities.
    
    Args:
        receipt_id: ID of the receipt to process
        merchant_metadata: Optional pre-validated merchant metadata
        embed_words: Whether to embed words (default: True)
        embed_lines: Whether to embed lines (default: False)
        
    Returns:
        Tuple of (merchant_metadata, embedding_results)
    """
    from receipt_label.client_manager import get_client_manager
    
    client_manager = get_client_manager()
    dynamo_client = client_manager.dynamo
    
    # Get receipt data
    receipt_words = dynamo_client.list_receipt_words_by_receipt(receipt_id)
    receipt_lines = dynamo_client.list_receipt_lines_by_receipt(receipt_id)
    
    if not receipt_words:
        raise ValueError(f"No words found for receipt {receipt_id}")
    
    # Get merchant metadata if not provided
    if merchant_metadata is None:
        logger.info(f"Running merchant validation for receipt {receipt_id}")
        
        # Use existing merchant validation agent
        validation_handler = create_validation_handler()
        
        # Safely convert receipt_id to integer
        try:
            receipt_id_int = int(receipt_id)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid receipt_id '{receipt_id}': must be convertible to integer") from e
        
        merchant_metadata, status_info = validation_handler.validate_receipt_merchant(
            image_id=receipt_words[0].image_id,
            receipt_id=receipt_id_int,
            receipt_lines=receipt_lines,
            receipt_words=receipt_words
        )
        
        logger.info(
            f"Merchant validation completed: {status_info.get('status', 'unknown')}"
        )
    
    # Extract canonical merchant name
    canonical_merchant_name = (
        merchant_metadata.canonical_merchant_name 
        or merchant_metadata.merchant_name
    ) if merchant_metadata else None
    
    # Perform real-time embedding
    embedding_results = {}
    
    if embed_words:
        try:
            logger.info(f"Embedding words for receipt {receipt_id}")
            word_embeddings = embed_receipt_words_realtime(
                receipt_id, canonical_merchant_name
            )
            embedding_results["words"] = {
                "count": len(word_embeddings),
                "merchant_name": canonical_merchant_name,
            }
            logger.info(f"Successfully embedded {len(word_embeddings)} words")
            
        except Exception as e:
            logger.error(f"Error embedding words for receipt {receipt_id}: {str(e)}")
            embedding_results["words"] = {"error": str(e)}
    
    if embed_lines:
        try:
            logger.info(f"Embedding lines for receipt {receipt_id}")
            line_embeddings = embed_receipt_lines_realtime(
                receipt_id, canonical_merchant_name
            )
            embedding_results["lines"] = {
                "count": len(line_embeddings),
                "merchant_name": canonical_merchant_name,
            }
            logger.info(f"Successfully embedded {len(line_embeddings)} lines")
            
        except Exception as e:
            logger.error(f"Error embedding lines for receipt {receipt_id}: {str(e)}")
            embedding_results["lines"] = {"error": str(e)}
    
    return merchant_metadata, embedding_results


def embed_receipt_realtime(
    receipt_id: str,
    merchant_metadata: Optional[ReceiptMetadata] = None,
    embed_words: bool = True,
    embed_lines: bool = False,
) -> dict:
    """
    Simplified interface for real-time receipt embedding.
    
    This is a convenience function that focuses on the embedding aspect
    and uses existing merchant validation when needed.
    
    Args:
        receipt_id: ID of the receipt to process
        merchant_metadata: Optional pre-validated merchant metadata
        embed_words: Whether to embed words (default: True)
        embed_lines: Whether to embed lines (default: False)
        
    Returns:
        Dictionary with embedding results
    """
    _, embedding_results = process_receipt_with_realtime_embedding(
        receipt_id=receipt_id,
        merchant_metadata=merchant_metadata,
        embed_words=embed_words,
        embed_lines=embed_lines,
    )
    
    return embedding_results