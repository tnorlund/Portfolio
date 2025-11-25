"""
Modular batch status handler for OpenAI Batch API operations.

This module provides centralized handling for all OpenAI batch statuses,
including error recovery, partial result processing, and DynamoDB updates.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from receipt_dynamo.constants import BatchStatus, EmbeddingStatus
from receipt_dynamo.entities import BatchSummary
from receipt_dynamo.data.dynamo_client import DynamoClient

from openai import OpenAI

logger = logging.getLogger(__name__)


def map_openai_to_dynamo_status(openai_status: str) -> BatchStatus:
    """
    Map OpenAI batch status to DynamoDB BatchStatus enum.

    Args:
        openai_status: Status string from OpenAI API

    Returns:
        Corresponding BatchStatus enum value

    Raises:
        ValueError: If status is unknown
    """
    mapping = {
        "validating": BatchStatus.VALIDATING,
        "in_progress": BatchStatus.IN_PROGRESS,
        "finalizing": BatchStatus.FINALIZING,
        "completed": BatchStatus.COMPLETED,
        "failed": BatchStatus.FAILED,
        "expired": BatchStatus.EXPIRED,
        "canceling": BatchStatus.CANCELING,
        "cancelled": BatchStatus.CANCELLED,
    }

    if openai_status not in mapping:
        raise ValueError(f"Unknown OpenAI batch status: {openai_status}")

    return mapping[openai_status]


def process_error_file(
    openai_batch_id: str, openai_client: OpenAI
) -> Dict[str, Any]:
    """
    Download and process error file for failed or expired batches.

    Args:
        openai_batch_id: OpenAI batch identifier
        openai_client: OpenAI client instance

    Returns:
        Dictionary containing:
        - error_count: Number of errors
        - error_types: Distribution of error types
        - error_details: List of detailed error records
        - sample_errors: First 5 errors for logging
    """
    batch = openai_client.batches.retrieve(openai_batch_id)

    if not batch.error_file_id:
        logger.warning(
            "No error file available for batch %s with status %s",
            openai_batch_id,
            batch.status,
        )
        return {
            "error_count": 0,
            "error_types": {},
            "error_details": [],
            "sample_errors": [],
        }

    # Download error file
    response = openai_client.files.content(batch.error_file_id)

    # Parse error content
    if hasattr(response, "read"):
        lines = response.read().decode("utf-8").splitlines()
    elif isinstance(response, bytes):
        lines = response.decode("utf-8").splitlines()
    elif isinstance(response, str):
        lines = response.splitlines()
    else:
        raise ValueError("Unexpected OpenAI error file content type")

    error_details = []
    error_types: Dict[str, int] = {}

    for line in lines:
        if not line.strip():
            continue

        try:
            error_record = json.loads(line)
            custom_id = error_record.get("custom_id", "unknown")
            error_info = error_record.get("error", {})
            error_type = error_info.get("type", "unknown_error")
            error_message = error_info.get("message", "No message provided")

            error_details.append(
                {
                    "custom_id": custom_id,
                    "error_type": error_type,
                    "error_message": error_message,
                    "raw_error": error_info,
                }
            )

            # Track error type distribution
            error_types[error_type] = error_types.get(error_type, 0) + 1

        except json.JSONDecodeError as e:
            logger.error("Failed to parse error line: %s - %s", line, e)
            continue

    return {
        "error_count": len(error_details),
        "error_types": error_types,
        "error_details": error_details,
        "sample_errors": error_details[:5],  # First 5 for logging
    }


def process_partial_results(
    openai_batch_id: str, openai_client: OpenAI
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Handle partial results from expired batches.

    Args:
        openai_batch_id: OpenAI batch identifier
        openai_client: OpenAI client instance

    Returns:
        Tuple of:
        - List of successful results
        - List of failed item IDs that need retry
    """
    batch = openai_client.batches.retrieve(openai_batch_id)

    successful_results = []
    failed_ids = []

    # Process output file if available (partial successes)
    if batch.output_file_id:
        response = openai_client.files.content(batch.output_file_id)

        if hasattr(response, "read"):
            lines = response.read().decode("utf-8").splitlines()
        elif isinstance(response, bytes):
            lines = response.decode("utf-8").splitlines()
        elif isinstance(response, str):
            lines = response.splitlines()
        else:
            raise ValueError("Unexpected OpenAI output file content type")

        for line in lines:
            if not line.strip():
                continue

            try:
                record = json.loads(line)
                # Extract embedding data
                embedding = (
                    record.get("response", {})
                    .get("body", {})
                    .get("data", [{}])[0]
                    .get("embedding")
                )

                if embedding:
                    successful_results.append(
                        {
                            "custom_id": record.get("custom_id"),
                            "embedding": embedding,
                        }
                    )
                else:
                    failed_ids.append(record.get("custom_id", "unknown"))

            except (json.JSONDecodeError, KeyError) as e:
                logger.error("Failed to parse result line: %s", e)
                continue

    # Process error file to identify failures
    if batch.error_file_id:
        error_info = process_error_file(openai_batch_id, openai_client)
        for error_detail in error_info["error_details"]:
            failed_ids.append(error_detail["custom_id"])

    logger.info(
        "Processed partial results for batch %s: %d successful, %d failed",
        openai_batch_id,
        len(successful_results),
        len(failed_ids),
    )

    return successful_results, failed_ids


def handle_completed_status(
    batch_id: str, openai_batch_id: str, dynamo_client: DynamoClient = None, openai_client: OpenAI = None
) -> Dict[str, Any]:
    """
    Handle completed batch status.

    Args:
        batch_id: Internal batch identifier (unused but kept for consistency)
        openai_batch_id: OpenAI batch identifier
        dynamo_client: DynamoDB client (unused, kept for consistency)
        openai_client: OpenAI client (unused, kept for consistency)

    Returns:
        Status dictionary with action taken
    """
    # Arguments are unused but kept for consistent interface
    # pylint: disable=unused-argument
    logger.info("Batch %s completed successfully", openai_batch_id)

    return {
        "action": "process_results",
        "status": "completed",
        "next_step": "download_and_store",
        "should_continue_processing": True,
    }


def handle_failed_status(
    batch_id: str, openai_batch_id: str, dynamo_client: DynamoClient, openai_client: OpenAI
) -> Dict[str, Any]:
    """
    Handle failed batch status.

    Args:
        batch_id: Internal batch identifier
        openai_batch_id: OpenAI batch identifier
        dynamo_client: DynamoDB client instance
        openai_client: OpenAI client instance

    Returns:
        Status dictionary with error details
    """
    logger.error("Batch %s failed", openai_batch_id)

    # Process error file
    error_info = process_error_file(openai_batch_id, openai_client)

    # Update batch summary in DynamoDB
    batch_summary = dynamo_client.get_batch_summary(batch_id)
    batch_summary.status = BatchStatus.FAILED
    dynamo_client.update_batch_summary(batch_summary)

    # Mark all failed items for retry based on batch type
    marked_count = 0
    if error_info["error_details"]:
        failed_ids = [
            detail["custom_id"] for detail in error_info["error_details"]
        ]
        # Determine entity type from batch_type
        entity_type = (
            "line" if batch_summary.batch_type == "LINE_EMBEDDING" else "word"
        )
        marked_count = mark_items_for_retry(
            failed_ids, entity_type, dynamo_client
        )
        logger.info(
            "Marked %d failed items from failed batch %s for retry",
            marked_count,
            openai_batch_id,
        )

    # Log sample errors for debugging
    if error_info["sample_errors"]:
        logger.error(
            "Sample errors from batch %s: %s",
            openai_batch_id,
            json.dumps(error_info["sample_errors"], indent=2),
        )

    return {
        "action": "handle_failure",
        "status": "failed",
        "error_count": error_info["error_count"],
        "error_types": error_info["error_types"],
        "sample_errors": error_info["sample_errors"],
        "marked_for_retry": marked_count,
        "next_step": "create_retry_batch",
        "should_continue_processing": False,
    }


def handle_expired_status(
    batch_id: str, openai_batch_id: str, dynamo_client: DynamoClient, openai_client: OpenAI
) -> Dict[str, Any]:
    """
    Handle expired batch status (exceeded 24h SLA).

    Args:
        batch_id: Internal batch identifier
        openai_batch_id: OpenAI batch identifier
        dynamo_client: DynamoDB client instance
        openai_client: OpenAI client instance

    Returns:
        Status dictionary with partial results info
    """
    logger.warning("Batch %s expired (exceeded 24h SLA)", openai_batch_id)

    # Process any partial results
    successful_results, failed_ids = process_partial_results(
        openai_batch_id, openai_client
    )

    # Update batch summary
    batch_summary = dynamo_client.get_batch_summary(batch_id)
    batch_summary.status = BatchStatus.EXPIRED
    dynamo_client.update_batch_summary(batch_summary)

    # Mark failed items for retry based on batch type
    marked_count = 0
    if failed_ids:
        # Determine entity type from batch_type
        entity_type = (
            "line" if batch_summary.batch_type == "LINE_EMBEDDING" else "word"
        )
        marked_count = mark_items_for_retry(
            failed_ids, entity_type, dynamo_client
        )
        logger.info(
            "Marked %d failed items from expired batch %s for retry",
            marked_count,
            openai_batch_id,
        )

    # TODO: Consider marking successful items as COMPLETED
    # This would require implementing a similar function to mark_items_for_retry
    # but setting status to COMPLETED instead of FAILED

    return {
        "action": "process_partial",
        "status": "expired",
        "successful_count": len(successful_results),
        "failed_count": len(failed_ids),
        "marked_for_retry": marked_count,
        "partial_results": successful_results,
        "failed_ids": failed_ids,
        "next_step": "process_partial_and_retry_failed",
        "should_continue_processing": bool(successful_results),
    }


def handle_in_progress_status(
    batch_id: str,
    openai_batch_id: str,
    status: str,
    dynamo_client: DynamoClient,
) -> Dict[str, Any]:
    """
    Handle in-progress statuses (validating, in_progress, finalizing).

    Args:
        batch_id: Internal batch identifier
        openai_batch_id: OpenAI batch identifier
        status: Current status string
        dynamo_client: DynamoDB client instance
        openai_client: OpenAI client instance

    Returns:
        Status dictionary indicating to wait
    """
    logger.info("Batch %s is still processing: %s", openai_batch_id, status)

    # Update batch summary with current status
    batch_summary = dynamo_client.get_batch_summary(batch_id)
    batch_summary.status = map_openai_to_dynamo_status(status)
    dynamo_client.update_batch_summary(batch_summary)

    # Calculate time since submission
    submitted_at = batch_summary.submitted_at
    if isinstance(submitted_at, str):
        submitted_at = datetime.fromisoformat(submitted_at)

    hours_elapsed = (
        datetime.now(timezone.utc) - submitted_at
    ).total_seconds() / 3600

    # Warn if approaching 24h limit
    if hours_elapsed > 20:
        logger.warning(
            "Batch %s processing %.1f hours - approaching 24h limit",
            openai_batch_id,
            hours_elapsed,
        )

    return {
        "action": "wait",
        "status": status,
        "hours_elapsed": hours_elapsed,
        "next_step": "poll_again_later",
        "should_continue_processing": False,
    }


def handle_cancelled_status(
    batch_id: str,
    openai_batch_id: str,
    status: str,
    dynamo_client: DynamoClient,
) -> Dict[str, Any]:
    """
    Handle cancelled/canceling statuses.

    Args:
        batch_id: Internal batch identifier
        openai_batch_id: OpenAI batch identifier
        status: Current status (canceling or cancelled)
        dynamo_client: DynamoDB client instance
        openai_client: OpenAI client instance

    Returns:
        Status dictionary for cancelled batch
    """
    logger.info("Batch %s was cancelled: %s", openai_batch_id, status)

    # Update batch summary
    batch_summary = dynamo_client.get_batch_summary(batch_id)
    batch_summary.status = map_openai_to_dynamo_status(status)
    dynamo_client.update_batch_summary(batch_summary)

    return {
        "action": "handle_cancellation",
        "status": status,
        "next_step": "cleanup_or_retry",
        "should_continue_processing": False,
    }


def handle_batch_status(
    batch_id: str,
    openai_batch_id: str,
    status: str,
    dynamo_client: DynamoClient,
    openai_client: OpenAI,
) -> Dict[str, Any]:
    """
    Central handler for all batch statuses.

    This function routes to the appropriate handler based on status
    and returns a consistent response structure.

    Args:
        batch_id: Internal batch identifier
        openai_batch_id: OpenAI batch identifier
        status: Current batch status from OpenAI
        dynamo_client: DynamoDB client instance
        openai_client: OpenAI client instance

    Returns:
        Dictionary containing:
        - action: What action was taken
        - status: Current status
        - next_step: Recommended next action
        - should_continue_processing: Whether to proceed with result processing
        - Additional status-specific fields
    """
    # Normalize status to lowercase
    status = status.lower()

    if status == "completed":
        return handle_completed_status(
            batch_id, openai_batch_id, dynamo_client, openai_client
        )
    if status == "failed":
        return handle_failed_status(batch_id, openai_batch_id, dynamo_client, openai_client)
    if status == "expired":
        return handle_expired_status(batch_id, openai_batch_id, dynamo_client, openai_client)
    if status in ["validating", "in_progress", "finalizing"]:
        return handle_in_progress_status(
            batch_id, openai_batch_id, status, dynamo_client
        )
    if status in ["canceling", "cancelled"]:
        return handle_cancelled_status(
            batch_id, openai_batch_id, status, dynamo_client
        )

    logger.error("Unknown batch status: %s", status)
    raise ValueError(f"Unknown batch status: {status}")


def mark_items_for_retry(
    failed_ids: List[str], entity_type: str, dynamo_client: DynamoClient
) -> int:
    """
    Mark failed items for retry in DynamoDB.

    Args:
        failed_ids: List of custom IDs that failed
        entity_type: "word" or "line" to determine entity type
        dynamo_client: DynamoDB client instance
        openai_client: OpenAI client instance

    Returns:
        Number of items marked for retry
    """
    marked_count = 0

    for custom_id in failed_ids:
        try:
            # Parse custom_id to extract entity details
            parts = custom_id.split("#")
            image_id = parts[1]
            receipt_id = int(parts[3])

            if entity_type == "line":
                line_id = int(parts[5])
                # Get and update line
                lines = dynamo_client.get_lines_from_receipt(
                    image_id, receipt_id
                )
                for line in lines:
                    if line.line_id == line_id:
                        line.embedding_status = EmbeddingStatus.FAILED
                        dynamo_client.update_lines([line])
                        marked_count += 1
                        break

            elif entity_type == "word":
                word_id = int(parts[7])
                # Get and update word
                words = dynamo_client.get_words_from_receipt(
                    image_id, receipt_id
                )
                for word in words:
                    if word.word_id == word_id:
                        word.embedding_status = EmbeddingStatus.FAILED
                        dynamo_client.update_words([word])
                        marked_count += 1
                        break

        except (IndexError, ValueError) as e:
            logger.error(
                "Failed to parse custom_id %s for retry marking: %s",
                custom_id,
                e,
            )
            continue

    logger.info("Marked %d items for retry", marked_count)
    return marked_count


def should_retry_batch(
    batch_summary: BatchSummary,
    max_retries: int = 3,  # pylint: disable=unused-argument
) -> bool:
    """
    Determine if a failed batch should be retried.

    Args:
        batch_summary: The batch summary entity
        max_retries: Maximum number of retry attempts

    Returns:
        True if batch should be retried
    """
    # Check retry count (would need to add this field to BatchSummary)
    # For now, return True for first failure
    return batch_summary.status in [BatchStatus.FAILED, BatchStatus.EXPIRED]
