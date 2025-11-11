"""Word polling handler for OpenAI batch results.

Pure business logic - no Lambda-specific code.
"""

import json
import os
from typing import Any, Dict, Optional, Tuple

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
    emf_metrics,
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


async def _ensure_receipt_metadata_async(
    image_id: str,
    receipt_id: int,
    client_manager,
) -> None:
    """Create receipt_metadata if missing, using LangChain workflow with Ollama Cloud.

    This is used in the word polling handler to ensure receipt_metadata exists
    before processing embeddings, since we're writing to ChromaDB through the
    step function and don't have access to it here.
    """
    try:
        # Check if metadata already exists
        try:
            existing_metadata = client_manager.dynamo.get_receipt_metadata(image_id, receipt_id)
            logger.debug(
                "Receipt metadata already exists",
                image_id=image_id,
                receipt_id=receipt_id,
            )
            return
        except Exception as check_error:
            # Check if this is a validation error (corrupted metadata) vs missing metadata
            error_str = str(check_error).lower()
            if "place id must be a string" in error_str or "place_id must be a string" in error_str:
                # Metadata exists but is corrupted - delete it and recreate
                logger.warning(
                    "Found corrupted receipt_metadata, will delete and recreate",
                    image_id=image_id,
                    receipt_id=receipt_id,
                    error=str(check_error),
                )
                try:
                    # Try to delete the corrupted metadata
                    # We need to construct the key manually since we can't read it
                    pk = f"IMAGE#{image_id}"
                    sk = f"RECEIPT#{receipt_id:05d}#METADATA"
                    client_manager.dynamo._client.delete_item(
                        TableName=client_manager.dynamo.table_name,
                        Key={
                            "PK": {"S": pk},
                            "SK": {"S": sk},
                        },
                    )
                    logger.info(
                        "Deleted corrupted receipt_metadata",
                        image_id=image_id,
                        receipt_id=receipt_id,
                    )
                except Exception as delete_error:
                    logger.warning(
                        "Failed to delete corrupted metadata, will try to overwrite",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        error=str(delete_error),
                    )
            # Metadata doesn't exist or was corrupted, create it
            pass

        # Get API keys from environment
        google_places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        ollama_key = os.environ.get("OLLAMA_API_KEY")
        langchain_key = os.environ.get("LANGCHAIN_API_KEY")

        if not google_places_key:
            error_msg = f"GOOGLE_PLACES_API_KEY not set, cannot create receipt_metadata for receipt {receipt_id} (image {image_id})"
            logger.error(error_msg)
            raise ValueError(error_msg)
        if not ollama_key:
            error_msg = f"OLLAMA_API_KEY not set, cannot create receipt_metadata for receipt {receipt_id} (image {image_id})"
            logger.error(error_msg)
            raise ValueError(error_msg)
        if not langchain_key:
            error_msg = f"LANGCHAIN_API_KEY not set, cannot create receipt_metadata for receipt {receipt_id} (image {image_id})"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info(
            "Creating receipt_metadata using LangChain workflow with Ollama Cloud",
            image_id=image_id,
            receipt_id=receipt_id,
        )

        # Import the LangChain workflow (with error handling for missing dependencies)
        try:
            from receipt_label.langchain.metadata_creation import create_receipt_metadata_simple
        except ImportError as import_error:
            error_msg = f"Failed to import LangChain workflow: {import_error}. Make sure langchain dependencies are installed."
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from import_error

        # Get receipt data (lines and words) for the LangChain workflow
        try:
            receipt_details = client_manager.dynamo.get_receipt_details(image_id, receipt_id)
            receipt_lines = receipt_details.lines
            receipt_words = receipt_details.words
        except Exception as receipt_error:
            error_msg = f"Failed to get receipt details: {receipt_error}"
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from receipt_error

        # Create metadata using LangChain workflow
        try:
            metadata = await create_receipt_metadata_simple(
                client=client_manager.dynamo,
                image_id=image_id,
                receipt_id=receipt_id,
                google_places_api_key=google_places_key,
                ollama_api_key=ollama_key,
                langsmith_api_key=langchain_key,
                thinking_strength="medium",  # Use medium thinking strength for balance of speed/quality
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
            )
        except Exception as workflow_error:
            error_msg = f"LangChain workflow failed: {workflow_error}"
            logger.error(
                error_msg,
                image_id=image_id,
                receipt_id=receipt_id,
                error_type=type(workflow_error).__name__,
                exc_info=True,
            )
            raise ValueError(error_msg) from workflow_error

        if metadata:
            logger.info(
                "Successfully created receipt_metadata using LangChain workflow",
                image_id=image_id,
                receipt_id=receipt_id,
                place_id=metadata.place_id,
                merchant_name=metadata.merchant_name,
            )
        else:
            error_msg = f"Failed to create receipt_metadata for receipt {receipt_id} (image {image_id})"
            logger.error(error_msg)
            raise ValueError(error_msg)

    except Exception as e:
        logger.error(
            "Error creating receipt_metadata",
            image_id=image_id,
            receipt_id=receipt_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        # Re-raise - metadata is required for embeddings to work
        raise


def _ensure_receipt_metadata(
    image_id: str,
    receipt_id: int,
    client_manager,
) -> None:
    """Synchronous wrapper for async metadata creation.

    Lambda functions don't have a running event loop by default,
    so we can use asyncio.run() directly.
    """
    import asyncio
    try:
        # Lambda functions don't have a running event loop, so asyncio.run() should work
        asyncio.run(_ensure_receipt_metadata_async(image_id, receipt_id, client_manager))
    except Exception as e:
        # Log the full error with traceback for debugging
        logger.error(
            "Error in async metadata creation wrapper",
            image_id=image_id,
            receipt_id=receipt_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        # Re-raise to preserve the original error
        raise


@with_timeout_protection(
    max_duration=840, operation_name="word_polling_handler"
)  # 14 minutes max
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
    register_shutdown_callback(
        lambda: logger.info("Graceful shutdown initiated for word polling")
    )

    # Collect metrics during processing to batch them via EMF (cost-effective)
    collected_metrics: Dict[str, float] = {}
    metric_dimensions: Dict[str, str] = {}
    error_types: Dict[str, int] = {}

    try:
        return _handle_internal(event, context, collected_metrics, metric_dimensions, error_types)
    except CircuitBreakerOpenError as e:
        logger.error("Circuit breaker prevented operation", error=str(e))
        collected_metrics["WordPollingCircuitBreakerBlocked"] = 1
        error_types["CircuitBreakerOpenError"] = error_types.get("CircuitBreakerOpenError", 0) + 1

        # Log metrics via EMF before raising
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={"error_types": error_types},
        )
        raise
    finally:
        stop_lambda_monitoring()
        final_cleanup()


def _handle_internal(
    event: Dict[str, Any],
    context: Any,
    collected_metrics: Dict[str, float],
    metric_dimensions: Dict[str, str],
    error_types: Dict[str, int],
) -> Dict[str, Any]:
    """Internal handler with full instrumentation and error handling."""
    try:
        return _handle_internal_core(event, context, collected_metrics, metric_dimensions, error_types)
    except TimeoutError as e:
        logger.error("Timeout error in word polling", error=str(e))
        collected_metrics["WordPollingTimeouts"] = collected_metrics.get("WordPollingTimeouts", 0) + 1
        metric_dimensions["timeout_stage"] = "handler"
        error_types["TimeoutError"] = error_types.get("TimeoutError", 0) + 1
        tracer.add_annotation("timeout", "true")

        # Log metrics via EMF before raising
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={"error_types": error_types},
        )
        raise
    except Exception as e:
        logger.error(
            "Unexpected error in word polling",
            error=str(e),
            error_type=type(e).__name__,
        )
        collected_metrics["WordPollingErrors"] = collected_metrics.get("WordPollingErrors", 0) + 1
        metric_dimensions["error_type"] = type(e).__name__
        error_types[type(e).__name__] = error_types.get(type(e).__name__, 0) + 1
        tracer.add_annotation("error", type(e).__name__)
        tracer.add_metadata(
            "error_details", {"message": str(e), "type": type(e).__name__}
        )

        # Log metrics via EMF before raising
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={"error_types": error_types},
        )
        raise


def _handle_internal_core(
    event: Dict[str, Any],
    context: Any,
    collected_metrics: Dict[str, float],
    metric_dimensions: Dict[str, str],
    error_types: Dict[str, int],
) -> Dict[str, Any]:
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

    # Count invocation (aggregated, not per-call)
    collected_metrics["WordPollingInvocations"] = 1

    # Check timeout before starting
    if check_timeout():
        logger.error("Lambda timeout detected before processing")
        collected_metrics["WordPollingTimeouts"] = collected_metrics.get("WordPollingTimeouts", 0) + 1
        metric_dimensions["timeout_stage"] = "pre_processing"
        error_types["TimeoutError"] = error_types.get("TimeoutError", 0) + 1

        # Log metrics via EMF before raising
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={"error_types": error_types},
        )
        raise TimeoutError("Lambda timeout detected before processing")

    with operation_with_timeout("get_client_manager", max_duration=30):
        client_manager = get_client_manager()

    # Check the batch status with monitoring and circuit breaker protection
    with trace_openai_batch_poll(batch_id, openai_batch_id):
        with operation_with_timeout(
            "get_openai_batch_status", max_duration=60
        ):
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
    collected_metrics["BatchStatusChecked"] = collected_metrics.get("BatchStatusChecked", 0) + 1
    metric_dimensions["batch_status"] = batch_status

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
            collected_metrics["WordPollingTimeouts"] = collected_metrics.get("WordPollingTimeouts", 0) + 1
            metric_dimensions["timeout_stage"] = "pre_results"
            error_types["TimeoutError"] = error_types.get("TimeoutError", 0) + 1

            # Log metrics via EMF before raising
            emf_metrics.log_metrics(
                collected_metrics,
                dimensions=metric_dimensions if metric_dimensions else None,
                properties={"error_types": error_types},
            )
            raise TimeoutError(
                "Lambda timeout detected before result processing"
            )

        # Download the batch results with monitoring and circuit breaker protection
        with tracer.subsegment("OpenAI.DownloadResults", namespace="remote"):
            with operation_with_timeout(
                "download_openai_batch_result", max_duration=180
            ):
                with openai_circuit_breaker().call():
                    results = download_openai_batch_result(openai_batch_id)

        result_count = len(results)
        logger.info("Downloaded embedding results", result_count=result_count)
        collected_metrics["DownloadedResults"] = result_count
        tracer.add_metadata("result_count", result_count)

        # Ensure receipt_metadata exists for all receipts (create if missing using Places API)
        # This is required because get_receipt_descriptions requires receipt_metadata
        # and embeddings need metadata to work properly
        with operation_with_timeout("ensure_receipt_metadata", max_duration=120):
            from receipt_label.embedding.word.poll import _get_unique_receipt_and_image_ids
            unique_receipts = _get_unique_receipt_and_image_ids(results)
            missing_metadata = []
            for receipt_id, image_id in unique_receipts:
                try:
                    _ensure_receipt_metadata(image_id, receipt_id, client_manager)
                    # Verify metadata was created (or already existed)
                    try:
                        client_manager.dynamo.get_receipt_metadata(image_id, receipt_id)
                        logger.debug(
                            "Verified receipt_metadata exists",
                            image_id=image_id,
                            receipt_id=receipt_id,
                        )
                    except Exception as verify_error:
                        logger.error(
                            "Metadata verification failed - metadata was not created",
                            image_id=image_id,
                            receipt_id=receipt_id,
                            verify_error=str(verify_error),
                            verify_error_type=type(verify_error).__name__,
                        )
                        missing_metadata.append((image_id, receipt_id))
                except Exception as e:
                    logger.error(
                        "Failed to ensure receipt_metadata",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True,  # Include full traceback
                    )
                    missing_metadata.append((image_id, receipt_id))

            # Fail if any receipts are missing metadata - embeddings require it
            if missing_metadata:
                error_msg = (
                    f"Receipt metadata is required but missing for {len(missing_metadata)} receipt(s). "
                    f"Failed to create metadata for: {missing_metadata[:5]}"  # Show first 5
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

        # Get receipt details with timeout protection
        with operation_with_timeout(
            "get_receipt_descriptions", max_duration=60
        ):
            descriptions = get_receipt_descriptions(results)

        description_count = len(descriptions)
        logger.info(
            "Retrieved receipt descriptions",
            description_count=description_count,
        )
        collected_metrics["ProcessedDescriptions"] = description_count

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
            collected_metrics["WordPollingTimeouts"] = collected_metrics.get("WordPollingTimeouts", 0) + 1
            metric_dimensions["timeout_stage"] = "pre_save"
            error_types["TimeoutError"] = error_types.get("TimeoutError", 0) + 1

            # Log metrics via EMF before raising
            emf_metrics.log_metrics(
                collected_metrics,
                dimensions=metric_dimensions if metric_dimensions else None,
                properties={"error_types": error_types},
            )
            raise TimeoutError("Lambda timeout detected before delta save")

        # Save embeddings as delta with comprehensive monitoring and circuit breaker protection
        with trace_chromadb_delta_save("words", result_count):
            with operation_with_timeout(
                "save_word_embeddings_as_delta", max_duration=300
            ):
                with timeout_aware_operation(
                    "save_word_embeddings_delta", check_interval=30
                ) as (stop_event, should_stop):
                    with chromadb_circuit_breaker().call():
                        # Check for graceful shutdown during long operation
                        if should_stop():
                            logger.warning(
                                "Save operation cancelled due to shutdown"
                            )
                            raise RuntimeError(
                                "Operation cancelled during graceful shutdown"
                            )

                        delta_result = save_word_embeddings_as_delta(
                            results,
                            descriptions,
                            batch_id,
                            bucket_name,
                            sqs_queue_url,
                        )

        delta_id = delta_result["delta_id"]
        embedding_count = delta_result["embedding_count"]

        logger.info(
            "Saved word embeddings delta",
            delta_id=delta_id,
            embedding_count=embedding_count,
            batch_id=batch_id,
        )

        # Collect metrics (aggregated, not per-call)
        collected_metrics["SavedEmbeddings"] = embedding_count
        collected_metrics["DeltasSaved"] = collected_metrics.get("DeltasSaved", 0) + 1
        metric_dimensions["collection"] = "words"

        # Add to trace
        tracer.add_metadata("delta_result", delta_result)
        tracer.add_annotation("delta_id", delta_id)

        # Mark batch complete only if NOT in step function mode (skip_sqs=False means standalone mode)
        # In step function mode, batches will be marked complete after successful compaction
        if not skip_sqs:
        with operation_with_timeout("mark_batch_complete", max_duration=30):
            mark_batch_complete(batch_id)
        logger.info("Marked batch as complete", batch_id=batch_id)
        else:
            logger.info(
                "Skipping batch completion marking (step function mode - will mark after compaction)",
                batch_id=batch_id,
            )

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
        collected_metrics["WordPollingSuccess"] = 1
        tracer.add_annotation("success", "true")

        # Log all metrics via EMF in a single log line (no API call cost)
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={
                "batch_id": batch_id,
                "openai_batch_id": openai_batch_id,
                "error_types": error_types,
            },
        )

        return result

    elif (
        status_result["action"] == "process_partial"
        and batch_status == "expired"
    ):
        # Handle expired batch with partial results
        partial_results = status_result.get("partial_results", [])
        failed_ids = status_result.get("failed_ids", [])

        if partial_results:
            logger.info(
                "Processing partial results", count=len(partial_results)
            )

            # Get receipt details for successful results
            descriptions = get_receipt_descriptions(partial_results)

            # Get bucket name for delta save
            bucket_name = os.environ.get("CHROMADB_BUCKET")
            if not bucket_name:
                raise ValueError("CHROMADB_BUCKET environment variable not set")

            # Determine SQS queue URL based on skip_sqs flag
            sqs_queue_url = None if skip_sqs else os.environ.get("COMPACTION_QUEUE_URL")

            # Save partial results
            delta_result = save_word_embeddings_as_delta(
                partial_results, descriptions, batch_id, bucket_name, sqs_queue_url
            )

            # Skip writing to DynamoDB - we only store in ChromaDB now
            logger.info(
                "Processed partial embedding results",
                count=len(partial_results),
            )

        # Mark failed items for retry
        if failed_ids:
            marked = mark_items_for_retry(failed_ids, "word", client_manager)
            logger.info("Marked words for retry", count=marked)

        # Log metrics via EMF
        collected_metrics["WordPollingPartialResults"] = collected_metrics.get("WordPollingPartialResults", 0) + 1
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={
                "batch_id": batch_id,
                "action": "process_partial",
                "error_types": error_types,
            },
        )

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
            "Batch failed with errors",
            openai_batch_id=openai_batch_id,
            error_count=error_info.get("error_count", 0),
        )

        # Log metrics via EMF
        collected_metrics["WordPollingFailures"] = collected_metrics.get("WordPollingFailures", 0) + 1
        error_types.update(error_info.get("error_types", {}))
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={
                "batch_id": batch_id,
                "action": "handle_failure",
                "error_types": error_types,
            },
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
        collected_metrics[f"WordPolling{status_result['action'].title()}"] = collected_metrics.get(f"WordPolling{status_result['action'].title()}", 0) + 1

        # Log metrics via EMF
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={
                "batch_id": batch_id,
                "action": status_result["action"],
                "error_types": error_types,
            },
        )

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
            action=status_result.get("action"),
            status_result=status_result,
        )
        collected_metrics["WordPollingErrors"] = collected_metrics.get("WordPollingErrors", 0) + 1
        metric_dimensions["error_type"] = "unknown_action"
        error_types["unknown_action"] = error_types.get("unknown_action", 0) + 1
        tracer.add_annotation("error", "unknown_action")

        # Log metrics via EMF
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={"error_types": error_types},
        )

        return {
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": "error",
            "error": f"Unknown action: {status_result.get('action')}",
        }
