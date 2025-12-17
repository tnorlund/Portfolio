"""Line polling handler for OpenAI batch results.

Pure business logic - no Lambda-specific code.
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
import utils.logging  # pylint: disable=import-error
from receipt_agent.clients.factory import (
    create_embed_fn,
    create_places_client,
)
from receipt_agent.config.settings import get_settings
from receipt_agent.subagents.metadata_finder import (
    create_receipt_metadata_finder_graph,
    run_receipt_metadata_finder,
)
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.embedding.delta import save_line_embeddings_as_delta
from receipt_chroma.embedding.openai import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_unique_receipt_and_image_ids,
    handle_batch_status,
    mark_items_for_retry,
)
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    build_line_payload,
)
from receipt_chroma.s3 import download_snapshot_atomic
from utils.circuit_breaker import (  # pylint: disable=import-error
    CircuitBreakerOpenError,
    chromadb_circuit_breaker,
    openai_circuit_breaker,
)
from utils.dual_chroma_client import (
    DualChromaClient,  # pylint: disable=import-error
)
from utils.graceful_shutdown import (  # pylint: disable=import-error
    final_cleanup,
    register_shutdown_callback,
    timeout_aware_operation,
)
from utils.metrics import emf_metrics  # pylint: disable=import-error
from utils.polling_common import (  # pylint: disable=import-error
    parse_line_custom_id,
    resolve_batch_info,
)
from utils.timeout_handler import (  # pylint: disable=import-error
    check_timeout,
    operation_with_timeout,
    start_lambda_monitoring,
    stop_lambda_monitoring,
    with_timeout_protection,
)
from utils.tracing import (  # pylint: disable=import-error
    trace_chromadb_delta_save,
    trace_openai_batch_poll,
    tracer,
)

from receipt_dynamo.constants import BatchStatus, EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)

s3_client = boto3.client("s3")


def _propagate_agent_env() -> None:
    """Ensure receipt_agent settings pick up the base env vars."""
    env_aliases = [
        ("OPENAI_API_KEY", "RECEIPT_AGENT_OPENAI_API_KEY"),
        ("GOOGLE_PLACES_API_KEY", "RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"),
        ("OLLAMA_API_KEY", "RECEIPT_AGENT_OLLAMA_API_KEY"),
        ("LANGCHAIN_API_KEY", "RECEIPT_AGENT_LANGCHAIN_API_KEY"),
        ("LANGCHAIN_PROJECT", "RECEIPT_AGENT_LANGCHAIN_PROJECT"),
    ]
    for src, dest in env_aliases:
        if dest not in os.environ and src in os.environ:
            os.environ[dest] = os.environ[src]


async def _ensure_receipt_metadata_async(
    image_id: str,
    receipt_id: int,
    dynamo_client: DynamoClient,
    *,
    line_results: Optional[List[dict]] = None,
    batch_id: Optional[str] = None,
) -> None:
    """Create receipt_metadata if missing using receipt_agent + local Chroma."""
    try:
        dynamo_client.get_receipt_metadata(image_id, receipt_id)
        logger.debug(
            "Receipt metadata already exists",
            image_id=image_id,
            receipt_id=receipt_id,
        )
        return
    except EntityNotFoundError:
        logger.info(
            "Receipt metadata missing; will attempt creation",
            image_id=image_id,
            receipt_id=receipt_id,
        )
    except Exception as error:
        logger.error(
            "Failed to fetch existing receipt_metadata",
            image_id=image_id,
            receipt_id=receipt_id,
            error=str(error),
        )
        raise

    _propagate_agent_env()
    settings = get_settings()

    receipt_details = dynamo_client.get_receipt_details(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    line_records: List[LineEmbeddingRecord] = []
    if line_results:
        lines_by_id = {line.line_id: line for line in receipt_details.lines}
        for result in line_results:
            try:
                meta = parse_line_custom_id(result["custom_id"])
            except Exception as parse_error:  # pylint: disable=broad-except
                logger.warning(
                    "Skipping line embedding result with invalid custom_id",
                    custom_id=result.get("custom_id"),
                    error=str(parse_error),
                )
                continue

            if (
                meta["image_id"] != image_id
                or meta["receipt_id"] != receipt_id
            ):
                continue

            target_line = lines_by_id.get(meta["line_id"])
            if not target_line:
                logger.warning(
                    "Line not found for embedding result",
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=meta["line_id"],
                )
                continue

            line_records.append(
                LineEmbeddingRecord(
                    line=target_line,
                    embedding=result.get("embedding") or [],
                    batch_id=batch_id,
                )
            )

    line_embeddings_map = {
        record.chroma_id: record.embedding for record in line_records
    } or None

    chroma_root = Path("/tmp/chroma/metadata_finder")
    lines_dir = chroma_root / "lines"
    words_dir = chroma_root / "words"
    lines_dir.mkdir(parents=True, exist_ok=True)
    words_dir.mkdir(parents=True, exist_ok=True)

    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
    if chromadb_bucket:
        try:
            download_snapshot_atomic(
                bucket=chromadb_bucket,
                collection="lines",
                local_path=str(lines_dir),
                verify_integrity=False,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to download lines snapshot for metadata finder",
                error=str(e),
            )
        try:
            download_snapshot_atomic(
                bucket=chromadb_bucket,
                collection="words",
                local_path=str(words_dir),
                verify_integrity=False,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to download words snapshot for metadata finder",
                error=str(e),
            )

    lines_client = ChromaClient(
        persist_directory=str(lines_dir), mode="write", metadata_only=True
    )
    words_client = ChromaClient(
        persist_directory=str(words_dir), mode="write", metadata_only=True
    )

    chroma_client = DualChromaClient(lines_client, words_client, logger)
    try:
        payload = build_line_payload(
            records=line_records,
            all_lines=receipt_details.lines,
            all_words=receipt_details.words,
            merchant_name=None,
        )

        if payload["ids"]:
            collection = chroma_client.get_collection(
                "lines", create_if_missing=True
            )
            collection.upsert(
                ids=payload["ids"],
                embeddings=payload["embeddings"],
                documents=payload["documents"],
                metadatas=payload["metadatas"],
            )

        embed_fn = create_embed_fn(settings=settings)
        places_client = create_places_client(settings=settings)

        graph, state_holder = create_receipt_metadata_finder_graph(
            dynamo_client=dynamo_client,
            chroma_client=chroma_client,
            embed_fn=embed_fn,
            places_api=places_client,
            settings=settings,
        )

        result = await run_receipt_metadata_finder(
            graph=graph,
            state_holder=state_holder,
            image_id=image_id,
            receipt_id=receipt_id,
            line_embeddings=line_embeddings_map,
            word_embeddings=None,
            receipt_lines=receipt_details.lines,
            receipt_words=receipt_details.words,
        )

        if not result.get("found"):
            raise ValueError(
                f"Metadata finder could not create metadata for {image_id}#{receipt_id}"
            )

        matched_fields = []
        if result.get("merchant_name"):
            matched_fields.append("name")
        if result.get("address"):
            matched_fields.append("address")
        if result.get("phone_number"):
            matched_fields.append("phone")
        if result.get("place_id"):
            matched_fields.append("place_id")

        metadata_entity = ReceiptMetadata(
            image_id=image_id,
            receipt_id=receipt_id,
            place_id=result.get("place_id") or "",
            merchant_name=result.get("merchant_name") or "",
            matched_fields=matched_fields,
            timestamp=datetime.now(timezone.utc),
            merchant_category="",
            address=result.get("address") or "",
            phone_number=result.get("phone_number") or "",
            validated_by="metadata_finder_agent",
            reasoning=result.get("reasoning") or "",
            canonical_place_id=result.get("place_id") or "",
            canonical_merchant_name=result.get("merchant_name") or "",
            canonical_address=result.get("address") or "",
            canonical_phone_number=result.get("phone_number") or "",
        )
        dynamo_client.add_receipt_metadatas([metadata_entity])

        logger.info(
            "Created receipt_metadata via metadata finder",
            image_id=image_id,
            receipt_id=receipt_id,
            place_id=metadata_entity.place_id,
        )
    finally:
        try:
            chroma_client.close()
        except Exception as e:
            logger.debug(
                "Failed to close chroma_client during cleanup", error=str(e)
            )


def _ensure_receipt_metadata(
    image_id: str,
    receipt_id: int,
    dynamo_client: DynamoClient,
    *,
    line_results: Optional[List[dict]] = None,
    batch_id: Optional[str] = None,
) -> None:
    """Synchronous wrapper for async metadata creation."""
    import asyncio

    asyncio.run(
        _ensure_receipt_metadata_async(
            image_id=image_id,
            receipt_id=receipt_id,
            dynamo_client=dynamo_client,
            line_results=line_results,
            batch_id=batch_id,
        )
    )


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
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    receipt_agent_logger = logging.getLogger("receipt_agent")
    receipt_agent_logger.setLevel(log_level)

    if not receipt_agent_logger.handlers:
        handler = logging.StreamHandler()

        # Use the same JSON formatter as the handler logger if available
        try:
            from utils.logging import StructuredFormatter

            formatter: logging.Formatter = StructuredFormatter()
        except ImportError:
            # Fallback to simple format
            formatter = logging.Formatter(
                "[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        handler.setFormatter(formatter)
        receipt_agent_logger.addHandler(handler)

        # Prevent propagation to avoid duplicate logs
        receipt_agent_logger.propagate = False

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
        return _handle_internal(
            event, context, collected_metrics, metric_dimensions, error_types
        )
    except CircuitBreakerOpenError as e:
        logger.error("Circuit breaker prevented operation", error=str(e))
        collected_metrics["LinePollingCircuitBreakerBlocked"] = 1
        error_types["CircuitBreakerOpenError"] = (
            error_types.get("CircuitBreakerOpenError", 0) + 1
        )

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
        return _handle_internal_core(
            event, context, collected_metrics, metric_dimensions, error_types
        )
    except TimeoutError as e:
        logger.error("Timeout error in line polling", error=str(e))
        collected_metrics["LinePollingTimeouts"] = (
            collected_metrics.get("LinePollingTimeouts", 0) + 1
        )
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
        collected_metrics["LinePollingErrors"] = (
            collected_metrics.get("LinePollingErrors", 0) + 1
        )
        metric_dimensions["error_type"] = type(e).__name__
        error_types[type(e).__name__] = (
            error_types.get(type(e).__name__, 0) + 1
        )
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

    batch_id, openai_batch_id, _batch_index = resolve_batch_info(
        event,
        logger,
        s3_client,
        handler_label="line_polling",
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
        collected_metrics["LinePollingTimeouts"] = (
            collected_metrics.get("LinePollingTimeouts", 0) + 1
        )
        metric_dimensions["timeout_stage"] = "pre_processing"
        error_types["TimeoutError"] = error_types.get("TimeoutError", 0) + 1

        # Log metrics via EMF before raising
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=metric_dimensions if metric_dimensions else None,
            properties={"error_types": error_types},
        )
        raise TimeoutError("Lambda timeout detected before processing")

    # Create DynamoDB client directly (replacing client_manager pattern)
    with operation_with_timeout("create_dynamo_client", max_duration=30):
        dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

    # Create OpenAI client (needed for batch status check)
    from openai import OpenAI

    openai_client = OpenAI()  # Uses OPENAI_API_KEY from environment

    def _get_receipt_descriptions(
        results: List[dict],
    ) -> dict[str, dict[int, dict]]:
        """
        Get the receipt descriptions from the embedding results, grouped by image
        and receipt.

        Returns:
            A dict mapping each image_id (str) to a dict that maps each
            receipt_id (int) to a dict containing:
                - receipt
                - lines
                - words
                - letters
                - labels
                - metadata
                - sections
        """
        descriptions: dict[str, dict[int, dict]] = {}
        for receipt_id, image_id in get_unique_receipt_and_image_ids(results):
            receipt_details = dynamo_client.get_receipt_details(
                image_id=image_id,
                receipt_id=receipt_id,
            )
            receipt_metadata = dynamo_client.get_receipt_metadata(
                image_id=image_id,
                receipt_id=receipt_id,
            )
            receipt_sections = dynamo_client.get_receipt_sections_from_receipt(
                image_id=image_id,
                receipt_id=receipt_id,
            )
            descriptions.setdefault(image_id, {})[receipt_id] = {
                "receipt": receipt_details.receipt,
                "lines": receipt_details.lines,
                "words": receipt_details.words,
                "letters": receipt_details.letters,
                "labels": receipt_details.labels,
                "metadata": receipt_metadata,
                "sections": receipt_sections,
            }
        return descriptions

    def _update_line_embedding_status_to_success(
        results: List[dict],
        descriptions: dict[str, dict[int, dict]],
    ) -> None:
        """
        Update the embedding status of the lines to SUCCESS.

        Args:
            results: The list of embedding results.
            descriptions: The nested dict of receipt descriptions.
        """
        # Group lines by receipt for efficient updates
        lines_by_receipt: dict[str, dict[int, list]] = {}

        for result in results:
            try:
                meta = parse_line_custom_id(result["custom_id"])
            except ValueError as err:
                logger.warning(
                    "Skipping result with invalid custom_id during status update",
                    custom_id=result.get("custom_id"),
                    error=str(err),
                )
                continue
            image_id = meta["image_id"]
            receipt_id = meta["receipt_id"]
            line_id = meta["line_id"]

            # Get the lines for this receipt from descriptions
            receipt_lines = descriptions[image_id][receipt_id]["lines"]

            # Find the target line by direct line_id match
            target_line = next(
                (line for line in receipt_lines if line.line_id == line_id),
                None,
            )

            if target_line:
                # Initialize the receipt dict if needed
                if image_id not in lines_by_receipt:
                    lines_by_receipt[image_id] = {}
                if receipt_id not in lines_by_receipt[image_id]:
                    lines_by_receipt[image_id][receipt_id] = []

                # Update the embedding status to SUCCESS
                target_line.embedding_status = EmbeddingStatus.SUCCESS.value
                lines_by_receipt[image_id][receipt_id].append(target_line)
            else:
                raise ValueError(
                    f"No line found with ID {line_id} in receipt {receipt_id} "
                    f"from image {image_id}"
                )

        # Update lines individually to avoid transaction conflicts when multiple
        # batches are processed concurrently
        for image_id, receipts in lines_by_receipt.items():
            for receipt_id, lines_to_update in receipts.items():
                dynamo_client.update_receipt_lines(lines_to_update)

    def _mark_batch_complete(batch_id: str) -> None:
        """
        Mark the embedding batch as complete in the system.

        Args:
            batch_id: The identifier of the batch.
        """
        batch_summary = dynamo_client.get_batch_summary(batch_id)
        batch_summary.status = BatchStatus.COMPLETED.value
        dynamo_client.update_batch_summary(batch_summary)

    # Check the batch status with monitoring and circuit breaker protection
    with trace_openai_batch_poll(batch_id, openai_batch_id):
        with operation_with_timeout(
            "get_openai_batch_status", max_duration=60
        ):
            with openai_circuit_breaker().call():
                batch_status = get_openai_batch_status(
                    openai_batch_id, openai_client
                )

    logger.info(
        "Retrieved batch status from OpenAI",
        batch_id=batch_id,
        openai_batch_id=openai_batch_id,
        status=batch_status,
    )

    # Add status to trace
    tracer.add_annotation("batch_status", batch_status)
    collected_metrics["BatchStatusChecked"] = (
        collected_metrics.get("BatchStatusChecked", 0) + 1
    )
    metric_dimensions["batch_status"] = batch_status

    # Use modular status handler with timeout protection
    with operation_with_timeout("handle_batch_status", max_duration=30):
        status_result = handle_batch_status(
            batch_id=batch_id,
            openai_batch_id=openai_batch_id,
            status=batch_status,
            dynamo_client=dynamo_client,
            openai_client=openai_client,
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
            collected_metrics["LinePollingTimeouts"] = (
                collected_metrics.get("LinePollingTimeouts", 0) + 1
            )
            metric_dimensions["timeout_stage"] = "pre_results"
            error_types["TimeoutError"] = (
                error_types.get("TimeoutError", 0) + 1
            )

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
                    results = download_openai_batch_result(
                        openai_batch_id, openai_client
                    )

        result_count = len(results)
        logger.info("Downloaded embedding results", result_count=result_count)
        collected_metrics["DownloadedResults"] = result_count
        tracer.add_metadata("result_count", result_count)

        # Ensure receipt_metadata exists for all receipts (create if missing using Places API)
        # This is required because get_receipt_descriptions requires receipt_metadata
        # and embeddings need metadata to work properly
        with operation_with_timeout(
            "ensure_receipt_metadata", max_duration=120
        ):
            unique_receipts = get_unique_receipt_and_image_ids(results)
            missing_metadata = []
            for receipt_id, image_id in unique_receipts:
                try:
                    _ensure_receipt_metadata(
                        image_id,
                        receipt_id,
                        dynamo_client,
                        line_results=results,
                        batch_id=batch_id,
                    )
                    # Verify metadata was created (or already existed)
                    try:
                        dynamo_client.get_receipt_metadata(
                            image_id, receipt_id
                        )
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
            descriptions = _get_receipt_descriptions(results)

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
            collected_metrics["LinePollingTimeouts"] = (
                collected_metrics.get("LinePollingTimeouts", 0) + 1
            )
            metric_dimensions["timeout_stage"] = "pre_save"
            error_types["TimeoutError"] = (
                error_types.get("TimeoutError", 0) + 1
            )

            # Log metrics via EMF before raising
            emf_metrics.log_metrics(
                collected_metrics,
                dimensions=metric_dimensions if metric_dimensions else None,
                properties={"error_types": error_types},
            )
            raise TimeoutError("Lambda timeout detected before delta save")

        # Save embeddings as delta with comprehensive monitoring and circuit breaker protection
        validation_attempts = 0
        validation_retries = 0
        validation_success = False
        delta_save_start_time = time.time()

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

                        try:
                            delta_result = save_line_embeddings_as_delta(
                                results,
                                descriptions,
                                batch_id,
                                bucket_name,
                                sqs_queue_url,
                            )
                            # If we get here, validation succeeded (or was skipped)
                            validation_success = True
                            validation_attempts = 1
                        except RuntimeError as e:
                            # Check if this is a validation failure
                            error_msg = str(e).lower()
                            if (
                                "validation failed" in error_msg
                                or "delta validation" in error_msg
                            ):
                                # Validation failed after retries
                                validation_success = False
                                validation_attempts = 3  # max_retries default
                                validation_retries = (
                                    2  # retries = attempts - 1
                                )
                            raise

        delta_save_duration = time.time() - delta_save_start_time

        # Check if delta creation failed
        if delta_result.get("status") == "failed":
            logger.error(
                "Failed to save delta for batch",
                batch_id=batch_id,
                error=delta_result.get("error", "Unknown error"),
            )
            collected_metrics["LinePollingErrors"] = (
                collected_metrics.get("LinePollingErrors", 0) + 1
            )
            metric_dimensions["error_type"] = "delta_save_failed"
            error_types["delta_save_failed"] = (
                error_types.get("delta_save_failed", 0) + 1
            )

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
        collected_metrics["DeltasSaved"] = (
            collected_metrics.get("DeltasSaved", 0) + 1
        )
        collected_metrics["DeltaValidationAttempts"] = validation_attempts
        if validation_retries > 0:
            collected_metrics["DeltaValidationRetries"] = validation_retries
        collected_metrics["DeltaValidationSuccess"] = (
            1 if validation_success else 0
        )
        collected_metrics["DeltaSaveDuration"] = (
            delta_save_duration  # Includes upload + validation
        )
        metric_dimensions["collection"] = "lines"

        # Add to trace
        tracer.add_metadata("delta_result", delta_result)
        tracer.add_annotation("delta_id", delta_id)

        # Update line embedding status to SUCCESS (line-specific step) with timeout protection
        with operation_with_timeout(
            "update_line_embedding_status_to_success", max_duration=60
        ):
            _update_line_embedding_status_to_success(results, descriptions)
        logger.info("Updated line embedding status to SUCCESS")

        # Mark batch complete only if NOT in step function mode (skip_sqs=False means standalone mode)
        # In step function mode, batches will be marked complete after successful compaction
        if not skip_sqs:
            with operation_with_timeout(
                "mark_batch_complete", max_duration=30
            ):
                _mark_batch_complete(batch_id)
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
            "database": "lines",
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

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp_file:
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
            except Exception as e:
                logger.debug(
                    "Failed to clean up temp file during cleanup",
                    tmp_file_path=tmp_file_path,
                    error=str(e),
                )

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
            descriptions = _get_receipt_descriptions(partial_results)

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
                _update_line_embedding_status_to_success(
                    partial_results, descriptions
                )
                logger.info(
                    "Processed partial line embedding results",
                    count=len(partial_results),
                )

        # Mark failed items for retry
        if failed_ids:
            marked = mark_items_for_retry(failed_ids, "line", dynamo_client)
            logger.info("Marked lines for retry", count=marked)

        # Log metrics via EMF
        collected_metrics["LinePollingPartialResults"] = (
            collected_metrics.get("LinePollingPartialResults", 0) + 1
        )
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
        collected_metrics["LinePollingFailures"] = (
            collected_metrics.get("LinePollingFailures", 0) + 1
        )
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
        collected_metrics[f"LinePolling{status_result['action'].title()}"] = (
            collected_metrics.get(
                f"LinePolling{status_result['action'].title()}", 0
            )
            + 1
        )

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
        collected_metrics["LinePollingErrors"] = (
            collected_metrics.get("LinePollingErrors", 0) + 1
        )
        metric_dimensions["error_type"] = "unknown_action"
        error_types["unknown_action"] = (
            error_types.get("unknown_action", 0) + 1
        )
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
