"""
DynamoDB Stream Processor Lambda for ChromaDB Compaction Integration

This module defines the Lambda function that processes DynamoDB stream events
for receipt metadata and word label changes, triggering ChromaDB metadata
updates through the existing compaction SQS queue.

Focuses on:
- RECEIPT_METADATA entities (merchant info changes)
- RECEIPT_WORD_LABEL entities (word label changes)
- COMPACTION_RUN entities (delta compaction jobs)
- Both MODIFY and REMOVE operations
"""

# pylint: disable=duplicate-code
# Some duplication with enhanced_compaction_handler is expected

import logging
import os
import time
from typing import Any, Dict, Optional

# Configuration from environment variables with sensible defaults
# These values are set by Pulumi infrastructure and should match Lambda configuration

# Max records to process in a single invocation (should match DynamoDB Stream batch_size)
MAX_RECORDS_PER_INVOCATION = int(os.getenv("MAX_RECORDS_PER_INVOCATION", "10"))

# Lambda timeout in seconds (set by Pulumi infrastructure)
LAMBDA_TIMEOUT_SECONDS = int(os.getenv("LAMBDA_TIMEOUT_SECONDS", "120"))

# Timeout threshold for graceful shutdown (80% of timeout to leave buffer for cleanup)
LAMBDA_TIMEOUT_THRESHOLD_SECONDS = int(LAMBDA_TIMEOUT_SECONDS * 0.8)

# Circuit breaker: stop processing after this many consecutive failures
MAX_CONSECUTIVE_FAILURES = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "10"))

# Import modular components (same pattern as utils)
from receipt_dynamo_stream import (
    LambdaResponse,
    build_messages_from_records,
    publish_messages,
)

# Enhanced observability imports
from utils import (
    emf_metrics,
    format_response,
    get_operation_logger,
    metrics,
    start_compaction_lambda_monitoring,
    stop_compaction_lambda_monitoring,
    trace_function,
    with_compaction_timeout_protection,
)

# Configure logging with observability
logger = get_operation_logger(__name__)


@trace_function(operation_name="stream_processor")
@with_compaction_timeout_protection(
    max_duration=LAMBDA_TIMEOUT_THRESHOLD_SECONDS
)
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process DynamoDB stream events for ChromaDB metadata synchronization.

    This function is lightweight and focuses only on:
    1. Parsing stream events for relevant entities
    2. Detecting ChromaDB-relevant field changes
    3. Creating SQS messages for the compaction Lambda

    Heavy processing is delegated to the compaction Lambda.

    Args:
        event: DynamoDB stream event
        context: Lambda context

    Returns:
        Response with processing statistics
    """
    correlation_id = None

    # Start monitoring
    start_compaction_lambda_monitoring(context)
    correlation_id = getattr(logger, "correlation_id", None)

    # Log configuration on first invocation (helps with debugging)
    logger.info(
        "Stream processor configuration",
        max_records=MAX_RECORDS_PER_INVOCATION,
        timeout_seconds=LAMBDA_TIMEOUT_SECONDS,
        timeout_threshold_seconds=LAMBDA_TIMEOUT_THRESHOLD_SECONDS,
        max_consecutive_failures=MAX_CONSECUTIVE_FAILURES,
        correlation_id=correlation_id,
    )

    # Collect metrics during processing to batch them via EMF (cost-effective)
    # This avoids expensive per-metric CloudWatch API calls
    collected_metrics: Dict[str, float] = {}

    try:
        # Validate event structure
        if "Records" not in event:
            logger.warning(
                "Received event without Records field",
                event_keys=list(event.keys()),
            )
            emf_metrics.log_metrics(
                {"StreamProcessorInvalidEvent": 1},
                properties={"event_keys": list(event.keys())},
            )
            return format_response(
                {
                    "statusCode": 400,
                    "processed_records": 0,
                    "queued_messages": 0,
                    "error": "Invalid event structure: missing Records",
                },
                event,
                correlation_id=correlation_id,
            )

        # Track processing time for timeout protection
        start_time = time.time()

        total_records = len(event["Records"])

        # Collect batch-level metrics
        collected_metrics["StreamRecordsReceived"] = total_records

        # Validate batch size - fail fast if batch is too large to prevent data loss
        # DynamoDB Streams will retry with a smaller batch on failure
        if total_records > MAX_RECORDS_PER_INVOCATION:
            error_msg = (
                f"Batch size ({total_records}) exceeds maximum "
                f"({MAX_RECORDS_PER_INVOCATION}). Rejecting to trigger retry with smaller batch."
            )
            logger.error(error_msg, total_records=total_records)
            emf_metrics.log_metrics(
                {"StreamBatchTooLarge": 1},
                properties={"total_records": total_records},
            )
            raise ValueError(error_msg)

        records_to_process = event["Records"]

        logger.info(
            "Processing DynamoDB stream batch",
            record_count=total_records,
            correlation_id=correlation_id,
        )

        # Track event types for observability
        event_name_counts: Dict[str, int] = {}
        for record in records_to_process:
            event_name = record.get("eventName", "unknown")
            event_name_counts[event_name] = (
                event_name_counts.get(event_name, 0) + 1
            )

        # Build messages from all stream records in batch
        messages_to_send = build_messages_from_records(
            records_to_process, metrics
        )

        # Calculate processing statistics
        messages_generated = len(messages_to_send)
        skipped_records = max(total_records - messages_generated, 0)

        logger.info(
            "Batch processing completed",
            total_records=total_records,
            messages_generated=messages_generated,
            skipped_records=skipped_records,
            event_breakdown=event_name_counts,
        )

        # Send all messages to appropriate SQS queues
        sent_count = 0
        if messages_to_send:
            sent_count = publish_messages(messages_to_send, metrics)
            logger.info(
                "Messages sent to compaction queues", message_count=sent_count
            )

        # Record processing duration
        processing_duration = int(
            (time.time() - start_time) * 1000
        )  # milliseconds

        # Calculate success rate
        success_rate = (
            (messages_generated / total_records * 100)
            if total_records > 0
            else 0
        )

        # Collect all metrics for batch EMF logging (cost-effective)
        collected_metrics.update(
            {
                "StreamBatchSize": total_records,
                "StreamRecordsProcessed": total_records,
                "StreamRecordsSkipped": skipped_records,
                "MessagesGenerated": messages_generated,
                "MessagesQueued": sent_count,
                "ProcessingDurationMs": processing_duration,
                "SuccessRate": success_rate,
            }
        )

        # Log all metrics via EMF in a single log line (no API call cost)
        emf_metrics.log_metrics(
            collected_metrics,
            properties={
                "event_name_counts": event_name_counts,
                "correlation_id": correlation_id,
            },
        )

        logger.info(
            "Stream processing completed successfully",
            processed_records=messages_generated,
            queued_messages=sent_count,
            duration_ms=processing_duration,
            success_rate=f"{success_rate:.1f}%",
        )

        # Return response
        response = LambdaResponse(
            status_code=200,
            processed_records=messages_generated,
            queued_messages=sent_count,
        )

        return format_response(
            response.to_dict(), event, correlation_id=correlation_id
        )

    except ValueError as e:
        # ValueError includes batch size validation errors
        logger.exception("Stream processor validation failed")
        emf_metrics.log_metrics(
            {"StreamProcessorValidationError": 1},
            properties={"error": str(e), "correlation_id": correlation_id},
        )
        # Return error to trigger DynamoDB Stream retry
        raise

    except Exception as e:
        logger.exception("Stream processor encountered unexpected error")

        # Log error via EMF (no API call cost)
        error_type = type(e).__name__
        emf_metrics.log_metrics(
            {"StreamProcessorError": 1},
            properties={
                "error_type": error_type,
                "error": str(e),
                "correlation_id": correlation_id,
            },
        )

        error_response = {
            "statusCode": 500,
            "processed_records": 0,
            "queued_messages": 0,
            "error": str(e),
        }

        return format_response(
            error_response,
            event,
            is_error=True,
            correlation_id=correlation_id,
        )

    finally:
        # Stop monitoring
        stop_compaction_lambda_monitoring()
