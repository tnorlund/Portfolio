"""Lambda handler for preparing merge pairs in the parallel reduce pattern.

This handler takes a list of intermediate results and groups them into pairs
for parallel merging. It continues until only one intermediate remains.
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

MAX_PAYLOAD_SIZE = 150 * 1024  # 150KB (conservative)

s3_client = boto3.client("s3")


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """
    Prepare pairs of intermediates for parallel merging.

    This implements the "reduce" step of MapReduce pattern:
    - Takes N intermediates
    - Groups them into ceil(N/2) pairs
    - Returns pairs for parallel merging
    - Indicates when only 1 remains (done)

    Input:
    {
        "intermediates": [
            {"intermediate_key": "intermediate/batch-1/chunk-0/"},
            {"intermediate_key": "intermediate/batch-1/chunk-1/"},
            ...
        ],
        "batch_id": "batch-uuid",
        "database": "words" or "lines",
        "round": 0,  # Current reduction round
        "poll_results_s3_key": "...",  # Pass through for final merge
        "poll_results_s3_bucket": "..."
    }

    Output (if more than 1 intermediate):
    {
        "done": False,
        "pairs": [
            {
                "pair_index": 0,
                "batch_id": "batch-uuid",
                "intermediates": [
                    {"intermediate_key": "..."},
                    {"intermediate_key": "..."}
                ],
                "database": "words",
                "round": 1
            },
            ...
        ],
        "pair_count": 4,
        "batch_id": "batch-uuid",
        "database": "words",
        "round": 1,
        "poll_results_s3_key": "...",
        "poll_results_s3_bucket": "..."
    }

    Output (if exactly 1 intermediate - done):
    {
        "done": True,
        "single_intermediate": {"intermediate_key": "..."},
        "batch_id": "batch-uuid",
        "database": "words",
        "poll_results_s3_key": "...",
        "poll_results_s3_bucket": "..."
    }
    """
    logger.info("Starting PrepareMergePairs handler")

    batch_id = event.get("batch_id")
    database = event.get("database", "words")
    current_round = event.get("round", 0)
    poll_results_s3_key = event.get("poll_results_s3_key")
    poll_results_s3_bucket = event.get("poll_results_s3_bucket")

    # Get intermediates - could be from various sources
    intermediates = event.get("intermediates", [])

    # Also check chunk_results (from ProcessWordChunksInParallel output)
    if not intermediates:
        intermediates = event.get("chunk_results", [])

    # Also check merged_results (from MergePairsInParallel output)
    if not intermediates:
        intermediates = event.get("merged_results", [])

    if not batch_id:
        raise ValueError("batch_id is required")

    logger.info(
        "PrepareMergePairs received: batch_id=%s, database=%s, intermediate_count=%d, round=%d",
        batch_id,
        database,
        len(intermediates),
        current_round,
    )

    # Filter out invalid/error results
    valid_intermediates = []
    for item in intermediates:
        if isinstance(item, dict):
            # Skip error responses
            status_code = item.get("statusCode")
            if status_code is not None and status_code >= 400:
                logger.warning("Skipping error result: %s", item)
                continue
            # Skip empty results
            if item.get("empty"):
                continue
            # Must have intermediate_key
            if "intermediate_key" in item:
                valid_intermediates.append(item)
            else:
                logger.warning(
                    "Skipping item without intermediate_key: %s", item
                )

    logger.info(
        "Filtered intermediates: original_count=%d, valid_count=%d",
        len(intermediates),
        len(valid_intermediates),
    )

    # Base case: 0 intermediates - nothing to merge
    if len(valid_intermediates) == 0:
        logger.info("No valid intermediates - nothing to merge")
        return {
            "done": True,
            "single_intermediate": None,
            "batch_id": batch_id,
            "database": database,
            "poll_results_s3_key": poll_results_s3_key,
            "poll_results_s3_bucket": poll_results_s3_bucket,
            "message": "No valid intermediates to merge",
        }

    # Base case: 1 intermediate - we're done reducing
    if len(valid_intermediates) == 1:
        logger.info(
            "Single intermediate remaining - reduction complete: intermediate_key=%s",
            valid_intermediates[0].get("intermediate_key"),
        )
        return {
            "done": True,
            "single_intermediate": valid_intermediates[0],
            "batch_id": batch_id,
            "database": database,
            "poll_results_s3_key": poll_results_s3_key,
            "poll_results_s3_bucket": poll_results_s3_bucket,
        }

    # Group into pairs
    pairs: List[Dict[str, Any]] = []
    next_round = current_round + 1

    for i in range(0, len(valid_intermediates), 2):
        pair_intermediates = valid_intermediates[i : i + 2]
        pairs.append(
            {
                "pair_index": len(pairs),
                "batch_id": f"{batch_id}-r{next_round}",
                "intermediates": pair_intermediates,
                "database": database,
                "round": next_round,
            }
        )

    logger.info(
        "Created pairs for parallel merge: pair_count=%d, batch_id=%s, next_round=%d",
        len(pairs),
        batch_id,
        next_round,
    )

    # Check payload size and upload to S3 if too large
    result = {
        "done": False,
        "pairs": pairs,
        "pair_count": len(pairs),
        "batch_id": batch_id,
        "database": database,
        "round": next_round,
        "poll_results_s3_key": poll_results_s3_key,
        "poll_results_s3_bucket": poll_results_s3_bucket,
    }

    # Check if we need to upload pairs to S3
    result_json = json.dumps(result)
    if len(result_json) > MAX_PAYLOAD_SIZE:
        bucket = os.environ.get("CHROMADB_BUCKET")
        if not bucket:
            raise ValueError("CHROMADB_BUCKET environment variable not set")

        pairs_s3_key = f"merge_pairs/{batch_id}/round-{next_round}/pairs.json"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp_file:
            json.dump(pairs, tmp_file, indent=2)
            tmp_file_path = tmp_file.name

        try:
            s3_client.upload_file(tmp_file_path, bucket, pairs_s3_key)
            logger.info(
                "Uploaded pairs to S3 (payload too large): pair_count=%d, bucket=%s, s3_key=%s",
                len(pairs),
                bucket,
                pairs_s3_key,
            )
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

        # Return reference to S3 instead of inline pairs
        # Each pair entry will reference S3 for its data
        return {
            "done": False,
            "pairs": [
                {
                    "pair_index": i,
                    "batch_id": f"{batch_id}-r{next_round}",
                    "intermediates": None,  # Will be loaded from S3
                    "pairs_s3_key": pairs_s3_key,
                    "pairs_s3_bucket": bucket,
                    "database": database,
                    "round": next_round,
                }
                for i in range(len(pairs))
            ],
            "pair_count": len(pairs),
            "batch_id": batch_id,
            "database": database,
            "round": next_round,
            "use_s3": True,
            "pairs_s3_key": pairs_s3_key,
            "pairs_s3_bucket": bucket,
            "poll_results_s3_key": poll_results_s3_key,
            "poll_results_s3_bucket": poll_results_s3_bucket,
        }

    return result
