"""
Containerized Lambda handler for polling OpenAI batch results and creating ChromaDB deltas.

This handler uses the same helpers as the non-containerized version but runs in a
container to have access to the ChromaDB package for creating native delta files.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger

# Import the same helpers used in the non-containerized version
from receipt_label.embedding.word.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    write_embedding_results_to_dynamo,
    save_word_embeddings_as_delta,
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


def poll_handler(event, context):
    """
    Poll a word embedding batch and save results as deltas to S3.
    
    This containerized handler uses the same logic as the non-containerized version
    but runs in a container environment with ChromaDB available.
    
    When called from Step Function, set skip_sqs_notification=True to 
    prevent individual compaction triggers.
    """
    logger.info("Starting containerized poll_word_embedding_batch_handler")
    logger.info(f"Event: {json.dumps(event)}")

    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]
    
    # Check if we should skip SQS notification (when called from step function)
    skip_sqs = event.get("skip_sqs_notification", False)

    # Check the batch status
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info(f"Batch {openai_batch_id} status: {batch_status}")

    if batch_status == "completed":
        # Download the batch results
        results = download_openai_batch_result(openai_batch_id)
        logger.info(f"Downloaded {len(results)} embedding results")

        # Get receipt details
        descriptions = get_receipt_descriptions(results)
        logger.info(f"Retrieved details for {len(descriptions)} receipts")

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
            delta_result = save_word_embeddings_as_delta(
                results, descriptions, batch_id
            )
            logger.info(
                f"Saved delta {delta_result['delta_id']} with "
                f"{delta_result['embedding_count']} embeddings"
            )
        finally:
            # Restore original queue URL
            if skip_sqs and original_queue_url:
                os.environ["COMPACTION_QUEUE_URL"] = original_queue_url

        # Write to DynamoDB for tracking
        written = write_embedding_results_to_dynamo(
            results, descriptions, batch_id
        )
        logger.info(f"Wrote {written} embedding results to DynamoDB")

        # Mark batch complete
        mark_batch_complete(batch_id)
        logger.info(f"Marked batch {batch_id} as complete")

        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "results_count": len(results),
            "delta_id": delta_result["delta_id"],
            "delta_key": delta_result["delta_key"],
            "embedding_count": delta_result["embedding_count"],
            "storage": "s3_delta",
        }

    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": batch_status,
    }