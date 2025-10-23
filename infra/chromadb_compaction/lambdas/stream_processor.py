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
from typing import Any, Dict

# Enhanced observability imports
from utils import (
    get_operation_logger,
    metrics,
    trace_function,
    start_compaction_lambda_monitoring,
    stop_compaction_lambda_monitoring,
    with_compaction_timeout_protection,
    format_response,
)

# Import modular components (same pattern as utils)
from processor import (
    LambdaResponse,
    build_messages_from_records,
    publish_messages,
)

# Configure logging with observability
logger = get_operation_logger(__name__)


@trace_function(operation_name="stream_processor")
@with_compaction_timeout_protection(
    max_duration=60
)  # Stream processing should be fast
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
    logger.info(
        "Starting stream processing",
        event_records=len(event.get("Records", [])),
        correlation_id=correlation_id,
    )

    try:
        # Handle different event types (test events vs real stream events)
        if "Records" not in event:
            logger.info(
                "Received non-stream event (likely test)",
                event_keys=list(event.keys()),
            )
            response = {
                "statusCode": 200,
                "processed_records": 0,
                "queued_messages": 0,
            }

            metrics.count("StreamProcessorTestEvents", 1)
            return format_response(
                response, event, correlation_id=correlation_id
            )

        metrics.gauge("StreamRecordsReceived", len(event["Records"]))

        logger.info(
            "Processing DynamoDB stream records",
            record_count=len(event["Records"]),
        )

        # Build messages from stream records
        messages_to_send = []
        processed_records = 0

        for record in event["Records"]:
            try:
                event_id = record.get("eventID", "unknown")
                event_name = record.get("eventName", "unknown")

                logger.info(
                    "Processing stream record",
                    record_id=event_id,
                    event_name=event_name,
                )

                metrics.count(
                    "StreamRecordProcessed", 1, {"event_name": event_name}
                )

                # Build message(s) for this record
                record_messages = build_messages_from_records(
                    [record], metrics
                )

                if record_messages:
                    messages_to_send.extend(record_messages)
                    processed_records += 1
                else:
                    metrics.count(
                        "StreamRecordSkipped",
                        1,
                        {"reason": "not_relevant_entity"},
                    )
                    logger.debug(
                        "Skipped record - not relevant entity",
                        record_id=event_id,
                                    )

            except (ValueError, KeyError, TypeError) as e:
                event_id = record.get("eventID", "unknown")
                logger.error(
                    "Error processing stream record",
                    record_id=event_id,
                    error=str(e),
                )

                metrics.count(
                    "StreamRecordProcessingError",
                    1,
                    {"error_type": type(e).__name__},
                )
                # Continue processing other records

        # Send all messages to appropriate SQS queues
        if messages_to_send:
            sent_count = publish_messages(
                messages_to_send, metrics
            )
            logger.info(
                "Sent messages to compaction queues", message_count=sent_count
            )

            metrics.count("MessagesQueuedForCompaction", sent_count)
        else:
            sent_count = 0

        response = LambdaResponse(
            status_code=200,
            processed_records=processed_records,
            queued_messages=sent_count,
        )

        logger.info(
            "Stream processing completed",
            processed_records=processed_records,
            queued_messages=sent_count,
        )

        metrics.gauge("StreamProcessorProcessedRecords", processed_records)
        metrics.gauge("StreamProcessorQueuedMessages", sent_count)

        # Convert dataclass to dict for AWS Lambda JSON serialization
        result = response.to_dict()

        return format_response(result, event, correlation_id=correlation_id)

    except Exception as e:
        logger.exception(
            "Stream processor failed",
        )

        metrics.count(
            "StreamProcessorError", 1, {"error_type": type(e).__name__}
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


# Re-export for backward compatibility with existing tests
from processor import (
    ChromaDBCollection,
    FieldChange,
    ParsedStreamRecord,
    StreamMessage,
    detect_entity_type as _detect_entity_type,
    parse_entity as _parse_entity,
    parse_stream_record,
    get_chromadb_relevant_changes,
    is_compaction_run as _is_compaction_run,
    parse_compaction_run as _parse_compaction_run,
    publish_messages as send_messages_to_queues,
)
