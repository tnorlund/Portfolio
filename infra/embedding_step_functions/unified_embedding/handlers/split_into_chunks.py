"""Handler for splitting delta results into chunks for parallel processing.

This handler takes a list of delta results and splits them into chunks of 10
for efficient parallel processing by the compaction Lambda.
"""

import logging
from typing import Any, Dict, List

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
CHUNK_SIZE = 10  # Max deltas per chunk as per compaction requirements


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
    logger.info("Starting split_into_chunks handler")

    try:
        # Extract parameters
        batch_id = event.get("batch_id")
        poll_results = event.get("poll_results", [])

        if not batch_id:
            raise ValueError("batch_id is required")

        logger.info(
            f"Processing {len(poll_results)} delta results for batch {batch_id}"
        )

        # Filter out any invalid results
        valid_deltas = []
        for result in poll_results:
            # Each result should have delta_key and collection info
            if isinstance(result, dict) and "delta_key" in result:

                # Ensure collection is set (default to receipt_words for backward compat)
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
                f"Chunk {chunk['chunk_index']}: {len(chunk_deltas)} deltas, "
                f"collections: {set(d.get('collection', 'unknown') for d in chunk_deltas)}"
            )

        logger.info(
            f"Created {len(chunks)} chunks from {len(valid_deltas)} deltas"
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
