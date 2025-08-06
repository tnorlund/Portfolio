"""
Containerized Lambda handler for polling OpenAI line embedding batch results.

This handler creates ChromaDB deltas and uses the same helpers as the
non-containerized version but runs in a container to have access to the
ChromaDB package for creating native delta files.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger

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


def poll_handler(event, _context):
    """
    Poll a line embedding batch and save results as deltas to S3.

    This containerized handler uses the same logic as the
    non-containerized version but runs in a container environment with
    ChromaDB available.

    When called from Step Function, set skip_sqs_notification=True to
    prevent individual compaction triggers.
    """
    logger.info("Starting containerized poll_line_embedding_batch_handler")
    logger.info("Event: %s", json.dumps(event))

    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]

    # Check if we should skip SQS notification (when called from step function)
    skip_sqs = event.get("skip_sqs_notification", False)

    # Check the batch status
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info("Batch %s status: %s", openai_batch_id, batch_status)

    if batch_status == "completed":
        # Download the batch results
        results = download_openai_batch_result(openai_batch_id)
        logger.info("Downloaded %d line embedding results", len(results))

        # Get receipt details
        descriptions = get_receipt_descriptions(results)
        logger.info("Retrieved details for %d receipts", len(descriptions))

        # Override SQS queue URL if we're skipping notifications
        original_queue_url = None
        if skip_sqs:
            original_queue_url = os.environ.get("COMPACTION_QUEUE_URL")
            # Temporarily remove it to prevent SQS notification
            if "COMPACTION_QUEUE_URL" in os.environ:
                del os.environ["COMPACTION_QUEUE_URL"]
            logger.info("Skipping SQS notification for this delta")

        try:
            # Save delta without SQS notification
            delta_result = save_line_embeddings_as_delta(
                results, descriptions, batch_id
            )
            logger.info(
                "Saved delta %s with %d embeddings",
                delta_result["delta_id"],
                delta_result["embedding_count"],
            )
        finally:
            # Restore original queue URL
            if skip_sqs and original_queue_url:
                os.environ["COMPACTION_QUEUE_URL"] = original_queue_url

        # Write to DynamoDB for tracking
        write_line_embedding_results_to_dynamo(results, descriptions, batch_id)
        logger.info(
            "Wrote %d line embedding results to DynamoDB", len(results)
        )

        # Update line embedding status to SUCCESS
        update_line_embedding_status_to_success(results, descriptions)
        logger.info("Updated line embedding status to SUCCESS")

        # Mark batch complete
        mark_batch_complete(batch_id)
        logger.info("Marked batch %s as complete", batch_id)

        # Return delta information for CompactAllDeltas step
        # Include batch info for tracking/debugging
        return {
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "results_count": len(results),
            "delta_id": delta_result["delta_id"],
            "delta_key": delta_result["delta_key"],
            "embedding_count": delta_result["embedding_count"],
            "storage": "s3_delta",
        }

    # Batch not completed yet - return minimal info
    return {
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": batch_status,
        # No delta info since batch isn't complete
    }
