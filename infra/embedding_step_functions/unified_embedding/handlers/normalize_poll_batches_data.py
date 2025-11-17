"""Handler for normalizing poll batches data - S3-only approach.

This handler ALWAYS uploads poll_results from the PollBatches Map state to S3,
regardless of size. This ensures we never hit Step Functions' 256KB payload limit
and provides consistent behavior regardless of batch count.

Why S3-only?
- Each PollBatch result is ~408 bytes
- With 397 batches: ~158KB raw + JSON overhead = ~200-250KB (close to 256KB limit)
- Step Functions has a hard 256KB limit for state data
- By always using S3, we avoid any risk of exceeding the limit
"""

import json
import os
import tempfile
from typing import Any, Dict

import boto3

import utils.logging

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

# S3-ONLY APPROACH: We always upload to S3 to avoid Step Functions payload limits
# Step Functions has a 256KB limit, but with 397 batches at ~408 bytes each,
# plus JSON array overhead, we can easily exceed this limit.
# By always using S3, we ensure consistent behavior regardless of batch count.

s3_client = boto3.client("s3")


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Normalize poll batches data - handles S3 references from individual PollBatch Lambdas.

    Each PollBatch Lambda now uploads its result to S3 and returns only a small reference.
    This handler downloads all individual results, combines them, and uploads to a single file.

    Args:
        event: Lambda event containing:
            - poll_results: Array of poll results from PollBatches Map state
              Each result is either:
              - A full result object (legacy format)
              - An S3 reference: {"batch_id": "...", "result_s3_key": "...", "result_s3_bucket": "..."}
            - batch_id: Batch ID for tracking (required)

    Returns:
        Dictionary containing:
            - poll_results: Always None (always in S3)
            - poll_results_s3_key: S3 key where combined poll_results are stored
            - poll_results_s3_bucket: S3 bucket where combined poll_results are stored
            - batch_id: Passed through for tracking
    """
    poll_results = event.get("poll_results", [])
    batch_id = event.get("batch_id")

    if not batch_id:
        raise ValueError("batch_id is required")

    # Get S3 bucket from environment
    bucket = os.environ.get("CHROMADB_BUCKET")
    if not bucket:
        raise ValueError("CHROMADB_BUCKET environment variable not set")

    # Check if poll_results contains S3 references (new format from individual PollBatch Lambdas)
    # Each result should have: {"batch_id": "...", "result_s3_key": "...", "result_s3_bucket": "..."}
    if (
        isinstance(poll_results, list)
        and len(poll_results) > 0
        and isinstance(poll_results[0], dict)
        and "result_s3_key" in poll_results[0]
    ):
        # New format: Array of S3 references - download and combine them
        logger.info(
            "poll_results contains S3 references, downloading and combining",
            batch_id=batch_id,
            result_count=len(poll_results),
        )

        combined_results = []
        for result_ref in poll_results:
            result_bucket = result_ref.get("result_s3_bucket")
            result_key = result_ref.get("result_s3_key")

            if not result_bucket or not result_key:
                logger.warning(
                    "Skipping invalid S3 reference",
                    result_ref=result_ref,
                )
                continue

            # Download individual result from S3
            with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_file:
                tmp_file_path = tmp_file.name

            try:
                s3_client.download_file(result_bucket, result_key, tmp_file_path)
                with open(tmp_file_path, "r", encoding="utf-8") as f:
                    individual_result = json.load(f)
                combined_results.append(individual_result)
            except Exception as e:
                logger.error(
                    "Failed to download individual result from S3",
                    result_bucket=result_bucket,
                    result_key=result_key,
                    error=str(e),
                )
                # Continue with other results
            finally:
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

        logger.info(
            "Downloaded and combined individual results",
            batch_id=batch_id,
            combined_count=len(combined_results),
        )

        # Upload combined results to S3
        poll_results_s3_key = f"poll_results/{batch_id}/poll_results.json"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
            json.dump(combined_results, tmp_file, indent=2)
            tmp_file_path = tmp_file.name

        try:
            s3_client.upload_file(
                tmp_file_path,
                bucket,
                poll_results_s3_key,
            )
            logger.info(
                "Uploaded combined poll_results to S3",
                s3_key=poll_results_s3_key,
                bucket=bucket,
                poll_results_count=len(combined_results),
            )
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

        return {
            "batch_id": batch_id,
            "poll_results": None,  # Set to None to indicate it's in S3
            "poll_results_s3_key": poll_results_s3_key,
            "poll_results_s3_bucket": bucket,
        }

    # Legacy format: Array of full results - upload directly to S3
    logger.info(
        "poll_results is in legacy format (full results), uploading to S3",
        batch_id=batch_id,
        poll_results_count=len(poll_results) if isinstance(poll_results, list) else 0,
    )

    poll_results_s3_key = f"poll_results/{batch_id}/poll_results.json"

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
            "Uploaded poll_results to S3",
            s3_key=poll_results_s3_key,
            bucket=bucket,
            poll_results_count=len(poll_results) if isinstance(poll_results, list) else 0,
        )
    finally:
        try:
            os.unlink(tmp_file_path)
        except Exception:
            pass

    # Always return S3 reference (S3-only approach)
    return {
        "batch_id": batch_id,
        "poll_results": None,  # Set to None to indicate it's in S3
        "poll_results_s3_key": poll_results_s3_key,
        "poll_results_s3_bucket": bucket,
    }

