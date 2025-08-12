"""Handler for finding words that need embeddings.

This handler reads from DynamoDB to find words without embeddings,
chunks them into batches, and uploads to S3 for processing.
"""

import os
import logging
from typing import Any, Dict
from receipt_label.embedding.word import (
    chunk_into_embedding_batches,
    list_receipt_words_with_no_embeddings,
    serialize_receipt_words,
    upload_serialized_words,
)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Find receipt words without embeddings and prepare batches.
    
    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)
        
    Returns:
        Dictionary containing batches ready for processing
        
    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting find_unembedded_words handler")
    
    try:
        # Get S3 bucket from environment
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")
        
        logger.info("Using S3 bucket: %s", bucket)
        
        # Get words without embeddings (noise words are already filtered)
        words = list_receipt_words_with_no_embeddings()
        logger.info("Found %d words without embeddings (noise words filtered)", len(words))
        
        if not words:
            logger.info("No words need embeddings")
            return {"batches": []}
        
        # Chunk words into batches (returns nested dict structure)
        batches = chunk_into_embedding_batches(words)
        logger.info("Chunked into %d batches", len(batches))
        
        # Log batch details for debugging
        for image_id, receipts in batches.items():
            for receipt_id, words_list in receipts.items():
                total = len(words_list)
                unique = len({(w.line_id, w.word_id) for w in words_list})
                if total != unique:
                    logger.warning(
                        f"Duplicate words in image {image_id}, receipt {receipt_id}: "
                        f"total {total}, unique {unique}"
                    )
                else:
                    logger.info(
                        f"Words count OK for image {image_id}, receipt {receipt_id}: "
                        f"{total} words"
                    )
        
        # Serialize and upload in one step
        uploaded = upload_serialized_words(
            serialize_receipt_words(batches), bucket
        )
        logger.info("Uploaded %d files", len(uploaded))
        
        # Clean the output to match expected format
        cleaned = [
            {
                "s3_key": e["s3_key"],
                "s3_bucket": e["s3_bucket"],
                "image_id": e["image_id"],
                "receipt_id": e["receipt_id"],
            }
            for e in uploaded
        ]
        
        logger.info("Successfully prepared %d batches for processing", len(cleaned))
        
        return {
            "batches": cleaned,
            "total_words": len(words),
            "batch_count": len(cleaned),
        }
        
    except AttributeError as e:
        logger.error("Client manager configuration error: %s", str(e))
        raise RuntimeError(f"Configuration error: {str(e)}") from e
        
    except KeyError as e:
        logger.error("Missing expected field in data: %s", str(e))
        raise RuntimeError(f"Data format error: {str(e)}") from e
        
    except Exception as e:
        logger.error("Unexpected error finding unembedded words: %s", str(e))
        raise RuntimeError(f"Internal error: {str(e)}") from e