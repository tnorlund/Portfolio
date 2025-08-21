"""Word polling handler for OpenAI batch results.

Pure business logic - no Lambda-specific code.
"""

import json
import os
from typing import Any, Dict

from receipt_label.embedding.common import (
    handle_batch_status,
    mark_items_for_retry,
)
from receipt_label.embedding.word.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    save_word_embeddings_as_delta,
)
from receipt_label.utils import get_client_manager
import utils.logging
from utils.metrics import (
    track_openai_api_call,
    track_s3_operation,
    track_chromadb_operation,
    metrics,
)
from utils.tracing import (
    trace_openai_batch_poll,
    trace_s3_snapshot_operation,
    trace_chromadb_delta_save,
    tracer,
)
from utils.timeout_handler import (
    start_lambda_monitoring,
    stop_lambda_monitoring,
    check_timeout,
    with_timeout_protection,
    operation_with_timeout,
)
from utils.circuit_breaker import (
    openai_circuit_breaker,
    s3_circuit_breaker,
    chromadb_circuit_breaker,
    CircuitBreakerOpenError,
)
from utils.graceful_shutdown import (
    register_shutdown_callback,
    timeout_aware_operation,
    final_cleanup,
)

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)


@with_timeout_protection(max_duration=840, operation_name="word_polling_handler")  # 14 minutes max
def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
# pylint: disable=unused-argument
    """Poll a word embedding batch and save results as deltas to S3.

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
    # Start monitoring and timeout protection
    start_lambda_monitoring(context)
    
    # Register cleanup callback
    register_shutdown_callback(lambda: logger.info("Graceful shutdown initiated for word polling"))
    
    try:
        return _handle_internal(event, context)
    except CircuitBreakerOpenError as e:
        logger.error("Circuit breaker prevented operation", error=str(e))
        metrics.count("WordPollingCircuitBreakerBlocked")
        raise
    finally:
        stop_lambda_monitoring()
        final_cleanup()




def _handle_internal(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Internal handler with full instrumentation and error handling."""
    try:
        return _handle_internal_core(event, context)
    except TimeoutError as e:
        logger.error("Timeout error in word polling", error=str(e))
        metrics.count("WordPollingTimeouts", dimensions={"stage": "handler"})
        tracer.add_annotation("timeout", "true")
        raise
    except Exception as e:
        logger.error("Unexpected error in word polling", error=str(e), error_type=type(e).__name__)
        metrics.count("WordPollingErrors", dimensions={"error_type": type(e).__name__})
        tracer.add_annotation("error", type(e).__name__)
        tracer.add_metadata("error_details", {"message": str(e), "type": type(e).__name__})
        raise


def _handle_internal_core(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Core handler logic with comprehensive instrumentation."""
    logger.info(
        "Starting word embedding batch polling",
        batch_id=event.get("batch_id"),
        openai_batch_id=event.get("openai_batch_id"),
        skip_sqs_notification=event.get("skip_sqs_notification", False),
    )

    # Extract event parameters
    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]
    skip_sqs = event.get("skip_sqs_notification", False)

    # Add trace annotations
    tracer.add_annotation("batch_id", batch_id)
    tracer.add_annotation("openai_batch_id", openai_batch_id)
    tracer.add_annotation("handler_type", "word_polling")

    # Count invocation
    metrics.count("WordPollingInvocations")

    # Check timeout before starting
    if check_timeout():
        logger.error("Lambda timeout detected before processing")
        metrics.count("WordPollingTimeouts", dimensions={"stage": "pre_processing"})
        raise TimeoutError("Lambda timeout detected before processing")

    with operation_with_timeout("get_client_manager", max_duration=30):
        client_manager = get_client_manager()

    # Check the batch status with monitoring and circuit breaker protection
    with trace_openai_batch_poll(batch_id, openai_batch_id):
        with operation_with_timeout("get_openai_batch_status", max_duration=60):
            with openai_circuit_breaker().call():
                batch_status = get_openai_batch_status(openai_batch_id)
    
    logger.info(
        "Retrieved batch status from OpenAI",
        batch_id=batch_id,
        openai_batch_id=openai_batch_id,
        status=batch_status,
    )

    # Add status to trace
    tracer.add_annotation("batch_status", batch_status)
    metrics.count("BatchStatusChecked", dimensions={"status": batch_status})

    # Use modular status handler with timeout protection
    with operation_with_timeout("handle_batch_status", max_duration=30):
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
        logger.info("Processing completed batch results")
        
        # Check timeout before processing
        if check_timeout():
            logger.error("Lambda timeout detected before result processing")
            metrics.count("WordPollingTimeouts", dimensions={"stage": "pre_results"})
            raise TimeoutError("Lambda timeout detected before result processing")

        # Download the batch results with monitoring and circuit breaker protection
        with tracer.subsegment("OpenAI.DownloadResults", namespace="remote"):
            with operation_with_timeout("download_openai_batch_result", max_duration=180):
                with openai_circuit_breaker().call():
                    results = download_openai_batch_result(openai_batch_id)
        
        result_count = len(results)
        logger.info("Downloaded embedding results", result_count=result_count)
        metrics.gauge("DownloadedResults", result_count)
        tracer.add_metadata("result_count", result_count)

        # Get receipt details with timeout protection
        with operation_with_timeout("get_receipt_descriptions", max_duration=60):
            descriptions = get_receipt_descriptions(results)
        
        description_count = len(descriptions)
        logger.info("Retrieved receipt descriptions", description_count=description_count)
        metrics.gauge("ProcessedDescriptions", description_count)

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
            logger.info("Will send SQS notification", queue_url=sqs_queue_url)

        # Check timeout before saving delta
        if check_timeout():
            logger.error("Lambda timeout detected before delta save")
            metrics.count("WordPollingTimeouts", dimensions={"stage": "pre_save"})
            raise TimeoutError("Lambda timeout detected before delta save")

        # Save embeddings as delta with comprehensive monitoring and circuit breaker protection
        with trace_chromadb_delta_save("words", result_count):
            with operation_with_timeout("save_word_embeddings_as_delta", max_duration=300):
                with timeout_aware_operation("save_word_embeddings_delta", check_interval=30) as (stop_event, should_stop):
                    with chromadb_circuit_breaker().call():
                        # Check for graceful shutdown during long operation
                        if should_stop():
                            logger.warning("Save operation cancelled due to shutdown")
                            raise RuntimeError("Operation cancelled during graceful shutdown")
                        
                        delta_result = save_word_embeddings_as_delta(
                            results, descriptions, batch_id, bucket_name, sqs_queue_url
                        )
        
        delta_id = delta_result['delta_id']
        embedding_count = delta_result['embedding_count']
        
        logger.info(
            "Saved word embeddings delta",
            delta_id=delta_id,
            embedding_count=embedding_count,
            batch_id=batch_id,
        )
        
        # Publish metrics
        metrics.gauge("SavedEmbeddings", embedding_count, dimensions={"collection": "words"})
        metrics.count("DeltasSaved", dimensions={"collection": "words"})
        
        # Add to trace
        tracer.add_metadata("delta_result", delta_result)
        tracer.add_annotation("delta_id", delta_id)

        # Mark batch complete with timeout protection
        with operation_with_timeout("mark_batch_complete", max_duration=30):
            mark_batch_complete(batch_id)
        logger.info("Marked batch %s as complete", batch_id)

        # Successful completion
        result = {
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
        
        logger.info("Successfully completed word polling", **result)
        metrics.count("WordPollingSuccess")
        tracer.add_annotation("success", "true")
        
        return result

    elif (
        status_result["action"] == "process_partial"
        and batch_status == "expired"
    ):
        # Handle expired batch with partial results
        partial_results = status_result.get("partial_results", [])
        failed_ids = status_result.get("failed_ids", [])

        if partial_results:
            logger.info("Processing %s partial results", len(partial_results))

            # Get receipt details for successful results
            descriptions = get_receipt_descriptions(partial_results)

            # Save partial results
            delta_result = save_word_embeddings_as_delta(
                partial_results, descriptions, batch_id
            )

            # Skip writing to DynamoDB - we only store in ChromaDB now
            logger.info(
                f"Processed {len(partial_results)} partial embedding results"
            )

        # Mark failed items for retry
        if failed_ids:
            marked = mark_items_for_retry(failed_ids, "word", client_manager)
            logger.info("Marked %s words for retry", marked)

        return {
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
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": status_result["action"],
            "hours_elapsed": status_result.get("hours_elapsed"),
            "next_step": status_result.get("next_step"),
        }

    else:
        # Unknown action
        logger.error(
            "Unknown action from status handler",
            action=status_result.get('action'),
            status_result=status_result,
        )
        metrics.count("WordPollingErrors", dimensions={"error_type": "unknown_action"})
        tracer.add_annotation("error", "unknown_action")
        
        return {
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": "error",
            "error": f"Unknown action: {status_result.get('action')}",
        }
