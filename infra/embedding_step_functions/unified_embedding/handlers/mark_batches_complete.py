"""Handler for marking batch summaries as completed after successful compaction.

This handler marks all batches from poll_results as COMPLETED only after
successful compaction (data written to S3 and EFS). This ensures batches
are only marked complete when the entire workflow succeeds.
"""

import json
import os
import tempfile
from typing import Any, Dict, List

import boto3
import utils.logging

from receipt_dynamo.data.dynamo_client import DynamoClient

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

# Initialize DynamoDB client
dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

# Initialize S3 client
s3_client = boto3.client("s3")


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Mark batch summaries as COMPLETED after successful compaction.

    Args:
        event: Lambda event containing:
            - poll_results: Array of poll results, each containing batch_id
            - poll_results_s3_key: (optional) S3 key where poll_results is stored
            - poll_results_s3_bucket: (optional) S3 bucket where poll_results is stored

    Returns:
        Dictionary containing:
            - batches_marked: Number of batches marked as complete
            - batch_ids: List of batch IDs that were marked complete
    """
    logger.info("Starting mark_batches_complete handler")

    try:
        poll_results = event.get("poll_results", [])

        # Load poll_results from S3 if it's stored there
        # Check multiple possible locations (from different step function paths)
        # Priority: primary > fallback > poll_data (source of truth from NormalizePollBatchesData)
        poll_results_s3_key_primary = event.get(
            "poll_results_s3_key"
        )  # From final_merge_result or root level
        poll_results_s3_key_fallback = event.get(
            "poll_results_s3_key_fallback"
        )  # From root level or intermediate steps
        poll_results_s3_key_poll_data = event.get(
            "poll_results_s3_key_poll_data"
        )  # From poll_results_data (guaranteed to exist)
        poll_results_s3_key_chunked = event.get(
            "poll_results_s3_key_chunked"
        )  # From chunked_data

        poll_results_s3_key = (
            poll_results_s3_key_primary
            or poll_results_s3_key_fallback
            or poll_results_s3_key_poll_data
            or poll_results_s3_key_chunked
        )

        poll_results_s3_bucket_primary = event.get(
            "poll_results_s3_bucket"
        )  # From final_merge_result or root level
        poll_results_s3_bucket_fallback = event.get(
            "poll_results_s3_bucket_fallback"
        )  # From root level or intermediate steps
        poll_results_s3_bucket_poll_data = event.get(
            "poll_results_s3_bucket_poll_data"
        )  # From poll_results_data (guaranteed to exist)
        poll_results_s3_bucket_chunked = event.get(
            "poll_results_s3_bucket_chunked"
        )  # From chunked_data

        poll_results_s3_bucket = (
            poll_results_s3_bucket_primary
            or poll_results_s3_bucket_fallback
            or poll_results_s3_bucket_poll_data
            or poll_results_s3_bucket_chunked
        )

        # Log which path was used for debugging
        if poll_results_s3_key:
            source = (
                "primary"
                if poll_results_s3_key_primary
                else (
                    "fallback"
                    if poll_results_s3_key_fallback
                    else (
                        "poll_data" if poll_results_s3_key_poll_data else "chunked_data"
                    )
                )
            )
            logger.info(
                "Found poll_results_s3_key from %s: %s",
                source,
                poll_results_s3_key,
            )
        else:
            logger.warning(
                "No poll_results_s3_key found in any location",
                available_keys=list(event.keys()),
            )

        if (
            (not poll_results or poll_results is None)
            and poll_results_s3_key
            and poll_results_s3_bucket
        ):
            logger.info(
                "Loading poll_results from S3: %s/%s",
                poll_results_s3_bucket,
                poll_results_s3_key,
            )
            with tempfile.NamedTemporaryFile(
                mode="r", suffix=".json", delete=False
            ) as tmp_file:
                tmp_file_path = tmp_file.name

            try:
                s3_client.download_file(
                    poll_results_s3_bucket, poll_results_s3_key, tmp_file_path
                )
                with open(tmp_file_path, "r", encoding="utf-8") as f:
                    poll_results = json.load(f)
                logger.info(
                    "Loaded poll_results from S3 (%d items)",
                    len(poll_results),
                )
            except Exception as e:
                logger.error(
                    "Failed to load poll_results from S3",
                    bucket=poll_results_s3_bucket,
                    key=poll_results_s3_key,
                    error=str(e),
                )
                # poll_results will remain empty, handler will return early below
            finally:
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

        if not poll_results:
            logger.info("No poll results to mark as complete")
            return {
                "batches_marked": 0,
                "batch_ids": [],
            }

        # Extract unique batch_ids from poll_results
        # CRITICAL: Only mark batches as COMPLETED if they actually completed
        # Filter out batches that are still processing (action: "wait") or failed
        batch_ids = []
        seen_batch_ids = set()  # O(1) membership checks
        skipped_batches = []
        for result in poll_results:
            if isinstance(result, dict) and "batch_id" in result:
                batch_id = result["batch_id"]
                # Safe normalization: handle None by defaulting to empty string
                batch_status = (result.get("batch_status") or "").lower()
                action = result.get("action", "")

                # Only mark batches as COMPLETED if:
                # 1. batch_status is "completed" AND
                # 2. action is "process_results" (not "wait", "handle_failure", etc.)
                if (
                    batch_id
                    and batch_status == "completed"
                    and action == "process_results"
                ):
                    if batch_id not in seen_batch_ids:
                        seen_batch_ids.add(batch_id)
                        batch_ids.append(batch_id)
                else:
                    # Track skipped batches for logging
                    if batch_id:
                        skipped_batches.append(
                            {
                                "batch_id": batch_id,
                                "batch_status": batch_status,
                                "action": action,
                            }
                        )

        if skipped_batches:
            logger.info(
                "Skipping batches that are not completed",
                skipped_count=len(skipped_batches),
                sample_skipped=skipped_batches[:5],  # Log first 5
            )

        if not batch_ids:
            if skipped_batches:
                logger.info(
                    "No completed batches to mark - all batches are still processing or failed",
                    total_batches=len(skipped_batches),
                )
            else:
                logger.warning("No batch_ids found in poll_results")
            return {
                "batches_marked": 0,
                "batch_ids": [],
                "skipped_batches": (len(skipped_batches) if skipped_batches else 0),
            }

        logger.info(
            "Marking batches as complete",
            batch_count=len(batch_ids),
            batch_ids=batch_ids[:5],  # Log first 5
        )

        # Fetch all batch summaries at once and update in batches (more efficient)
        marked_count = 0
        errors = []

        try:
            # Get all batch summaries in one call
            batch_summaries = dynamo_client.get_batch_summaries_by_batch_ids(batch_ids)

            # Update status for all summaries
            for batch_summary in batch_summaries:
                batch_summary.status = "COMPLETED"

            # Update all summaries in batches (update_batch_summaries uses transactions)
            # Process in chunks of 25 (DynamoDB transaction limit)
            chunk_size = 25
            for i in range(0, len(batch_summaries), chunk_size):
                chunk = batch_summaries[i : i + chunk_size]
                try:
                    dynamo_client.update_batch_summaries(chunk)
                    marked_count += len(chunk)
                    logger.info(
                        "Marked batches as complete",
                        chunk_size=len(chunk),
                        chunk_number=i // chunk_size + 1,
                    )
                except Exception as e:
                    # If batch update fails, try individual updates
                    logger.warning(
                        "Batch update failed, trying individual updates",
                        error=str(e)[:200],
                    )
                    for batch_summary in chunk:
                        try:
                            dynamo_client.update_batch_summary(batch_summary)
                            marked_count += 1
                            logger.debug(
                                "Marked batch as complete",
                                batch_id=batch_summary.batch_id,
                            )
                        except Exception as e2:
                            error_msg = f"Failed to mark batch {batch_summary.batch_id} as complete: {str(e2)[:100]}"
                            errors.append(error_msg)
                            logger.error(
                                "Error marking batch as complete",
                                batch_id=batch_summary.batch_id,
                                error=str(e2),
                            )

            # Check for any batch_ids that weren't found
            found_batch_ids = {bs.batch_id for bs in batch_summaries}
            for batch_id in batch_ids:
                if batch_id not in found_batch_ids:
                    error_msg = f"Batch {batch_id} not found in DynamoDB"
                    errors.append(error_msg)
                    logger.warning("Batch not found", batch_id=batch_id)

        except AttributeError as e:
            # Fallback: if methods don't exist, try individual calls
            logger.warning(
                "Batch methods not available, falling back to individual calls",
                error=str(e),
            )
            for batch_id in batch_ids:
                try:
                    # Try get_batch_summary first
                    batch_summary = dynamo_client.get_batch_summary(batch_id)
                    batch_summary.status = "COMPLETED"
                    dynamo_client.update_batch_summary(batch_summary)
                    marked_count += 1
                    logger.debug("Marked batch as complete", batch_id=batch_id)
                except Exception as e2:
                    error_msg = (
                        f"Failed to mark batch {batch_id} as complete: {str(e2)[:100]}"
                    )
                    errors.append(error_msg)
                    logger.error(
                        "Error marking batch as complete",
                        batch_id=batch_id,
                        error=str(e2),
                    )

        if errors:
            logger.warning(
                "Some batches failed to mark as complete",
                marked_count=marked_count,
                error_count=len(errors),
                total_batches=len(batch_ids),
            )
        else:
            logger.info(
                "Successfully marked all batches as complete",
                marked_count=marked_count,
            )

        return {
            "batches_marked": marked_count,
            "batch_ids": batch_ids,
            "errors": errors if errors else None,
        }

    except Exception as e:
        logger.error("Unexpected error marking batches as complete", error=str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Failed to mark batches as complete",
        }
