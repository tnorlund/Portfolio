"""
Containerized Lambda handler for polling OpenAI batch results and creating ChromaDB deltas.

This handler uses the same helpers as the non-containerized version but runs in a
container to have access to the ChromaDB package for creating native delta files.

Enhanced to handle all OpenAI batch statuses including failed, expired, and in-progress.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict

# Import the same helpers used in the non-containerized version
from receipt_label.embedding.word.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    write_embedding_results_to_dynamo,
    save_word_embeddings_as_delta,
)

# Import modular status handler
from receipt_label.embedding.common import (
    handle_batch_status,
    mark_items_for_retry,
    process_partial_results,
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


def poll_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Poll a word embedding batch and save results as deltas to S3.
    
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
    logger.info("Starting containerized poll_word_embedding_batch_handler")
    logger.info(f"Event: {json.dumps(event)}")

    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]
    
    # Check if we should skip SQS notification (when called from step function)
    skip_sqs = event.get("skip_sqs_notification", False)

    # Get client manager
    client_manager = get_client_manager()

    # Check the batch status
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info(f"Batch {openai_batch_id} status: {batch_status}")

    # Use modular status handler
    status_result = handle_batch_status(
        batch_id=batch_id,
        openai_batch_id=openai_batch_id,
        status=batch_status,
        client_manager=client_manager
    )
    
    # Process based on the action determined by status handler
    if status_result["action"] == "process_results" and batch_status == "completed":
        # Download the batch results
        results = download_openai_batch_result(openai_batch_id)
        logger.info(f"Downloaded {len(results)} embedding results")

        # Get receipt details
        descriptions = get_receipt_descriptions(results)
        logger.info(f"Retrieved details for {len(descriptions)} receipts")

        # Get configuration from environment
        bucket_name = os.environ.get("CHROMADB_BUCKET")
        if not bucket_name:
            raise ValueError("CHROMADB_BUCKET environment variable not set")
        
        # Determine SQS queue URL based on skip_sqs flag
        if skip_sqs:
            logger.info("Skipping SQS notification for this delta")
            sqs_queue_url = None
        else:
            sqs_queue_url = os.environ.get("COMPACTION_QUEUE_URL")
            logger.info(f"Will send SQS notification to: {sqs_queue_url}")

        delta_result = save_word_embeddings_as_delta(
            results, descriptions, batch_id, bucket_name, sqs_queue_url
        )
        logger.info(
            f"Saved delta {delta_result['delta_id']} with "
            f"{delta_result['embedding_count']} embeddings"
        )

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
            "action": status_result["action"],
            "results_count": len(results),
            "delta_id": delta_result["delta_id"],
            "delta_key": delta_result["delta_key"],
            "embedding_count": delta_result["embedding_count"],
            "collection": "receipt_words",  # Specify collection for compaction
            "storage": "s3_delta",
        }
    
    elif status_result["action"] == "process_partial" and batch_status == "expired":
        # Handle expired batch with partial results
        partial_results = status_result.get("partial_results", [])
        failed_ids = status_result.get("failed_ids", [])
        
        if partial_results:
            logger.info(f"Processing {len(partial_results)} partial results")
            
            # Get receipt details for successful results
            descriptions = get_receipt_descriptions(partial_results)
            
            # Save partial results
            delta_result = save_word_embeddings_as_delta(
                partial_results, descriptions, batch_id
            )
            
            # Write partial results to DynamoDB
            written = write_embedding_results_to_dynamo(
                partial_results, descriptions, batch_id
            )
            logger.info(f"Processed {written} partial embedding results")
        
        # Mark failed items for retry
        if failed_ids:
            marked = mark_items_for_retry(
                failed_ids,
                "word",
                client_manager
            )
            logger.info(f"Marked {marked} words for retry")
        
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
    
    elif status_result["action"] == "handle_failure":
        # Handle completely failed batch
        error_info = status_result
        logger.error(
            f"Batch {openai_batch_id} failed with {error_info.get('error_count', 0)} errors"
        )
        
        # Could mark all items for retry here if needed
        # For now, just return the error info
        
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
    
    elif status_result["action"] in ["wait", "handle_cancellation"]:
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
    
    else:
        # Unknown action
        logger.error(f"Unknown action from status handler: {status_result['action']}")
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": "unknown",
            "error": f"Unknown action: {status_result['action']}",
        }