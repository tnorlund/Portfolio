"""Line polling handler for OpenAI batch results.

Pure business logic - no Lambda-specific code.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

import boto3
from receipt_label.embedding.common import (
    handle_batch_status,
    mark_items_for_retry,
)
from receipt_label.embedding.line.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    save_line_embeddings_as_delta,
    update_line_embedding_status_to_success,
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

s3_client = boto3.client("s3")


async def _ensure_receipt_metadata_async(
    image_id: str,
    receipt_id: int,
    client_manager,
) -> None:
    """Create receipt_metadata if missing, using LangChain workflow with Ollama Cloud.

    This is used in the line polling handler to ensure receipt_metadata exists
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
    max_duration=840, operation_name="line_polling_handler"
)  # 14 minutes max
def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Poll a line embedding batch and save results as deltas to S3.

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
    # CRITICAL: Configure receipt_label loggers to output to CloudWatch
    # This ensures validation messages from legacy_helpers.py and chromadb_client.py appear in logs
    receipt_label_logger = logging.getLogger("receipt_label")
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    receipt_label_logger.setLevel(log_level)

    if not receipt_label_logger.handlers:
        # Create a handler that outputs to stdout (CloudWatch captures this)
        handler = logging.StreamHandler()

        # Use the same JSON formatter as the handler logger if available
        try:
            from utils.logging import StructuredFormatter
            formatter = StructuredFormatter()
        except ImportError:
            # Fallback to simple format
            formatter = logging.Formatter(
                "[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        handler.setFormatter(formatter)
        receipt_label_logger.addHandler(handler)

        # Prevent propagation to avoid duplicate logs
        receipt_label_logger.propagate = False

    # Start monitoring and timeout protection
    start_lambda_monitoring(context)

    # Register cleanup callback
    register_shutdown_callback(
        lambda: logger.info("Graceful shutdown initiated for line polling")
    )

    # Collect metrics during processing to batch them via EMF (cost-effective)
    collected_metrics: Dict[str, float] = {}
    metric_dimensions: Dict[str, str] = {}
    error_types: Dict[str, int] = {}

    try:
        return _handle_internal(event, context, collected_metrics, metric_dimensions, error_types)
    except CircuitBreakerOpenError as e:
        logger.error("Circuit breaker prevented operation", error=str(e))
        collected_metrics["LinePollingCircuitBreakerBlocked"] = 1
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
        logger.error("Timeout error in line polling", error=str(e))
        collected_metrics["LinePollingTimeouts"] = collected_metrics.get("LinePollingTimeouts", 0) + 1
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
            "Unexpected error in line polling",
            error=str(e),
            error_type=type(e).__name__,
        )
        collected_metrics["LinePollingErrors"] = collected_metrics.get("LinePollingErrors", 0) + 1
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
        "Starting line embedding batch polling",
        batch_id=event.get("batch_id"),
        openai_batch_id=event.get("openai_batch_id"),
        batch_index=event.get("batch_index"),
        manifest_s3_key=event.get("manifest_s3_key"),
        skip_sqs_notification=event.get("skip_sqs_notification", False),
    )

    # Check if we need to load batch info from S3 manifest
    manifest_s3_key = event.get("manifest_s3_key")
    manifest_s3_bucket = event.get("manifest_s3_bucket")
    batch_index = event.get("batch_index")
    pending_batches = event.get("pending_batches")

    if manifest_s3_key and manifest_s3_bucket is not None and batch_index is not None:
        # Download manifest from S3 and look up batch info
        logger.info(
            "Loading batch info from S3 manifest",
            manifest_s3_key=manifest_s3_key,
            manifest_s3_bucket=manifest_s3_bucket,
            batch_index=batch_index,
        )

        with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_file:
            tmp_file_path = tmp_file.name

        try:
            s3_client.download_file(manifest_s3_bucket, manifest_s3_key, tmp_file_path)
            with open(tmp_file_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # Look up batch info using batch_index
            if not isinstance(manifest, dict) or "batches" not in manifest:
                raise ValueError(f"Invalid manifest format: expected dict with 'batches' key")

            batches = manifest.get("batches", [])
            if not isinstance(batches, list):
                raise ValueError(f"Invalid manifest format: 'batches' must be a list")

            if batch_index < 0 or batch_index >= len(batches):
                raise ValueError(
                    f"batch_index {batch_index} out of range (0-{len(batches)-1})"
                )

            batch_info = batches[batch_index]
            batch_id = batch_info["batch_id"]
            openai_batch_id = batch_info["openai_batch_id"]

            logger.info(
                "Loaded batch info from manifest",
                batch_id=batch_id,
                openai_batch_id=openai_batch_id,
                batch_index=batch_index,
            )
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass
    elif pending_batches is not None and batch_index is not None:
        # Use inline pending_batches array
        if not isinstance(pending_batches, list):
            raise ValueError(f"pending_batches must be a list, got {type(pending_batches).__name__}")

        if batch_index < 0 or batch_index >= len(pending_batches):
            raise ValueError(
                f"batch_index {batch_index} out of range (0-{len(pending_batches)-1})"
            )

        batch_info = pending_batches[batch_index]
        batch_id = batch_info["batch_id"]
        openai_batch_id = batch_info["openai_batch_id"]

        logger.info(
            "Using batch info from inline pending_batches",
            batch_id=batch_id,
            openai_batch_id=openai_batch_id,
            batch_index=batch_index,
        )
    else:
        # Backward compatible: direct batch_id and openai_batch_id in event
        batch_id = event["batch_id"]
        openai_batch_id = event["openai_batch_id"]
        logger.info(
            "Using batch info directly from event (backward compatible)",
            batch_id=batch_id,
            openai_batch_id=openai_batch_id,
        )

    skip_sqs = event.get("skip_sqs_notification", False)

    # Add trace annotations
    tracer.add_annotation("batch_id", batch_id)
    tracer.add_annotation("openai_batch_id", openai_batch_id)
    tracer.add_annotation("handler_type", "line_polling")

    # Count invocation (aggregated, not per-call)
    collected_metrics["LinePollingInvocations"] = 1

    # Check timeout before starting
    if check_timeout():
        logger.error("Lambda timeout detected before processing")
        collected_metrics["LinePollingTimeouts"] = collected_metrics.get("LinePollingTimeouts", 0) + 1
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
            collected_metrics["LinePollingTimeouts"] = collected_metrics.get("LinePollingTimeouts", 0) + 1
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
            from receipt_label.embedding.line.poll import _get_unique_receipt_and_image_ids
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
            collected_metrics["LinePollingTimeouts"] = collected_metrics.get("LinePollingTimeouts", 0) + 1
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
        with trace_chromadb_delta_save("lines", result_count):
            with operation_with_timeout(
                "save_line_embeddings_as_delta", max_duration=300
            ):
                with timeout_aware_operation(
                    "save_line_embeddings_delta", check_interval=30
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

                        delta_result = save_line_embeddings_as_delta(
                            results,
                            descriptions,
                            batch_id,
                            bucket_name,
                            sqs_queue_url,
                        )

        # Check if delta creation failed
        if delta_result.get("status") == "failed":
            logger.error(
                "Failed to save delta for batch",
                batch_id=batch_id,
                error=delta_result.get("error", "Unknown error"),
            )
            collected_metrics["LinePollingErrors"] = collected_metrics.get("LinePollingErrors", 0) + 1
            metric_dimensions["error_type"] = "delta_save_failed"
            error_types["delta_save_failed"] = error_types.get("delta_save_failed", 0) + 1

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
                "action": "delta_save_failed",
                "error": delta_result.get(
                    "error", "Failed to save embedding delta"
                ),
                "results_count": len(results),
            }

        delta_id = delta_result["delta_id"]
        embedding_count = delta_result["embedding_count"]

        logger.info(
            "Saved line embeddings delta",
            delta_id=delta_id,
            embedding_count=embedding_count,
            batch_id=batch_id,
        )

        # Collect metrics (aggregated, not per-call)
        collected_metrics["SavedEmbeddings"] = embedding_count
        collected_metrics["DeltasSaved"] = collected_metrics.get("DeltasSaved", 0) + 1
        metric_dimensions["collection"] = "lines"

        # Add to trace
        tracer.add_metadata("delta_result", delta_result)
        tracer.add_annotation("delta_id", delta_id)

        # Update line embedding status to SUCCESS (line-specific step) with timeout protection
        with operation_with_timeout(
            "update_line_embedding_status_to_success", max_duration=60
        ):
            update_line_embedding_status_to_success(results, descriptions)
        logger.info("Updated line embedding status to SUCCESS")

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

        # Successful completion - build full result first
        full_result = {
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "action": status_result["action"],
            "results_count": len(results),
            "delta_id": delta_result["delta_id"],
            "delta_key": delta_result["delta_key"],
            "embedding_count": delta_result["embedding_count"],
            "storage": "s3_delta",
            "collection": "lines",
            "database": "lines",  # Database for line embeddings
        }

        logger.info("Successfully completed line polling", **full_result)
        collected_metrics["LinePollingSuccess"] = 1
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

        # Upload result to S3 and return only a small reference
        # This prevents the Map state from exceeding 256KB when aggregating results
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")

        result_s3_key = f"poll_results/{batch_id}/result.json"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
            json.dump(full_result, tmp_file, indent=2)
            tmp_file_path = tmp_file.name

        try:
            s3_client.upload_file(
                tmp_file_path,
                bucket,
                result_s3_key,
            )
            logger.info(
                "Uploaded poll result to S3",
                s3_key=result_s3_key,
                bucket=bucket,
                batch_id=batch_id,
            )
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

        # Return only a small S3 reference instead of full result
        # This keeps the Map state output small (~100 bytes per batch vs ~408 bytes)
        return {
            "batch_id": batch_id,
            "result_s3_key": result_s3_key,
            "result_s3_bucket": bucket,
        }

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

            # Get configuration from environment
            bucket_name = os.environ.get("CHROMADB_BUCKET")
            if not bucket_name:
                raise ValueError(
                    "CHROMADB_BUCKET environment variable not set"
                )

            # Determine SQS queue URL based on skip_sqs flag
            if skip_sqs:
                logger.info("Skipping SQS notification for partial delta")
                sqs_queue_url = None
            else:
                sqs_queue_url = os.environ.get("COMPACTION_QUEUE_URL")

            # Save partial results
            delta_result = save_line_embeddings_as_delta(
                partial_results,
                descriptions,
                batch_id,
                bucket_name,
                sqs_queue_url,
            )

            # Check if delta creation failed
            if delta_result.get("status") == "failed":
                logger.error(
                    "Failed to save partial delta for batch",
                    batch_id=batch_id,
                    error=delta_result.get("error", "Unknown error"),
                )
                # Don't return early - still need to mark failed items for retry
            else:
                # Update status for successful lines only if delta was saved
                update_line_embedding_status_to_success(
                    partial_results, descriptions
                )
                logger.info(
                    "Processed partial line embedding results",
                    count=len(partial_results),
                )

        # Mark failed items for retry
        if failed_ids:
            marked = mark_items_for_retry(failed_ids, "line", client_manager)
            logger.info("Marked lines for retry", count=marked)

        # Log metrics via EMF
        collected_metrics["LinePollingPartialResults"] = collected_metrics.get("LinePollingPartialResults", 0) + 1
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
        collected_metrics["LinePollingFailures"] = collected_metrics.get("LinePollingFailures", 0) + 1
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
        collected_metrics[f"LinePolling{status_result['action'].title()}"] = collected_metrics.get(f"LinePolling{status_result['action'].title()}", 0) + 1

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
        collected_metrics["LinePollingErrors"] = collected_metrics.get("LinePollingErrors", 0) + 1
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
