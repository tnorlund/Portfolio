"""Handler for creating chunk groups from chunk results.

This handler partitions chunk results into groups for hierarchical merging,
handling large arrays that might exceed Step Functions payload limits.
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

# Step Functions payload limit is 256KB (262,144 bytes)
# We'll use S3 if the response would exceed 150KB to leave a large buffer
MAX_PAYLOAD_SIZE = 150 * 1024  # 150KB (conservative)

s3_client = boto3.client("s3")


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
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
    """Load chunk groups from S3 and return them inline."""
    groups_s3_key = event.get("groups_s3_key")
    groups_s3_bucket = event.get("groups_s3_bucket")
    batch_id = event.get("batch_id")
    poll_results = event.get("poll_results", [])

    if not groups_s3_key or not groups_s3_bucket:
        raise ValueError("groups_s3_key and groups_s3_bucket are required for load_groups_from_s3 operation")

    logger.info(
        "Loading chunk groups from S3",
        s3_key=groups_s3_key,
        bucket=groups_s3_bucket,
        batch_id=batch_id,
    )

    # Download groups from S3
    with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_file:
        tmp_file_path = tmp_file.name

    try:
        s3_client.download_file(groups_s3_bucket, groups_s3_key, tmp_file_path)

        with open(tmp_file_path, "r", encoding="utf-8") as f:
            groups = json.load(f)

        logger.info(
            "Loaded chunk groups from S3",
            group_count=len(groups),
            batch_id=batch_id,
        )

        return {
            "batch_id": batch_id,
            "groups": groups,
            "total_groups": len(groups),
            "poll_results": poll_results,
            "use_s3": False,  # Groups are now inline
            "groups_s3_key": None,  # Always include these fields for consistency
            "groups_s3_bucket": None,
        }
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_file_path)
        except Exception:
            pass


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
            "Creating chunk groups",
            chunk_count=len(chunk_results),
            group_size=group_size,
            batch_id=batch_id,
        )

        # Partition chunk_results into groups
        groups: List[List[Dict[str, Any]]] = []
        for i in range(0, len(chunk_results), group_size):
            group = chunk_results[i : i + group_size]
            groups.append(group)

        logger.info(
            "Created chunk groups",
            total_groups=len(groups),
            batch_id=batch_id,
        )

        # Check if response would exceed Step Functions payload limit
        response_payload = json.dumps({
            "batch_id": batch_id,
            "groups": groups,
            "total_groups": len(groups),
            "poll_results": poll_results,
        })
        payload_size = len(response_payload.encode("utf-8"))

        logger.info(
            "Response payload size",
            size_bytes=payload_size,
            size_kb=payload_size / 1024,
            max_size_kb=MAX_PAYLOAD_SIZE / 1024,
        )

        # If payload is too large, upload groups to S3 and return S3 keys
        if payload_size > MAX_PAYLOAD_SIZE:
            logger.info(
                "Response payload exceeds limit, uploading groups to S3",
                payload_size_kb=payload_size / 1024,
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
                    "Uploaded groups to S3",
                    s3_key=groups_s3_key,
                    bucket=bucket,
                    group_count=len(groups),
                )
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

            # Return S3 reference instead of full groups
            return {
                "batch_id": batch_id,
                "groups_s3_key": groups_s3_key,
                "groups_s3_bucket": bucket,
                "total_groups": len(groups),
                "poll_results": poll_results,
                "use_s3": True,
            }
        else:
            # Response is small enough, return groups directly
            logger.info("Response payload within limit, returning groups directly")
            return {
                "batch_id": batch_id,
                "groups": groups,
                "total_groups": len(groups),
                "poll_results": poll_results,
                "use_s3": False,
                "groups_s3_key": None,  # Always include these fields for consistency
                "groups_s3_bucket": None,
            }

    except ValueError as e:
        logger.error("Validation error", error=str(e))
        return {
            "statusCode": 400,
            "error": str(e),
            "message": "Invalid input parameters",
        }

    except Exception as e:
        logger.error("Unexpected error creating chunk groups", error=str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Failed to create chunk groups",
        }

