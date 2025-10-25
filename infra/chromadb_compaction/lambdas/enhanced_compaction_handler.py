"""Enhanced ChromaDB compaction handler with stream message support.

This handler extends the existing compaction functionality to handle both:
1. Traditional delta file processing (existing functionality)
2. DynamoDB stream messages for metadata updates (new functionality)

Maintains compatibility with existing SQS queue and mutex lock infrastructure.
"""

# pylint: disable=duplicate-code,too-many-instance-attributes,too-many-locals
# Some duplication with stream_processor is expected for shared data structures
# Complex compaction logic requires many variables and nested operations

import json
import os
import time
from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict, List, Optional

import boto3

# Enhanced observability imports
from utils import (
    get_operation_logger,
    metrics,
    trace_function,
    trace_compaction_operation,
    start_compaction_lambda_monitoring,
    stop_compaction_lambda_monitoring,
    with_compaction_timeout_protection,
    format_response,
)

# Import receipt_dynamo for proper DynamoDB operations
from receipt_dynamo.data.dynamo_client import DynamoClient

# Import modular components - flexible for both Lambda and test environments
try:
    # Try absolute import first (Lambda environment)
    from compaction import (
        process_sqs_messages,
        categorize_stream_messages,
        group_messages_by_collection,
        process_metadata_updates,
        process_label_updates,
        process_compaction_run_messages,
        LambdaResponse,
        StreamMessage,
        MetadataUpdateResult,
        LabelUpdateResult,
    )
    MODULAR_MODE = True
    print("✅ Modular mode: Using compaction package")
except ImportError as e:
    try:
        # Try relative import (test environment)
        from .compaction import (
            process_sqs_messages,
            categorize_stream_messages,
            group_messages_by_collection,
            process_metadata_updates,
            process_label_updates,
            process_compaction_run_messages,
            LambdaResponse,
            StreamMessage,
            MetadataUpdateResult,
            LabelUpdateResult,
        )
        MODULAR_MODE = True
        print("✅ Modular mode: Using compaction package (relative import)")
    except ImportError as e2:
        print(f"⚠️  Fallback mode: {e2}")
        MODULAR_MODE = False
    
    # Fallback implementations
    class LambdaResponse:
        def __init__(self, status_code: int, message: str, **kwargs):
            self.status_code = status_code
            self.message = message
            for key, value in kwargs.items():
                setattr(self, key, value)
        
        def to_dict(self):
            result = {"statusCode": self.status_code, "message": self.message}
            for key, value in self.__dict__.items():
                if key not in ["status_code", "message"] and value is not None:
                    result[key] = value
            return result
    
    class StreamMessage:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)
    
    class MetadataUpdateResult:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)
        
        def to_dict(self):
            return {k: v for k, v in self.__dict__.items() if v is not None}
    
    class LabelUpdateResult:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)
        
        def to_dict(self):
            return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def process_sqs_messages(records, logger, metrics=None, **kwargs):
        """Fallback implementation that logs messages instead of processing."""
        logger.info(f"Fallback mode: Would process {len(records)} SQS messages")
        return LambdaResponse(
            status_code=200,
            message=f"Fallback mode: {len(records)} messages logged",
            processed_messages=len(records)
        ).to_dict()
    
    def process_stream_messages(stream_messages, logger, metrics=None, **kwargs):
        """Fallback implementation that logs messages instead of processing."""
        logger.info(f"Fallback mode: Would process {len(stream_messages)} stream messages")
        return LambdaResponse(
            status_code=200,
            message=f"Fallback mode: {len(stream_messages)} stream messages logged",
            stream_messages=len(stream_messages)
        ).to_dict()


# Configure logging with observability
logger = get_operation_logger(__name__)

# Initialize clients
sqs_client = boto3.client("sqs")

# Initialize DynamoDB client only when needed to avoid import-time errors
DYNAMO_CLIENT = None


def get_dynamo_client():
    """Get DynamoDB client, initializing if needed."""
    global DYNAMO_CLIENT  # pylint: disable=global-statement
    if DYNAMO_CLIENT is None:
        DYNAMO_CLIENT = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    return DYNAMO_CLIENT


# Get configuration from environment
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "30"))
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "3"))
max_heartbeat_failures = int(os.environ.get("MAX_HEARTBEAT_FAILURES", "3"))
compaction_queue_url = os.environ.get("COMPACTION_QUEUE_URL", "")


@trace_function(operation_name="enhanced_compaction_handler")
@with_compaction_timeout_protection(
    max_duration=840
)  # 14 minutes for long compaction operations
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Enhanced entry point for Lambda handler.

    Routes to appropriate handler based on trigger type:
    - SQS messages: Process stream events and traditional deltas
    - Direct invocation: Traditional compaction operations
    """
    # Configure receipt_label loggers to respect Lambda's LOG_LEVEL for debugging
    import logging

    log_level = getattr(
        logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO
    )

    # Configure the main receipt_label logger and its child loggers
    receipt_label_logger = logging.getLogger("receipt_label")
    receipt_label_logger.setLevel(log_level)

    # CRITICAL FIX: Add handler to receipt_label loggers so they can output to CloudWatch
    if not receipt_label_logger.handlers:
        # Create a handler that outputs to stdout (CloudWatch captures this)
        handler = logging.StreamHandler()

        # Use the same JSON formatter as ChromaDB if available, otherwise simple format
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

    # Specifically configure the lock_manager logger that we need for debugging
    lock_manager_logger = logging.getLogger("receipt_label.utils.lock_manager")
    lock_manager_logger.setLevel(log_level)

    # Test messages to verify logger configuration is working
    receipt_label_logger.info(
        "INFO: receipt_label logger configured for level %s with handler",
        os.environ.get("LOG_LEVEL", "INFO"),
    )
    lock_manager_logger.debug(
        "DEBUG: lock_manager logger configured successfully"
    )
    lock_manager_logger.info(
        "INFO: lock_manager logger test - this should be visible"
    )

    correlation_id = None

    # Start monitoring
    start_compaction_lambda_monitoring(context)
    correlation_id = getattr(logger, "correlation_id", None)
    logger.info(
        "Enhanced compaction handler started",
        event_keys=list(event.keys()),
        correlation_id=correlation_id,
    )

    start_time = time.time()
    function_name = (
        context.function_name
        if context and hasattr(context, "function_name")
        else "enhanced_compaction_handler"
    )

    try:
        # Check if this is an SQS trigger
        if "Records" in event:
            metrics.gauge(
                "CompactionRecordsReceived", len(event["Records"])
            )

            result = process_sqs_messages(
                records=event["Records"],
                logger=logger,
                metrics=metrics,
                process_stream_messages_func=process_stream_messages,
                process_delta_messages_func=process_delta_messages,
            )

            # IMPORTANT: For SQS partial-batch failure, return the raw shape
            # {"batchItemFailures": [...]} without any wrapping so the
            # Lambda service honors per-record retries.
            if isinstance(result, dict) and "batchItemFailures" in result:
                return result

            # Track successful execution with metrics
            execution_time = time.time() - start_time
            metrics.timer("CompactionLambdaExecutionTime", execution_time)
            metrics.count("CompactionLambdaSuccess", 1)
            logger.info(
                "Enhanced compaction handler completed successfully",
                execution_time_seconds=execution_time,
            )

            # Format response with observability
            return format_response(result, event)

        # Direct invocation not supported - Lambda is for SQS triggers only
        logger.warning(
            "Direct invocation not supported", invocation_type="direct"
        )
        metrics.count("CompactionDirectInvocationAttempt", 1)

        response = LambdaResponse(
            status_code=400,
            error="Direct invocation not supported",
            message=(
                "This Lambda is designed to process SQS messages "
                "from DynamoDB streams"
            ),
        )

        return format_response(response.to_dict(), event, is_error=True)

    except Exception as e:
        execution_time = time.time() - start_time
        error_type = type(e).__name__

        # Enhanced error logging
        logger.error(
            "Enhanced compaction handler failed",
            error=str(e),
            error_type=error_type,
            execution_time_seconds=execution_time,
            exc_info=True,
        )
        metrics.count(
            "CompactionLambdaError", 1, {"error_type": error_type}
        )

        error_response = LambdaResponse(
            status_code=500,
            message=f"Compaction handler failed: {str(e)}",
            error=str(e),
        )

        return format_response(
            error_response.to_dict(), event, is_error=True
        )

    finally:
        # Stop monitoring
        stop_compaction_lambda_monitoring()


def process_delta_messages(
    delta_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process traditional delta file messages.

    Currently not implemented - this handler focuses on DynamoDB
    stream messages. Traditional delta processing would be handled
    by a separate compaction system.
    """
    logger.info(
        "Received delta messages (not processed)",
        count=len(delta_messages),
    )
    logger.warning("Delta message processing not implemented")
    metrics.count("CompactionDeltaMessagesSkipped", len(delta_messages))

    response = LambdaResponse(
        status_code=200,
        processed_deltas=0,  # None actually processed
        skipped_deltas=len(delta_messages),
        message=(
            "Delta messages skipped - not implemented in "
            "stream-focused handler"
        ),
    )
    return response.to_dict()


# Process parsed DynamoDB stream messages grouped by collection
def process_stream_messages(
    stream_messages: List[StreamMessage],
) -> Dict[str, Any]:
    messages_by_collection = group_messages_by_collection(stream_messages)

    total_metadata_updates = 0
    total_label_updates = 0
    total_compaction_merged = 0

    for collection, msgs in messages_by_collection.items():
        metadata_msgs, label_msgs, compaction_run_msgs = categorize_stream_messages(
            msgs
        )

        # Process metadata updates
        if metadata_msgs:
            metadata_results = process_metadata_updates(
                metadata_updates=metadata_msgs,
                collection=collection,
                logger=logger,
                metrics=metrics,
                get_dynamo_client_func=get_dynamo_client,
            )
            total_metadata_updates += sum(
                r.updated_count for r in metadata_results if getattr(r, "error", None) is None
            )

        # Process label updates
        if label_msgs:
            label_results = process_label_updates(
                label_updates=label_msgs,
                collection=collection,
                logger=logger,
                metrics=metrics,
                get_dynamo_client_func=get_dynamo_client,
            )
            total_label_updates += sum(
                r.updated_count for r in label_results if getattr(r, "error", None) is None
            )

        # Process compaction run merges
        if compaction_run_msgs:
            merged = process_compaction_run_messages(
                compaction_runs=compaction_run_msgs,
                collection=collection,
                logger=logger,
                metrics=metrics,
                get_dynamo_client_func=get_dynamo_client,
            )
            total_compaction_merged += merged

    return LambdaResponse(
        status_code=200,
        message="Stream messages processed",
        metadata_updates=total_metadata_updates,
        label_updates=total_label_updates,
    ).to_dict()


# Alias for consistent naming with other handlers
handle = lambda_handler