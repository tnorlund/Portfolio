"""Lambda handler for splitting delta results into chunks.

This function takes a list of delta results and splits them into dynamically-sized
chunks for efficient parallel processing by the compaction Lambda.

Memory optimization: Uses smaller chunks (5) for word embeddings which typically
consume more memory than line embeddings (10).
"""

import logging
import os
from typing import Any, Dict, List

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration with dynamic chunk sizing
DEFAULT_CHUNK_SIZE = 10  # Default for backward compatibility
CHUNK_SIZE_WORDS = int(
    os.environ.get("CHUNK_SIZE_WORDS", "5")
)  # Smaller for words
CHUNK_SIZE_LINES = int(
    os.environ.get("CHUNK_SIZE_LINES", "10")
)  # Standard for lines


def get_chunk_size(delta_results: List[Dict[str, Any]]) -> int:
    """Determine optimal chunk size based on collection type.

    Word embeddings typically consume more memory due to:
    - Higher volume per receipt
    - More metadata (bounding boxes, labels)
    - Larger documents (word text vs line text)

    Args:
        delta_results: List of delta results to analyze

    Returns:
        Optimal chunk size for this batch
    """
    # Check if this batch contains word embeddings
    has_words = any(
        d.get("collection") == "receipt_words" for d in delta_results
    )

    # Check if this batch contains line embeddings
    has_lines = any(
        d.get("collection") == "receipt_lines" for d in delta_results
    )

    # Mixed batch - use smaller chunk size to be safe
    if has_words and has_lines:
        logger.info("Mixed word/line batch detected, using word chunk size")
        return CHUNK_SIZE_WORDS

    # Pure word batch
    if has_words:
        logger.info(
            "Word embedding batch detected, using chunk size: %d",
            CHUNK_SIZE_WORDS,
        )
        return CHUNK_SIZE_WORDS

    # Pure line batch
    if has_lines:
        logger.info(
            "Line embedding batch detected, using chunk size: %d",
            CHUNK_SIZE_LINES,
        )
        return CHUNK_SIZE_LINES

    # Unknown or legacy batch - use default
    logger.info(
        "Unknown batch type, using default chunk size: %d", DEFAULT_CHUNK_SIZE
    )
    return DEFAULT_CHUNK_SIZE


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Split delta results into chunks for parallel processing.

    Args:
        event: Lambda event containing:
            - batch_id: Unique identifier for this compaction batch
            - poll_results: Array of delta results from polling

    Returns:
        Dictionary containing:
            - batch_id: Passed through for tracking
            - chunks: Array of chunk objects for Map state processing
            - total_chunks: Total number of chunks created
    """
    logger.info("Starting split_into_chunks handler")

    try:
        # Extract parameters
        batch_id = event.get("batch_id")
        poll_results = event.get("poll_results", [])

        if not batch_id:
            raise ValueError("batch_id is required")

        logger.info(
            "Processing %d delta results for batch %s",
            len(poll_results),
            batch_id,
        )

        # Filter out any invalid results
        valid_deltas = []
        for result in poll_results:
            # Each result should have delta_key and collection info
            if isinstance(result, dict) and "delta_key" in result:
                # Ensure collection is set (default to receipt_words for
                # backward compat)
                if "collection" not in result:
                    result["collection"] = "receipt_words"
                valid_deltas.append(result)
            else:
                logger.warning("Skipping invalid delta result: %s", result)

        if not valid_deltas:
            logger.info("No valid deltas to process")
            return {
                "batch_id": batch_id,
                "chunks": [],
                "total_chunks": 0,
            }

        # Determine optimal chunk size based on content
        chunk_size = get_chunk_size(valid_deltas)

        # Split into chunks
        chunks: List[Dict[str, Any]] = []
        for i in range(0, len(valid_deltas), chunk_size):
            chunk_deltas = valid_deltas[i : i + chunk_size]
            chunk = {
                "chunk_index": len(chunks),
                "batch_id": batch_id,
                "delta_results": chunk_deltas,
                "operation": "process_chunk",
            }
            chunks.append(chunk)

            # Log chunk details for debugging
            collections = set(
                d.get("collection", "unknown") for d in chunk_deltas
            )
            logger.info(
                "Chunk %d: %d deltas, collections: %s",
                chunk["chunk_index"],
                len(chunk_deltas),
                collections,
            )

        logger.info(
            "Created %d chunks from %d deltas", len(chunks), len(valid_deltas)
        )

        return {
            "batch_id": batch_id,
            "chunks": chunks,
            "total_chunks": len(chunks),
        }

    except ValueError as e:
        logger.error("Validation error: %s", str(e))
        return {
            "statusCode": 400,
            "error": str(e),
            "message": "Invalid input parameters",
        }

    except Exception as e:
        logger.error("Unexpected error splitting into chunks: %s", str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Failed to split deltas into chunks",
        }
