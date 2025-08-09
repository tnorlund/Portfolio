"""
Containerized Lambda handler for polling OpenAI line embedding batch results.

This handler creates ChromaDB deltas and uses the same helpers as the
non-containerized version but runs in a container to have access to the
ChromaDB package for creating native delta files.

Enhanced to handle all OpenAI batch statuses including failed, expired,
and in-progress.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict

# Import the same helpers used in the non-containerized version
from receipt_label.embedding.line.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    update_line_embedding_status_to_success,
    write_line_embedding_results_to_dynamo,
    save_line_embeddings_as_delta,
)

# Import modular status handler
from receipt_label.embedding.common import (
    handle_batch_status,
    mark_items_for_retry,
)
from receipt_label.utils import get_client_manager

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def _handle_completed_batch(
    batch_id: str,
    openai_batch_id: str, 
    batch_status: str,
    status_result: Dict[str, Any],
    skip_sqs: bool
) -> Dict[str, Any]:
    """Handle completed batch processing."""
    # Download the batch results
    results = download_openai_batch_result(openai_batch_id)
    logger.info("Downloaded %d line embedding results", len(results))

    # Get receipt details
    descriptions = get_receipt_descriptions(results)
    logger.info("Retrieved details for %d receipts", len(descriptions))

    # Save delta with or without SQS notification based on skip_sqs flag
    if skip_sqs:
        logger.info("Skipping SQS notification for this delta")
    
    delta_result = save_line_embeddings_as_delta(
        results, descriptions, batch_id, skip_sqs_notification=skip_sqs
    )
    logger.info(
        "Saved delta %s with %d embeddings",
        delta_result["delta_id"],
        delta_result["embedding_count"],
    )

    # Write to DynamoDB for tracking
    write_line_embedding_results_to_dynamo(results, descriptions, batch_id)
    logger.info("Wrote %d line embedding results to DynamoDB", len(results))

    # Update line embedding status to SUCCESS
    update_line_embedding_status_to_success(results, descriptions)
    logger.info("Updated line embedding status to SUCCESS")

    # Mark batch complete
    mark_batch_complete(batch_id)
    logger.info("Marked batch %s as complete", batch_id)

    # Return delta information for CompactAllDeltas step
    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": batch_status,
        "action": status_result["action"],
        "results_count": len(results),
        "delta_id": delta_result["delta_id"],
        "delta_key": delta_result["delta_key"],
        "embedding_count": delta_result["embedding_count"],
        "storage": "s3_delta",
    }


def poll_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=unused-argument
    """
    Poll a line embedding batch and save results as deltas to S3.

    This enhanced handler processes all OpenAI batch statuses including
    failed, expired, and in-progress states.

    When called from Step Function, set skip_sqs_notification=True to
    prevent individual compaction triggers.

    Args:
        event: Lambda event containing batch_id and openai_batch_id
        context: Lambda context (unused)

    Returns:
        Dictionary with status, action taken, and next steps
    """
    logger.info("Starting containerized poll_line_embedding_batch_handler")
    logger.info("Event: %s", json.dumps(event))

    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]

    # Check if we should skip SQS notification (when called from step function)
    skip_sqs = event.get("skip_sqs_notification", False)

    # Get client manager
    client_manager = get_client_manager()

    # Check the batch status
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info("Batch %s status: %s", openai_batch_id, batch_status)

    # Use modular status handler
    status_result = handle_batch_status(
        batch_id=batch_id,
        openai_batch_id=openai_batch_id,
        status=batch_status,
        client_manager=client_manager,
    )

    # Process based on the action determined by status handler
    if (
        status_result["action"] == "process_results"
        and batch_status == "completed"
    ):
        return _handle_completed_batch(
            batch_id, openai_batch_id, batch_status, status_result, skip_sqs
        )

    if (
        status_result["action"] == "process_partial"
        and batch_status == "expired"
    ):
        # Handle expired batch with partial results
        partial_results = status_result.get("partial_results", [])
        failed_ids = status_result.get("failed_ids", [])

        if partial_results:
            logger.info("Processing %d partial results", len(partial_results))

            # Get receipt details for successful results
            descriptions = get_receipt_descriptions(partial_results)

            # Save partial results
            save_line_embeddings_as_delta(
                partial_results, descriptions, batch_id
            )

            # Write partial results to DynamoDB
            write_line_embedding_results_to_dynamo(
                partial_results, descriptions, batch_id
            )

            # Update status for successful lines
            update_line_embedding_status_to_success(
                partial_results, descriptions
            )
            logger.info("Processed partial line embedding results")

        # Mark failed items for retry
        if failed_ids:
            marked = mark_items_for_retry(failed_ids, "line", client_manager)
            logger.info("Marked %d lines for retry", marked)

        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": status_result["action"],
            "successful_count": status_result.get("successful_count", 0),
            "failed_count": status_result.get("failed_count", 0),
            "next_step": status_result.get("next_step"),
        }

    if status_result["action"] == "handle_failure":
        # Handle completely failed batch
        error_info = status_result
        logger.error(
            "Batch %s failed with %d errors",
            openai_batch_id,
            error_info.get("error_count", 0),
        )

        return {
            "statusCode": 200,  # Still 200 for Step Functions
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": status_result["action"],
            "error_count": error_info.get("error_count", 0),
            "error_types": error_info.get("error_types", {}),
            "sample_errors": error_info.get("sample_errors", []),
            "next_step": error_info.get("next_step"),
        }

    if status_result["action"] in ["wait", "handle_cancellation"]:
        # Batch is still processing or was cancelled
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": status_result["action"],
            "hours_elapsed": status_result.get("hours_elapsed"),
            "next_step": status_result.get("next_step"),
        }

    # Unknown action
    logger.error(
        "Unknown action from status handler: %s", status_result["action"]
    )
    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": batch_status,
        "action": "unknown",
        "error": f"Unknown action: {status_result['action']}",
    }
