"""Lambda handler for creating chunk groups from chunk results.

This handler partitions chunk results into groups for hierarchical merging,
handling large arrays that might exceed Step Functions payload limits.
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

# Step Functions payload limit is 256KB (262,144 bytes)
# We'll use S3 if the response would exceed 150KB to leave a large buffer
MAX_PAYLOAD_SIZE = 150 * 1024  # 150KB (conservative)

s3_client = boto3.client("s3")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Create chunk groups from chunk results, or load groups from S3.

    Args:
        event: Lambda event containing:
            - operation: "create" (default) or "load_groups_from_s3"
            - For "create":
                - chunk_results: Array of chunk result objects with intermediate_key
                - batch_id: Batch ID for tracking
                - group_size: Size of each group (default: 10)
                - poll_results: Poll results to pass through
            - For "load_groups_from_s3":
                - groups_s3_key: S3 key where groups are stored
                - groups_s3_bucket: S3 bucket where groups are stored
                - batch_id: Batch ID for tracking
                - poll_results: Poll results to pass through

    Returns:
        Dictionary containing:
            - batch_id: Passed through
            - groups: Array of chunk groups (each group is an array of chunk results)
            - total_groups: Total number of groups created
            - poll_results: Passed through
            - use_s3: True if groups are in S3, False if inline
    """
    operation = event.get("operation", "create")

    if operation == "load_groups_from_s3":
        return _load_groups_from_s3(event)
    else:
        return _create_chunk_groups(event)


def _load_groups_from_s3(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create group index array instead of loading full groups.

    Instead of loading all groups (which would exceed Step Functions payload limit),
    we create an array of group indices and S3 metadata. Each processing Lambda
    will download its specific group from S3 using the group_index.
    """
    groups_s3_key = event.get("groups_s3_key")
    groups_s3_bucket = event.get("groups_s3_bucket")
    batch_id = event.get("batch_id")
    poll_results_s3_key = event.get("poll_results_s3_key")
    poll_results_s3_bucket = event.get("poll_results_s3_bucket")

    if not groups_s3_key or not groups_s3_bucket:
        raise ValueError("groups_s3_key and groups_s3_bucket are required for load_groups_from_s3 operation")

    # Get total_groups from S3 metadata or download just to count
    total_groups = None
    with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_file:
        tmp_file_path = tmp_file.name

    try:
        s3_client.download_file(groups_s3_bucket, groups_s3_key, tmp_file_path)
        with open(tmp_file_path, "r", encoding="utf-8") as f:
            groups = json.load(f)
        total_groups = len(groups)
        logger.info(
            "Found %d groups in S3 for batch %s",
            total_groups,
            batch_id,
        )
    finally:
        try:
            os.unlink(tmp_file_path)
        except Exception:
            pass

    if total_groups is None:
        raise ValueError("Could not determine total_groups from S3")

    # DO NOT load poll_results from S3 here - it's too large and would exceed payload limit
    # Keep it in S3 and let downstream steps load it when needed
    # poll_results will be loaded by MarkBatchesComplete or PrepareHierarchicalFinalMerge

    # Create an array of group metadata (indices + S3 info) instead of full groups
    # Each processing Lambda will download its specific group from S3
    group_indices = [
        {
            "group_index": i,
            "batch_id": batch_id,
            "groups_s3_key": groups_s3_key,
            "groups_s3_bucket": groups_s3_bucket,
            "chunk_group": None,  # Not available in S3 mode - will be downloaded by Lambda
        }
        for i in range(total_groups)
    ]

    logger.info(
        "Created %d group index entries for batch %s (groups and poll_results remain in S3)",
        len(group_indices),
        batch_id,
    )

    # Return response - keep poll_results in S3 to avoid payload limit
    # Always include poll_results_s3_key and poll_results_s3_bucket (even if None) for JSONPath compatibility
    response = {
        "batch_id": batch_id,
        "groups": group_indices,  # Array of group indices, not full groups
        "total_groups": total_groups,
        "use_s3": True,  # Groups are still in S3
        "groups_s3_key": groups_s3_key,  # Pass through S3 info
        "groups_s3_bucket": groups_s3_bucket,
        "poll_results_s3_key": poll_results_s3_key,  # Always include, even if None
        "poll_results_s3_bucket": poll_results_s3_bucket,  # Always include, even if None
    }

    # Set poll_results based on whether it's in S3 or inline
    if poll_results_s3_key and poll_results_s3_bucket:
        response["poll_results"] = None  # Keep in S3, will be loaded when needed
    else:
        response["poll_results"] = []  # Empty if not in S3

    return response


def _create_chunk_groups(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create chunk groups from chunk results."""
    logger.info("Starting create_chunk_groups handler")

    try:
        chunk_results = event.get("chunk_results", [])
        batch_id = event.get("batch_id")
        group_size = event.get("group_size", 10)
        poll_results = event.get("poll_results", [])

        if not batch_id:
            raise ValueError("batch_id is required")

        logger.info(
            "Creating chunk groups: %d chunks, group_size=%d, batch %s",
            len(chunk_results),
            group_size,
            batch_id,
        )

        # Partition chunk_results into groups
        groups: List[List[Dict[str, Any]]] = []
        for i in range(0, len(chunk_results), group_size):
            group = chunk_results[i : i + group_size]
            groups.append(group)

        logger.info(
            "Created %d chunk groups for batch %s",
            len(groups),
            batch_id,
        )

        # Always use S3 to avoid payload size issues
        # This ensures we never hit Step Functions' 256KB limit
        logger.info(
            "Always uploading groups to S3 (chunks: %d, groups: %d)",
            len(chunk_results),
            len(groups),
        )

        # Get S3 bucket from environment
        bucket = os.environ.get("CHROMADB_BUCKET")
        if not bucket:
            raise ValueError("CHROMADB_BUCKET environment variable not set")

        # Upload groups to S3
        groups_s3_key = f"chunk_groups/{batch_id}/groups.json"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
            json.dump(groups, tmp_file, indent=2)
            tmp_file_path = tmp_file.name

        try:
            s3_client.upload_file(
                tmp_file_path,
                bucket,
                groups_s3_key,
            )
            logger.info(
                "Uploaded %d groups to S3: %s/%s",
                len(groups),
                bucket,
                groups_s3_key,
            )
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

        # Check if poll_results is too large - if so, also store it in S3
        poll_results_payload = json.dumps(poll_results)
        poll_results_size = len(poll_results_payload.encode("utf-8"))

        # Estimate total response size (without poll_results)
        base_response = {
            "batch_id": batch_id,
            "groups_s3_key": groups_s3_key,
            "groups_s3_bucket": bucket,
            "total_groups": len(groups),
            "use_s3": True,
        }
        base_response_size = len(json.dumps(base_response).encode("utf-8"))

        # If poll_results is large, also store it in S3
        poll_results_s3_key = None
        if poll_results_size > 100 * 1024:  # If poll_results > 100KB, store in S3
            logger.info(
                "poll_results is large (%d KB), also storing in S3",
                poll_results_size // 1024,
            )
            poll_results_s3_key = f"chunk_groups/{batch_id}/poll_results.json"

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
                json.dump(poll_results, tmp_file, indent=2)
                tmp_file_path = tmp_file.name

            try:
                s3_client.upload_file(
                    tmp_file_path,
                    bucket,
                    poll_results_s3_key,
                )
                logger.info(
                    "Uploaded poll_results to S3: %s/%s",
                    bucket,
                    poll_results_s3_key,
                )
            finally:
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

        # Return response - always include all fields (even if null) for JSONPath compatibility
        # Step Functions JSONPath fails if a field doesn't exist, so we must always include these
        response = {
            "batch_id": batch_id,
            "groups_s3_key": groups_s3_key,
            "groups_s3_bucket": bucket,
            "total_groups": len(groups),
            "use_s3": True,
            "poll_results_s3_key": poll_results_s3_key,  # Always include, even if None
            "poll_results_s3_bucket": bucket if poll_results_s3_key else None,  # Always include, even if None
        }

        if poll_results_s3_key:
            # poll_results is in S3 - set poll_results to null
            response["poll_results"] = None
        else:
            # poll_results is small enough to include inline
            response["poll_results"] = poll_results

        return response

    except ValueError as e:
        logger.error("Validation error: %s", str(e))
        return {
            "statusCode": 400,
            "error": str(e),
            "message": "Invalid input parameters",
        }

    except Exception as e:
        logger.error("Unexpected error creating chunk groups: %s", str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Failed to create chunk groups",
        }
