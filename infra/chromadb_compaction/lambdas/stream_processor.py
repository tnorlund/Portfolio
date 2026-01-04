"""
DynamoDB Stream Processor Lambda for ChromaDB Compaction Integration

This module defines the Lambda function that processes DynamoDB stream events
for receipt place data and word label changes, triggering ChromaDB metadata
updates through the existing compaction SQS queue.

Focuses on:
- RECEIPT_PLACE entities (merchant info changes)
- RECEIPT_WORD_LABEL entities (word label changes)
- COMPACTION_RUN entities (delta compaction jobs)
- Both MODIFY and REMOVE operations
"""

# pylint: disable=duplicate-code,no-name-in-module,wrong-import-order
# Some duplication with enhanced_compaction_handler is expected
# no-name-in-module: receipt_dynamo_stream is installed at Lambda runtime
# wrong-import-order: utils is local to Lambda, not third-party

import os
import time
from dataclasses import dataclass

from receipt_dynamo_stream import (
    DynamoDBStreamEvent,
    LambdaContext,
    LambdaResponse,
    StreamProcessorResponseData,
    build_messages_from_records,
    publish_messages,
)

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

# Configuration from environment variables with sensible defaults.
# These values are set by Pulumi infrastructure.

# Max records per invocation (should match DynamoDB Stream batch_size)
MAX_RECORDS_PER_INVOCATION = int(os.getenv("MAX_RECORDS_PER_INVOCATION", "10"))

# Lambda timeout in seconds (set by Pulumi infrastructure)
LAMBDA_TIMEOUT_SECONDS = int(os.getenv("LAMBDA_TIMEOUT_SECONDS", "120"))

# Timeout threshold for graceful shutdown (80% of timeout for cleanup)
LAMBDA_TIMEOUT_THRESHOLD_SECONDS = int(LAMBDA_TIMEOUT_SECONDS * 0.8)

# Circuit breaker: stop after this many consecutive failures
MAX_CONSECUTIVE_FAILURES = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "10"))


@dataclass
class BatchStats:
    """Processing statistics for a stream batch."""

    total_records: int
    messages_generated: int
    sent_count: int
    start_time: float

    @property
    def skipped(self) -> int:
        """Records that didn't generate messages."""
        return max(self.total_records - self.messages_generated, 0)

    @property
    def duration_ms(self) -> int:
        """Processing duration in milliseconds."""
        return int((time.time() - self.start_time) * 1000)

    @property
    def success_rate(self) -> float:
        """Percentage of records that generated messages."""
        if self.total_records == 0:
            return 0.0
        return self.messages_generated / self.total_records * 100

    def to_metrics(self) -> dict[str, float]:
        """Convert to metrics dict for EMF logging."""
        return {
            "StreamBatchSize": self.total_records,
            "StreamRecordsProcessed": self.total_records,
            "StreamRecordsSkipped": self.skipped,
            "MessagesGenerated": self.messages_generated,
            "MessagesQueued": self.sent_count,
            "ProcessingDurationMs": self.duration_ms,
            "SuccessRate": self.success_rate,
        }


def _count_event_types(records: list[dict]) -> dict[str, int]:
    """Count occurrences of each event type for observability."""
    counts: dict[str, int] = {}
    for record in records:
        event_name = record.get("eventName", "unknown")
        counts[event_name] = counts.get(event_name, 0) + 1
    return counts


def _validate_batch_size(total_records: int) -> None:
    """Validate batch size, raising ValueError if too large."""
    if total_records > MAX_RECORDS_PER_INVOCATION:
        raise ValueError(
            f"Batch size ({total_records}) exceeds maximum "
            f"({MAX_RECORDS_PER_INVOCATION}). "
            f"Rejecting to trigger retry with smaller batch."
        )


@trace_function(operation_name="stream_processor")
@with_compaction_timeout_protection(
    max_duration=LAMBDA_TIMEOUT_THRESHOLD_SECONDS
)
def lambda_handler(
    event: DynamoDBStreamEvent, context: LambdaContext
) -> StreamProcessorResponseData:
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
    collected_metrics: dict[str, float] = {}

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
            error_data: StreamProcessorResponseData = {
                "statusCode": 400,
                "processed_records": 0,
                "queued_messages": 0,
                "error": "Invalid event structure: missing Records",
            }
            return format_response(
                error_data,
                event,
                correlation_id=correlation_id,
            )

        # Track processing time for timeout protection
        start_time = time.time()

        total_records = len(event["Records"])

        # Collect batch-level metrics
        collected_metrics["StreamRecordsReceived"] = total_records

        # Validate batch size - fail fast if too large (prevents data loss).
        # DynamoDB Streams will retry with a smaller batch on failure.
        try:
            _validate_batch_size(total_records)
        except ValueError:
            logger.error("Batch too large", total_records=total_records)
            emf_metrics.log_metrics(
                {"StreamBatchTooLarge": 1},
                properties={"total_records": total_records},
            )
            raise

        logger.info(
            "Processing DynamoDB stream batch",
            record_count=total_records,
            correlation_id=correlation_id,
        )

        # Track event types for observability
        event_name_counts = _count_event_types(event["Records"])

        # Build messages from all stream records in batch
        messages_to_send = build_messages_from_records(
            event["Records"], metrics
        )

        # Send all messages to appropriate SQS queues
        sent_count = 0
        if messages_to_send:
            sent_count = publish_messages(messages_to_send, metrics)
            logger.info(
                "Messages sent to compaction queues", message_count=sent_count
            )

        # Collect batch statistics
        stats = BatchStats(
            total_records=total_records,
            messages_generated=len(messages_to_send),
            sent_count=sent_count,
            start_time=start_time,
        )

        logger.info(
            "Batch processing completed",
            total_records=stats.total_records,
            messages_generated=stats.messages_generated,
            skipped_records=stats.skipped,
            event_breakdown=event_name_counts,
        )

        # Log all metrics via EMF in a single log line (no API call cost)
        collected_metrics.update(stats.to_metrics())
        emf_metrics.log_metrics(
            collected_metrics,
            properties={
                "event_name_counts": event_name_counts,
                "correlation_id": correlation_id,
            },
        )

        logger.info(
            "Stream processing completed successfully",
            processed_records=stats.messages_generated,
            queued_messages=stats.sent_count,
            duration_ms=stats.duration_ms,
            success_rate=f"{stats.success_rate:.1f}%",
        )

        # Return response
        response = LambdaResponse(
            status_code=200,
            processed_records=stats.messages_generated,
            queued_messages=stats.sent_count,
        )

        return format_response(
            response.to_dict(),
            event,
            correlation_id=correlation_id,
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

    except Exception as e:  # pylint: disable=broad-exception-caught
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

        error_response: StreamProcessorResponseData = {
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
