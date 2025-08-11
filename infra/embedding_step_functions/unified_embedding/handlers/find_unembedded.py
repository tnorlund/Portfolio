"""Handler for finding items that need embeddings.

Pure business logic - no Lambda-specific code.
"""

import os
from typing import Any, Dict
from receipt_label.embedding.line import (
    chunk_into_line_embedding_batches,
    list_receipt_lines_with_no_embeddings,
    serialize_receipt_lines,
    upload_serialized_lines,
)
from utils.logging import get_logger

logger = get_logger(__name__)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Find receipt lines without embeddings and prepare batches.
    
    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)
        
    Returns:
        Dictionary containing batches ready for processing
        
    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting find_unembedded_lines handler")
    
    try:
        # Get S3 bucket from environment
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")
        
        # Find lines without embeddings
        lines_without_embeddings = list_receipt_lines_with_no_embeddings()
        logger.info("Found %d lines without embeddings", len(lines_without_embeddings))
        
        # Chunk into batches
        batches = chunk_into_line_embedding_batches(lines_without_embeddings)
        logger.info("Chunked into %d batches", len(batches))
        
        # Serialize and upload to S3
        uploaded = upload_serialized_lines(
            serialize_receipt_lines(batches), 
            bucket
        )
        logger.info("Uploaded %d files", len(uploaded))
        
        # Format response
        cleaned = [
            {
                "s3_key": e["s3_key"],
                "s3_bucket": e["s3_bucket"],
                "image_id": e["image_id"],
                "receipt_id": e["receipt_id"],
            }
            for e in uploaded
        ]
        
        return {"batches": cleaned}
        
    except Exception as e:
        logger.error("Error finding unembedded lines: %s", str(e))
        raise RuntimeError(f"Error finding unembedded lines: {str(e)}") from e