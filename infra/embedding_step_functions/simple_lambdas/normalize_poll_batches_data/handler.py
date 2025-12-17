"""Lambda handler for preparing chunks from poll results.

This handler combines the functionality of:
- NormalizePollWordBatchesData (downloading and combining individual poll results)
- SplitWordIntoChunks (splitting into chunks and uploading to S3)
- LoadChunksFromS3 (creating chunk indices)

This simplification:
- Reduces Lambda invocations from 3 to 1
- Eliminates intermediate Step Functions state
- Makes data flow clearer
- Ensures poll_results_s3_key is always available for MarkBatchesComplete
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, List

import boto3

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
CHUNK_SIZE_WORDS = int(os.environ.get("CHUNK_SIZE_WORDS", "15"))
CHUNK_SIZE_LINES = int(os.environ.get("CHUNK_SIZE_LINES", "25"))

s3_client = boto3.client("s3")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Prepare chunks for parallel processing.

    Takes poll results (S3 references from PollBatches Map), combines them,
    creates chunks, and returns chunk indices for the Map state.

    Args:
        event: Lambda event containing:
            - poll_results: Array of S3 references from PollBatches Map
              Each: {"batch_id": "...", "result_s3_key": "...", "result_s3_bucket": "..."}
            - batch_id: Execution ID for tracking
            - database: "words" or "lines" (determines chunk size)

    Returns:
        Dictionary containing:
            - batch_id: Passed through for tracking
            - chunks: Array of chunk indices for Map state
            - total_chunks: Number of chunks created
            - chunks_s3_key: S3 key where full chunks are stored
            - chunks_s3_bucket: S3 bucket
            - poll_results_s3_key: S3 key for combined poll results (for MarkBatchesComplete)
            - poll_results_s3_bucket: S3 bucket
            - has_chunks: Boolean for Step Functions Choice state
    """
    poll_results_refs = event.get("poll_results", [])
    batch_id = event.get("batch_id")
    database = event.get("database", "words")

    if not batch_id:
        raise ValueError("batch_id is required")

    bucket = os.environ.get("CHROMADB_BUCKET")
    if not bucket:
        raise ValueError("CHROMADB_BUCKET environment variable not set")

    chunk_size = CHUNK_SIZE_WORDS if database == "words" else CHUNK_SIZE_LINES

    logger.info(
        "Starting prepare_chunks: batch_id=%s, database=%s, chunk_size=%d, poll_results_count=%d",
        batch_id,
        database,
        chunk_size,
        len(poll_results_refs) if poll_results_refs else 0,
    )

    # Step 1: Download and combine all individual poll results from S3
    combined_results = _download_and_combine_poll_results(poll_results_refs)

    if not combined_results:
        logger.info("No poll results to process")
        return {
            "batch_id": batch_id,
            "chunks": [],
            "total_chunks": 0,
            "chunks_s3_key": None,
            "chunks_s3_bucket": None,
            "poll_results_s3_key": None,
            "poll_results_s3_bucket": None,
            "has_chunks": False,
        }

    logger.info(
        "Combined poll results: batch_id=%s, combined_count=%d",
        batch_id,
        len(combined_results),
    )

    # Step 2: Upload combined poll_results to S3 (needed by MarkBatchesComplete)
    poll_results_s3_key = f"poll_results/{batch_id}/poll_results.json"
    _upload_json_to_s3(combined_results, bucket, poll_results_s3_key)

    logger.info(
        "Uploaded combined poll_results to S3: s3_key=%s, bucket=%s, count=%d",
        poll_results_s3_key,
        bucket,
        len(combined_results),
    )

    # Step 3: Filter valid deltas and create chunks
    valid_deltas = _filter_valid_deltas(combined_results, database)

    if not valid_deltas:
        logger.info("No valid deltas to process after filtering")
        return {
            "batch_id": batch_id,
            "chunks": [],
            "total_chunks": 0,
            "chunks_s3_key": None,
            "chunks_s3_bucket": None,
            "poll_results_s3_key": poll_results_s3_key,
            "poll_results_s3_bucket": bucket,
            "has_chunks": False,
        }

    chunks = _create_chunks(valid_deltas, chunk_size, batch_id)

    logger.info(
        "Created chunks: batch_id=%s, delta_count=%d, chunk_count=%d",
        batch_id,
        len(valid_deltas),
        len(chunks),
    )

    # Step 4: Upload full chunks to S3
    chunks_s3_key = f"chunks/{batch_id}/chunks.json"
    _upload_json_to_s3(chunks, bucket, chunks_s3_key)

    logger.info(
        "Uploaded chunks to S3: s3_key=%s, bucket=%s, chunk_count=%d",
        chunks_s3_key,
        bucket,
        len(chunks),
    )

    # Step 5: Create chunk indices for Map state (minimal payload)
    chunk_indices = [
        {
            "chunk_index": i,
            "batch_id": batch_id,
            "chunks_s3_key": chunks_s3_key,
            "chunks_s3_bucket": bucket,
        }
        for i in range(len(chunks))
    ]

    return {
        "batch_id": batch_id,
        "chunks": chunk_indices,
        "total_chunks": len(chunks),
        "chunks_s3_key": chunks_s3_key,
        "chunks_s3_bucket": bucket,
        "poll_results_s3_key": poll_results_s3_key,
        "poll_results_s3_bucket": bucket,
        "has_chunks": True,
    }


def _download_and_combine_poll_results(
    poll_results_refs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Download individual poll results from S3 and combine them."""
    if not poll_results_refs:
        return []

    combined = []

    for ref in poll_results_refs:
        if not isinstance(ref, dict):
            logger.warning("Skipping non-dict poll result reference: %s", ref)
            continue

        # Check if this is an S3 reference
        result_bucket = ref.get("result_s3_bucket")
        result_key = ref.get("result_s3_key")

        if result_bucket and result_key:
            # Download from S3
            try:
                with tempfile.NamedTemporaryFile(
                    mode="r", suffix=".json", delete=False
                ) as tmp_file:
                    tmp_file_path = tmp_file.name

                s3_client.download_file(
                    result_bucket, result_key, tmp_file_path
                )

                with open(tmp_file_path, "r", encoding="utf-8") as f:
                    result = json.load(f)

                # Flatten: if list, extend; if dict, append
                if isinstance(result, list):
                    combined.extend(result)
                elif isinstance(result, dict):
                    combined.append(result)

                os.unlink(tmp_file_path)

            except Exception as e:
                logger.error(
                    "Failed to download poll result from S3: bucket=%s, key=%s, error=%s",
                    result_bucket,
                    result_key,
                    str(e),
                )
                # Continue with other results
        else:
            # Legacy format: direct result object (has delta_key or other data)
            combined.append(ref)

    return combined


def _filter_valid_deltas(
    poll_results: List[Dict[str, Any]], database: str
) -> List[Dict[str, Any]]:
    """Filter poll results to only include valid deltas."""
    valid = []

    for result in poll_results:
        if not isinstance(result, dict):
            continue

        # Must have delta_key
        if "delta_key" not in result:
            logger.debug("Skipping result without delta_key: %s", result)
            continue

        # Set collection if not present
        if "collection" not in result:
            result["collection"] = database

        valid.append(result)

    return valid


def _create_chunks(
    deltas: List[Dict[str, Any]], chunk_size: int, batch_id: str
) -> List[Dict[str, Any]]:
    """Split deltas into chunks."""
    chunks = []

    for i in range(0, len(deltas), chunk_size):
        chunk_deltas = deltas[i : i + chunk_size]
        chunks.append(
            {
                "chunk_index": len(chunks),
                "batch_id": batch_id,
                "delta_results": chunk_deltas,
            }
        )

    return chunks


def _upload_json_to_s3(data: Any, bucket: str, key: str) -> None:
    """Upload JSON data to S3."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp_file:
        json.dump(data, tmp_file, indent=2)
        tmp_file_path = tmp_file.name

    try:
        s3_client.upload_file(tmp_file_path, bucket, key)
    finally:
        try:
            os.unlink(tmp_file_path)
        except Exception:
            pass
