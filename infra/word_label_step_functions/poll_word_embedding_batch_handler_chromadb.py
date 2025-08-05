"""
Delta-based polling handler for word embeddings using ChromaDB architecture.

This replaces the direct Pinecone integration with a delta pattern that
allows Lambda functions to work without containerization.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger

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
    
    This handler:
    1. Checks OpenAI batch status
    2. Downloads completed results
    3. Saves embeddings as delta files to S3
    4. Optionally sends SQS notification for compaction
    5. Updates DynamoDB tracking
    
    When called from Step Function, set skip_sqs_notification=True to 
    prevent individual compaction triggers.
    """
    logger.info("Starting poll_word_embedding_batch_handler (ChromaDB Delta pattern)")
    logger.info(f"Event: {event}")

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