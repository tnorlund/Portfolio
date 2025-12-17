"""Lambda handler for preparing chunk groups from ProcessChunksInParallel output.

This handler combines the logic of:
- GroupChunksForMerge (Pass state) - data restructuring
- CheckChunkGroupCount (Choice state) - decides if hierarchical merge needed
- CreateChunkGroups (Lambda) - creates groups and uploads to S3
- LoadChunkGroupsFromS3 (Lambda) - creates group index array

Single Lambda invocation replaces 5 Step Function states.

This is a lightweight handler that only uses boto3 (included in Lambda runtime).
No external dependencies required.
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

# Threshold for hierarchical merge - if chunks > this, use hierarchical merge
HIERARCHICAL_MERGE_THRESHOLD = 4

# Default group size for hierarchical merge
DEFAULT_GROUP_SIZE = 20

s3_client = boto3.client("s3")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Prepare chunk groups for merge, deciding between hierarchical and direct merge.

    This handler takes the output of ProcessChunksInParallel and prepares for merging:
    1. If total_chunks <= HIERARCHICAL_MERGE_THRESHOLD: skip hierarchical merge
    2. If total_chunks > HIERARCHICAL_MERGE_THRESHOLD: create groups for parallel merge

    Args:
        event: Lambda event containing:
            - chunked_data: Object with batch_id, total_chunks from PrepareChunks
            - chunk_results: Array of chunk result objects with intermediate_key
            - poll_results_s3_key: S3 key for poll results (from PrepareChunks)
            - poll_results_s3_bucket: S3 bucket for poll results

    Returns:
        Dictionary containing:
            - use_hierarchical_merge: True if hierarchical merge needed, False for direct
            - batch_id: Batch ID
            - For hierarchical merge:
                - chunk_groups: Object with groups array ready for Map state
            - For direct merge:
                - chunk_results: Array of chunk results for direct final merge
            - poll_results_s3_key: Passed through for MarkBatchesComplete
            - poll_results_s3_bucket: Passed through for MarkBatchesComplete
    """
    logger.info("Starting prepare_chunk_groups handler")
    logger.info("Event: %s", json.dumps(event))

    try:
        # Extract data from event - comes from ProcessChunksInParallel output
        chunked_data = event.get("chunked_data", {})
        chunk_results = event.get("chunk_results", [])

        # Get batch_id and total_chunks from chunked_data
        batch_id = chunked_data.get("batch_id")
        total_chunks = chunked_data.get("total_chunks", len(chunk_results))

        # Get poll_results S3 info (passed through from PrepareChunks)
        poll_results_s3_key = chunked_data.get("poll_results_s3_key")
        poll_results_s3_bucket = chunked_data.get("poll_results_s3_bucket")

        # Get group_size from event or use default
        group_size = event.get("group_size", DEFAULT_GROUP_SIZE)

        # Get database type (words or lines)
        database = event.get("database", "words")

        if not batch_id:
            raise ValueError("batch_id is required in chunked_data")

        logger.info(
            "PrepareChunkGroups: batch_id=%s, total_chunks=%d, chunk_results=%d, "
            "group_size=%d, threshold=%d",
            batch_id,
            total_chunks,
            len(chunk_results),
            group_size,
            HIERARCHICAL_MERGE_THRESHOLD,
        )

        # Decide if hierarchical merge is needed
        use_hierarchical_merge = total_chunks > HIERARCHICAL_MERGE_THRESHOLD

        if not use_hierarchical_merge:
            # Direct merge path - skip hierarchical merge
            logger.info(
                "Skipping hierarchical merge (chunks=%d <= threshold=%d)",
                total_chunks,
                HIERARCHICAL_MERGE_THRESHOLD,
            )

            return {
                "use_hierarchical_merge": False,
                "batch_id": batch_id,
                "chunk_results": chunk_results,
                "total_chunks": total_chunks,
                "database": database,
                # Pass through poll_results S3 info for MarkBatchesComplete
                "poll_results_s3_key": poll_results_s3_key,
                "poll_results_s3_bucket": poll_results_s3_bucket,
            }

        # Hierarchical merge path - create groups
        logger.info(
            "Using hierarchical merge (chunks=%d > threshold=%d), group_size=%d",
            total_chunks,
            HIERARCHICAL_MERGE_THRESHOLD,
            group_size,
        )

        # Partition chunk_results into groups
        groups: List[List[Dict[str, Any]]] = []
        for i in range(0, len(chunk_results), group_size):
            group = chunk_results[i : i + group_size]
            groups.append(group)

        logger.info("Created %d chunk groups", len(groups))

        # Get S3 bucket from environment
        bucket = os.environ.get("CHROMADB_BUCKET")
        if not bucket:
            raise ValueError("CHROMADB_BUCKET environment variable not set")

        # Upload groups to S3 (always use S3 to avoid payload limits)
        groups_s3_key = f"chunk_groups/{batch_id}/groups.json"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp_file:
            json.dump(groups, tmp_file, indent=2)
            tmp_file_path = tmp_file.name

        try:
            s3_client.upload_file(
                tmp_file_path,
                bucket,
                groups_s3_key,
            )
            logger.info(
                "Uploaded groups to S3: bucket=%s, key=%s, groups=%d",
                bucket,
                groups_s3_key,
                len(groups),
            )
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

        # Create group index array for Map state
        # Each entry contains the info needed for MergeSingleChunkGroup
        group_indices = [
            {
                "group_index": i,
                "chunk_group": None,  # Will be downloaded by merge handler from S3
                "groups_s3_key": groups_s3_key,
                "groups_s3_bucket": bucket,
            }
            for i in range(len(groups))
        ]

        logger.info(
            "Created %d group index entries for Map state", len(group_indices)
        )

        # Return hierarchical merge response
        # chunk_groups structure matches what MergeChunkGroupsInParallel expects
        return {
            "use_hierarchical_merge": True,
            "batch_id": batch_id,
            "database": database,
            "chunk_groups": {
                "batch_id": batch_id,
                "groups": group_indices,
                "total_groups": len(groups),
                "groups_s3_key": groups_s3_key,
                "groups_s3_bucket": bucket,
            },
            # Pass through poll_results S3 info for MarkBatchesComplete
            "poll_results_s3_key": poll_results_s3_key,
            "poll_results_s3_bucket": poll_results_s3_bucket,
        }

    except ValueError as e:
        logger.error("Validation error: %s", str(e))
        raise

    except Exception as e:
        logger.error("Unexpected error: %s", str(e), exc_info=True)
        raise
