"""Simplified ChromaDB compaction handler using receipt_chroma package.

This handler processes DynamoDB stream messages for ChromaDB compaction by:
1. Receiving SQS messages from DynamoDB streams
2. Categorizing messages by collection (lines/words)
3. Using receipt_chroma.compaction for business logic
4. Maintaining EMF metrics and structured logging

Business logic has been moved to receipt_chroma package for reusability and testability.
"""

# pylint: disable=duplicate-code
import json
import logging
import os
import shutil
import tempfile
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Union

# AWS SDK
import boto3

# Enhanced observability imports
from utils import (
    get_operation_logger,
    emf_metrics,
    start_compaction_lambda_monitoring,
    stop_compaction_lambda_monitoring,
    with_compaction_timeout_protection,
    trace_function,
    format_response,
)

# DynamoDB and ChromaDB imports
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection

# Use receipt_chroma package for compaction logic
from receipt_chroma import ChromaClient, LockManager
from receipt_chroma.compaction import process_collection_updates

# Import StreamMessage from receipt_dynamo_stream
try:
    from receipt_dynamo_stream.models import StreamMessage
except ImportError:
    # Fallback for testing
    class StreamMessage:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)


# Get logger instance
logger = get_operation_logger(__name__)


class LambdaResponse:
    """Lambda response wrapper for consistent formatting."""

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


class MetricsAccumulator:
    """Wrapper that collects metrics in a dict instead of making API calls.

    This enables cost-effective batch EMF logging instead of per-metric CloudWatch API calls.
    """

    def __init__(self, collected_metrics: Dict[str, Any]):
        self.collected_metrics = collected_metrics

    def count(
        self,
        metric_name: str,
        value: int = 1,
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """Accumulate count metric."""
        key = metric_name
        if dimensions:
            # Include dimensions in key for uniqueness
            dim_str = "_".join(
                f"{k}={v}" for k, v in sorted(dimensions.items())
            )
            key = f"{metric_name}_{dim_str}"
        self.collected_metrics[key] = (
            self.collected_metrics.get(key, 0) + value
        )

    def gauge(
        self,
        metric_name: str,
        value: Union[int, float],
        unit: str = "None",
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """Accumulate gauge metric (use latest value)."""
        key = metric_name
        if dimensions:
            dim_str = "_".join(
                f"{k}={v}" for k, v in sorted(dimensions.items())
            )
            key = f"{metric_name}_{dim_str}"
        self.collected_metrics[key] = value

    def put_metric(
        self,
        metric_name: str,
        value: Union[int, float],
        unit: str = "Count",
        dimensions: Optional[Dict[str, str]] = None,
        timestamp: Optional[float] = None,
    ):
        """Accumulate metric (delegates to count or gauge)."""
        if unit == "Count":
            self.count(metric_name, int(value), dimensions)
        else:
            self.gauge(metric_name, value, unit, dimensions)

    def timer(
        self,
        metric_name: str,
        dimensions: Optional[Dict[str, str]] = None,
        unit: str = "Seconds",
    ):
        """Context manager for timing operations."""
        # Simple implementation for accumulator
        class TimerContext:
            def __init__(self, accumulator, name, dims, unit_val):
                self.accumulator = accumulator
                self.name = name
                self.dims = dims
                self.unit = unit_val
                self.start_time = None

            def __enter__(self):
                self.start_time = time.time()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                elapsed = time.time() - self.start_time
                self.accumulator.gauge(
                    self.name, elapsed, self.unit, self.dims
                )

        return TimerContext(self, metric_name, dimensions, unit)


def configure_receipt_chroma_loggers():
    """Configure receipt_chroma loggers to respect Lambda's LOG_LEVEL."""
    log_level = getattr(
        logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO
    )

    # Configure the main receipt_chroma logger and its child loggers
    receipt_chroma_logger = logging.getLogger("receipt_chroma")
    receipt_chroma_logger.setLevel(log_level)

    # Add handler to receipt_chroma loggers so they output to CloudWatch
    if not receipt_chroma_logger.handlers:
        handler = logging.StreamHandler()

        # Use structured formatter if available
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
        receipt_chroma_logger.addHandler(handler)
        receipt_chroma_logger.propagate = False

    # Configure lock_manager logger specifically
    lock_manager_logger = logging.getLogger("receipt_chroma.lock_manager")
    lock_manager_logger.setLevel(log_level)

    # Log configuration success
    receipt_chroma_logger.info(
        "receipt_chroma logger configured for level %s",
        os.environ.get("LOG_LEVEL", "INFO"),
    )


def parse_sqs_messages(records: List[Dict[str, Any]]) -> List[StreamMessage]:
    """Parse SQS records into StreamMessage objects.

    Args:
        records: List of SQS record dicts

    Returns:
        List of StreamMessage objects
    """
    messages = []
    for record in records:
        try:
            # Parse message body
            message_body = json.loads(record["body"])
            attributes = record.get("messageAttributes", {})

            # Extract collection from attributes
            collection_value = attributes.get("collection", {}).get(
                "stringValue", "lines"
            )
            collection = ChromaDBCollection(collection_value)

            # Create StreamMessage
            stream_msg = StreamMessage(
                entity_type=message_body.get("entity_type", ""),
                entity_data=message_body.get("entity_data", {}),
                changes=message_body.get("changes", {}),
                event_name=message_body.get("event_name", ""),
                collections=(collection,),
                timestamp=message_body.get("timestamp", ""),
                stream_record_id=record.get("messageId", ""),
                aws_region=attributes.get("region", {}).get(
                    "stringValue", "us-east-1"
                ),
                record_snapshot=message_body.get("record_snapshot"),
            )
            messages.append(stream_msg)

        except Exception as e:
            logger.error(
                "Failed to parse SQS message",
                error=str(e),
                message_id=record.get("messageId"),
            )
            # Skip invalid messages
            continue

    return messages


def process_collection(
    collection: ChromaDBCollection,
    messages: List[StreamMessage],
    logger: Any,
    metrics: Any = None,
) -> Dict[str, Any]:
    """Process stream messages for a single collection using receipt_chroma.

    This function orchestrates:
    1. Snapshot download (from S3)
    2. In-memory updates (via receipt_chroma.compaction)
    3. Snapshot upload (atomic with lock)

    Args:
        collection: Target collection (LINES or WORDS)
        messages: List of StreamMessage objects
        logger: OperationLogger instance
        metrics: MetricsAccumulator for EMF logging

    Returns:
        Dict with processing results and failed message IDs
    """
    # Environment configuration
    bucket = os.environ["CHROMADB_BUCKET"]
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        "Processing collection",
        collection=collection.value,
        message_count=len(messages),
    )

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Initialize lock manager
    lock_manager = LockManager(
        bucket=bucket,
        collection=collection.value,
        logger=logger,
    )

    # Create temp directory for snapshot
    temp_dir = tempfile.mkdtemp(prefix=f"chroma-{collection.value}-")

    try:
        # Phase 1: Download snapshot from S3
        with logger.operation_timer("snapshot_download"):
            from receipt_chroma.s3 import download_snapshot_atomic

            download_result = download_snapshot_atomic(
                bucket=bucket,
                collection=collection.value,
                local_path=temp_dir,
                verify_integrity=True,
            )

        if download_result.get("status") == "error":
            logger.error(
                "Snapshot download failed",
                error=download_result.get("error"),
            )
            if metrics:
                metrics.count("CompactionSnapshotDownloadError", 1)
            return {
                "failed_message_ids": [m.stream_record_id for m in messages]
            }

        logger.info(
            "Snapshot downloaded",
            version=download_result.get("version"),
        )

        # Phase 2: Open ChromaDB client and apply updates
        with logger.operation_timer("in_memory_updates"):
            client = ChromaClient(persist_directory=temp_dir, mode="write")

            # Call receipt_chroma package for business logic
            result = process_collection_updates(
                stream_messages=messages,
                collection=collection,
                chroma_client=client,
                logger=logger,
                metrics=metrics,
                dynamo_client=dynamo_client,
            )

            client.close()

        logger.info(
            "Updates applied",
            metadata_updates=result.total_metadata_updated,
            label_updates=result.total_labels_updated,
            delta_merges=result.delta_merge_count,
            has_errors=result.has_errors,
        )

        # Track metrics from result
        if metrics:
            metrics.gauge(
                "CompactionMetadataUpdatedRecords",
                result.total_metadata_updated,
            )
            metrics.gauge(
                "CompactionLabelsUpdatedRecords", result.total_labels_updated
            )
            metrics.gauge("CompactionDeltaMergeCount", result.delta_merge_count)
            if result.has_errors:
                metrics.count("CompactionProcessingErrors", 1)

        # If errors occurred, determine failed messages
        failed_message_ids = []
        if result.has_errors:
            # Mark messages with errors for retry
            for meta_result in result.metadata_updates:
                if meta_result.error:
                    # Find corresponding message
                    for msg in messages:
                        if (
                            msg.entity_data.get("image_id")
                            == meta_result.image_id
                            and msg.entity_data.get("receipt_id")
                            == meta_result.receipt_id
                        ):
                            failed_message_ids.append(msg.stream_record_id)

            for label_result in result.label_updates:
                if label_result.error:
                    # Find corresponding message
                    for msg in messages:
                        entity_data = msg.entity_data
                        if (
                            entity_data.get("image_id")
                            and entity_data.get("word_id")
                            and label_result.chromadb_id.startswith(
                                f"IMAGE#{entity_data['image_id']}"
                            )
                        ):
                            failed_message_ids.append(msg.stream_record_id)

        # Phase 3: Upload snapshot atomically with lock
        with logger.operation_timer("snapshot_upload"):
            from receipt_chroma.s3 import upload_snapshot_atomic

            upload_result = upload_snapshot_atomic(
                local_path=temp_dir,
                bucket=bucket,
                collection=collection.value,
                lock_manager=lock_manager,
                metadata={
                    "update_type": "batch_compaction",
                    "message_count": len(messages),
                    "metadata_updates": result.total_metadata_updated,
                    "label_updates": result.total_labels_updated,
                    "delta_merges": result.delta_merge_count,
                },
            )

        if upload_result.get("status") == "error":
            logger.error(
                "Snapshot upload failed", error=upload_result.get("error")
            )
            if metrics:
                metrics.count("CompactionSnapshotUploadError", 1)
            return {
                "failed_message_ids": [m.stream_record_id for m in messages]
            }

        logger.info(
            "Snapshot uploaded",
            new_version=upload_result.get("version_id"),
            promoted=upload_result.get("promoted"),
        )

        return {
            "status": "success",
            "result": result,
            "failed_message_ids": failed_message_ids,
        }

    finally:
        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def process_sqs_messages(
    records: List[Dict[str, Any]], logger: Any, metrics: Any = None
) -> Dict[str, Any]:
    """Parse SQS messages and route to collection processors.

    Args:
        records: List of SQS record dicts
        logger: Logger instance
        metrics: Optional metrics accumulator

    Returns:
        Dict with batchItemFailures for partial batch retry
    """
    # Parse StreamMessage objects from SQS records
    try:
        stream_messages = parse_sqs_messages(records)
    except Exception as e:
        logger.error("Failed to parse SQS messages", error=str(e))
        if metrics:
            metrics.count("CompactionMessageParseError", 1)
        # Return all as failures for retry
        return {
            "batchItemFailures": [
                {"itemIdentifier": r["messageId"]} for r in records
            ]
        }

    if not stream_messages:
        logger.warning("No valid messages parsed from SQS records")
        return {"batchItemFailures": []}

    # Track metrics
    if metrics:
        metrics.count("CompactionStreamMessage", len(stream_messages))

    # Group messages by collection
    messages_by_collection = defaultdict(list)
    for msg in stream_messages:
        for collection in msg.collections:
            messages_by_collection[collection].append(msg)

    logger.info(
        "Grouped messages by collection",
        collections=[c.value for c in messages_by_collection.keys()],
        total_messages=len(stream_messages),
    )

    # Process each collection
    failed_message_ids = []
    for collection, msgs in messages_by_collection.items():
        try:
            result = process_collection(
                collection=collection,
                messages=msgs,
                logger=logger,
                metrics=metrics,
            )
            # Collect failures
            if result.get("failed_message_ids"):
                failed_message_ids.extend(result["failed_message_ids"])
        except Exception as e:
            logger.error(
                "Collection processing failed",
                collection=collection.value,
                error=str(e),
                exc_info=True,
            )
            if metrics:
                metrics.count("CompactionCollectionProcessingError", 1)
            # Mark all messages for this collection as failed
            failed_message_ids.extend([m.stream_record_id for m in msgs])

    return {
        "batchItemFailures": [
            {"itemIdentifier": msg_id} for msg_id in failed_message_ids
        ]
    }


@trace_function(operation_name="enhanced_compaction_handler")
@with_compaction_timeout_protection(max_duration=840)  # 14 minutes
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Simplified Lambda handler using receipt_chroma package.

    Routes SQS messages to appropriate collection processors.
    """
    # Configure receipt_chroma loggers
    configure_receipt_chroma_loggers()

    # Start monitoring
    start_compaction_lambda_monitoring(context)
    correlation_id = getattr(logger, "correlation_id", None)

    logger.info(
        "Enhanced compaction handler started",
        event_keys=list(event.keys()),
        correlation_id=correlation_id,
    )

    start_time = time.time()

    # Collect metrics for batch EMF logging (cost-effective)
    collected_metrics: Dict[str, Any] = {}
    error_types: Dict[str, int] = {}

    # Create metrics accumulator
    metrics_accumulator = MetricsAccumulator(collected_metrics)

    try:
        # Check if this is an SQS trigger
        if "Records" in event:
            # Collect metrics
            collected_metrics["CompactionRecordsReceived"] = len(
                event["Records"]
            )

            # Process SQS messages
            result = process_sqs_messages(
                records=event["Records"],
                logger=logger,
                metrics=metrics_accumulator,
            )

            # Handle partial batch failures
            if isinstance(result, dict) and "batchItemFailures" in result:
                # Emit metrics before returning
                execution_time = time.time() - start_time
                collected_metrics["CompactionLambdaExecutionTime"] = (
                    execution_time
                )
                if result["batchItemFailures"]:
                    collected_metrics["CompactionPartialBatchFailure"] = 1
                    collected_metrics["CompactionFailedMessages"] = len(
                        result["batchItemFailures"]
                    )
                else:
                    collected_metrics["CompactionLambdaSuccess"] = 1

                # Emit metrics via EMF
                emf_metrics.log_metrics(
                    collected_metrics,
                    properties={
                        "error_types": error_types,
                        "correlation_id": correlation_id,
                    },
                )

                logger.info(
                    "Enhanced compaction handler completed",
                    execution_time_seconds=execution_time,
                    failed_messages=len(result.get("batchItemFailures", [])),
                )

                return result

            # Track successful execution
            execution_time = time.time() - start_time
            collected_metrics["CompactionLambdaExecutionTime"] = execution_time
            collected_metrics["CompactionLambdaSuccess"] = 1

            logger.info(
                "Enhanced compaction handler completed successfully",
                execution_time_seconds=execution_time,
            )

            # Emit metrics via EMF
            emf_metrics.log_metrics(
                collected_metrics,
                properties={
                    "error_types": error_types,
                    "correlation_id": correlation_id,
                },
            )

            return format_response(result, event)

        # Direct invocation not supported
        logger.warning("Direct invocation not supported")
        collected_metrics["CompactionDirectInvocationAttempt"] = 1

        response = LambdaResponse(
            status_code=400,
            error="Direct invocation not supported",
            message="This Lambda processes SQS messages from DynamoDB streams",
        )

        # Emit metrics
        execution_time = time.time() - start_time
        collected_metrics["CompactionLambdaExecutionTime"] = execution_time
        emf_metrics.log_metrics(collected_metrics)

        return format_response(response.to_dict(), event, is_error=True)

    except Exception as e:
        execution_time = time.time() - start_time
        error_type = type(e).__name__
        error_types[error_type] = error_types.get(error_type, 0) + 1

        logger.error(
            "Enhanced compaction handler failed",
            error=str(e),
            error_type=error_type,
            execution_time_seconds=execution_time,
            exc_info=True,
        )

        collected_metrics["CompactionLambdaError"] = 1
        collected_metrics["CompactionLambdaExecutionTime"] = execution_time

        # Emit error metrics
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions={"error_type": error_type} if error_type else None,
            properties={
                "error_types": error_types,
                "correlation_id": correlation_id,
                "error": str(e),
            },
        )

        error_response = LambdaResponse(
            status_code=500,
            message=f"Compaction handler failed: {str(e)}",
            error=str(e),
        )

        return format_response(error_response.to_dict(), event, is_error=True)

    finally:
        # Stop monitoring
        stop_compaction_lambda_monitoring()
