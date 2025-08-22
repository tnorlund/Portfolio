"""Handler for splitting delta results into chunks for parallel processing.

This handler takes a list of delta results and splits them into chunks of 10
for efficient parallel processing by the compaction Lambda.
"""

import os
from typing import Any, Dict, List

import utils.logging

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

# Configuration - get chunk size from environment
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "10"))  # Default 10 if not set


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
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
    logger.info("Starting split_into_chunks handler", chunk_size=CHUNK_SIZE)

    try:
        # Extract parameters
        batch_id = event.get("batch_id")
        poll_results = event.get("poll_results", [])

        if not batch_id:
            raise ValueError("batch_id is required")

        logger.info(
            "Processing delta results for batch",
            delta_count=len(poll_results),
            batch_id=batch_id,
        )

        # Filter out any invalid results
        valid_deltas = []
        for result in poll_results:
            # Each result should have delta_key and collection info
            if isinstance(result, dict) and "delta_key" in result:

                # Ensure collection is set (default to words for backward compat)
                if "collection" not in result:
                    result["collection"] = "words"
                valid_deltas.append(result)
            else:
                logger.warning("Skipping invalid delta result", result=result)

        if not valid_deltas:
            logger.info("No valid deltas to process")
            return {
                "batch_id": batch_id,
                "chunks": [],
                "total_chunks": 0,
            }

        # Split into chunks
        chunks: List[Dict[str, Any]] = []
        for i in range(0, len(valid_deltas), CHUNK_SIZE):
            chunk_deltas = valid_deltas[i : i + CHUNK_SIZE]
            chunk = {
                "chunk_index": len(chunks),
                "batch_id": batch_id,
                "delta_results": chunk_deltas,
                "operation": "process_chunk",
            }
            chunks.append(chunk)

            # Log chunk details for debugging
            logger.info(
                "Chunk details",
                chunk_index=chunk["chunk_index"],
                delta_count=len(chunk_deltas),
                collections=list(
                    set(d.get("collection", "unknown") for d in chunk_deltas)
                ),
            )

        logger.info(
            "Created chunks from deltas",
            chunk_count=len(chunks),
            delta_count=len(valid_deltas),
        )

        return {
            "batch_id": batch_id,
            "chunks": chunks,
            "total_chunks": len(chunks),
        }

    except ValueError as e:
        logger.error("Validation error", error=str(e))
        return {
            "statusCode": 400,
            "error": str(e),
            "message": "Invalid input parameters",
        }

    except Exception as e:
        logger.error("Unexpected error splitting into chunks", error=str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Failed to split deltas into chunks",
        }
