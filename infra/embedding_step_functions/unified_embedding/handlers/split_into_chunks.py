"""Handler for splitting delta results into chunks for parallel processing.

This handler takes a list of delta results and splits them into chunks of 10
for efficient parallel processing by the compaction Lambda.

To avoid Step Functions' 256KB payload limit, chunks are uploaded to S3 when
the response would be too large, and S3 keys are returned instead.
"""

import json
import os
import tempfile
from typing import Any, Dict, List

import boto3

import utils.logging

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

# Configuration - get chunk size from environment
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "10"))  # Default 10 if not set

# Step Functions payload limit is 256KB (262,144 bytes)
# We'll use S3 if the response would exceed 150KB to leave a large buffer
# This is conservative because JSON serialization and Step Functions overhead can add significant size
MAX_PAYLOAD_SIZE = 150 * 1024  # 150KB (conservative)

s3_client = boto3.client("s3")


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Split delta results into chunks for parallel processing, or load chunks from S3.

    Args:
        event: Lambda event containing:
            - operation: "split" (default) or "load_chunks_from_s3"
            - For "split":
            - batch_id: Unique identifier for this compaction batch
            - poll_results: Array of delta results from polling
            - For "load_chunks_from_s3":
                - chunks_s3_key: S3 key where chunks are stored
                - chunks_s3_bucket: S3 bucket where chunks are stored
                - batch_id: Batch ID for tracking

    Returns:
        Dictionary containing:
            - batch_id: Passed through for tracking
            - chunks: Array of chunk objects for Map state processing
            - total_chunks: Total number of chunks created
            - use_s3: False (chunks are now inline)
    """
    operation = event.get("operation", "split")

    if operation == "load_chunks_from_s3":
        return _load_chunks_from_s3(event)
    else:
        return _split_into_chunks(event)


def _load_chunks_from_s3(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create chunk index array instead of loading full chunks.

    Instead of loading all chunks (which would exceed Step Functions payload limit),
    we create an array of chunk indices and S3 metadata. Each processing Lambda
    will download its specific chunk from S3 using the chunk_index.
    """
    chunks_s3_key = event.get("chunks_s3_key")
    chunks_s3_bucket = event.get("chunks_s3_bucket")
    batch_id = event.get("batch_id")
    total_chunks = event.get("total_chunks")

    if not chunks_s3_key or not chunks_s3_bucket:
        raise ValueError("chunks_s3_key and chunks_s3_bucket are required for load_chunks_from_s3 operation")

    if total_chunks is None:
        # Need to get total_chunks from S3 metadata or download just to count
        # For now, we'll download just to get the count (minimal overhead)
        with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_file:
            tmp_file_path = tmp_file.name

        try:
            s3_client.download_file(chunks_s3_bucket, chunks_s3_key, tmp_file_path)
            with open(tmp_file_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            total_chunks = len(chunks)
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

    logger.info(
        "Creating chunk index array for S3 chunks",
        s3_key=chunks_s3_key,
        bucket=chunks_s3_bucket,
        batch_id=batch_id,
        total_chunks=total_chunks,
    )

    # Create an array of chunk metadata (indices + S3 info) instead of full chunks
    # Each processing Lambda will download its specific chunk from S3
    # Include delta_results as None so Step Functions can reference it without errors
    chunk_indices = [
        {
            "chunk_index": i,
            "batch_id": batch_id,
            "chunks_s3_key": chunks_s3_key,
            "chunks_s3_bucket": chunks_s3_bucket,
            "operation": "process_chunk",
            "delta_results": None,  # Not available in S3 mode - will be downloaded by Lambda
        }
        for i in range(total_chunks)
    ]

    logger.info(
        "Created chunk index array",
        chunk_count=len(chunk_indices),
        batch_id=batch_id,
    )

    return {
        "batch_id": batch_id,
        "chunks": chunk_indices,  # Array of chunk indices, not full chunks
        "total_chunks": len(chunk_indices),
        "use_s3": True,  # Chunks still in S3 - each Lambda will fetch its own
        "chunks_s3_key": chunks_s3_key,
        "chunks_s3_bucket": chunks_s3_bucket,
    }


def _split_into_chunks(event: Dict[str, Any]) -> Dict[str, Any]:
    """Split delta results into chunks for parallel processing."""
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
                "use_s3": False,
                "chunks_s3_key": None,  # Always include these fields for consistency
                "chunks_s3_bucket": None,
            }

        # Estimate if we'll need S3 based on number of deltas
        # Each delta result can be ~1-3KB (includes delta_key, batch_id, embedding_count, etc.)
        # Each chunk adds ~500 bytes overhead (chunk_index, batch_id, operation)
        # With 10 deltas per chunk, ~20 batches = ~20 chunks = ~40-60KB, which is safe
        # But ~50 batches = ~50 chunks = ~100-150KB, which is risky
        estimated_chunks = (len(valid_deltas) + CHUNK_SIZE - 1) // CHUNK_SIZE
        estimated_size_per_delta = 2500  # ~2.5KB per delta result (conservative)
        estimated_size_per_chunk_overhead = 500  # ~500 bytes per chunk overhead
        estimated_total_size = (
            len(valid_deltas) * estimated_size_per_delta
            + estimated_chunks * estimated_size_per_chunk_overhead
            + 2000  # +2KB for batch_id, total_chunks, JSON overhead
        )

        # Use S3 if we estimate it will be too large, or if we have many deltas
        # Be very conservative - ALWAYS use S3 if we have more than 1 chunk
        # This ensures we never hit the payload limit, even with large delta results
        # Single chunk (<=10 deltas) should be safe, but multiple chunks = use S3
        use_s3_early = (
            estimated_total_size > MAX_PAYLOAD_SIZE
            or estimated_chunks > 1  # More than 1 chunk = ALWAYS use S3 to be safe
            or len(valid_deltas) > CHUNK_SIZE  # More deltas than fit in one chunk = use S3
        )

        logger.info(
            "Size estimation",
            delta_count=len(valid_deltas),
            estimated_chunks=estimated_chunks,
            estimated_size_kb=estimated_total_size / 1024,
            use_s3_early=use_s3_early,
        )

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

        # Check actual payload size (double-check even if we estimated)
        response_payload = json.dumps({
            "batch_id": batch_id,
            "chunks": chunks,
            "total_chunks": len(chunks),
        })
        payload_size = len(response_payload.encode("utf-8"))

        logger.info(
            "Response payload size",
            size_bytes=payload_size,
            size_kb=payload_size / 1024,
            max_size_kb=MAX_PAYLOAD_SIZE / 1024,
        )

        # If payload is too large (or we estimated it would be), upload chunks to S3
        if use_s3_early or payload_size > MAX_PAYLOAD_SIZE:
            logger.info(
                "Response payload exceeds limit, uploading chunks to S3",
                payload_size_kb=payload_size / 1024,
            )

            # Get S3 bucket from environment
            bucket = os.environ.get("CHROMADB_BUCKET")
            if not bucket:
                raise ValueError("CHROMADB_BUCKET environment variable not set")

            # Upload chunks to S3
            chunks_s3_key = f"chunks/{batch_id}/chunks.json"

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
                json.dump(chunks, tmp_file, indent=2)
                tmp_file_path = tmp_file.name

            try:
                s3_client.upload_file(
                    tmp_file_path,
                    bucket,
                    chunks_s3_key,
                )
                logger.info(
                    "Uploaded chunks to S3",
                    s3_key=chunks_s3_key,
                    bucket=bucket,
                    chunk_count=len(chunks),
                )
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

            # Return S3 reference instead of full chunks
            return {
                "batch_id": batch_id,
                "chunks_s3_key": chunks_s3_key,
                "chunks_s3_bucket": bucket,
                "total_chunks": len(chunks),
                "use_s3": True,
            }
        else:
            # Response is small enough, return chunks directly
            logger.info("Response payload within limit, returning chunks directly")
            return {
                "batch_id": batch_id,
                "chunks": chunks,
                "total_chunks": len(chunks),
                "use_s3": False,
                "chunks_s3_key": None,  # Always include these fields for consistency
                "chunks_s3_bucket": None,
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
