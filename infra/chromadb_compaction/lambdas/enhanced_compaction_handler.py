"""Simplified ChromaDB compaction handler using receipt_chroma package.

This handler processes DynamoDB stream messages for ChromaDB compaction by:
1. Receiving SQS messages from DynamoDB streams
2. Categorizing messages by collection (lines/words)
3. Using receipt_chroma.compaction for business logic
4. Maintaining EMF metrics and structured logging

Business logic lives in receipt_chroma package for reusability.

Lambda Bundling:
    This Lambda is bundled by Pulumi with a specific directory structure:
    - `utils/` is copied directly into the Lambda package (local module)
    - `receipt_chroma`, `receipt_dynamo`, `receipt_dynamo_stream` are installed
      from the monorepo as pip packages
    - Third-party deps are installed via requirements.txt

    When running pylint locally, these imports may fail because:
    - `utils` is not on PYTHONPATH (it's relative to Lambda root)
    - Monorepo packages may not be installed in the local venv

    The Lambda runtime has all dependencies available via the bundled package.
"""

# pylint: disable=duplicate-code,import-error,no-name-in-module,wrong-import-order
# duplicate-code: Some patterns shared with stream_processor
# import-error: utils is bundled into Lambda package, not available locally
# no-name-in-module: monorepo packages (receipt_chroma, receipt_dynamo) are
#   installed at Lambda runtime but may not be in local venv
# wrong-import-order: utils is local to Lambda, not third-party
import gc
import json
import logging
import os
import shutil
import tempfile
import time
from collections import defaultdict
from typing import Any, Dict, List

# Use receipt_chroma package for compaction logic
from receipt_chroma import ChromaClient, LockManager
from receipt_chroma.compaction import process_collection_updates
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient

# Import StreamMessage from receipt_dynamo_stream
try:
    from receipt_dynamo_stream.models import StreamMessage
except ImportError:
    # Fallback for testing
    # pylint: disable-next=too-few-public-methods
    class StreamMessage:  # type: ignore[no-redef]
        """Fallback StreamMessage for testing."""

        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

# Enhanced observability imports
from utils import (
    CollectionProcessingResult,
    MetricsAccumulator,
    cleanup_manual_messages,
    emf_metrics,
    fetch_phase2_messages,
    format_response,
    get_operation_logger,
    start_compaction_lambda_monitoring,
    stop_compaction_lambda_monitoring,
    trace_function,
    with_compaction_timeout_protection,
)


# Get logger instance
logger = get_operation_logger(__name__)


# pylint: disable-next=too-few-public-methods
class LambdaResponse:
    """Lambda response wrapper for consistent formatting."""

    def __init__(self, status_code: int, message: str, **kwargs):
        self.status_code = status_code
        self.message = message
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary for Lambda return value."""
        result = {"statusCode": self.status_code, "message": self.message}
        for key, value in self.__dict__.items():
            if key not in ["status_code", "message"] and value is not None:
                result[key] = value
        return result


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
            # pylint: disable=import-outside-toplevel
            from utils.logging import StructuredFormatter  # optional fallback

            formatter = StructuredFormatter()
        except ImportError:
            # Fallback to simple format
            fmt = "[%(levelname)s] %(asctime)s %(name)s - %(message)s"
            formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")

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

        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
            logger.warning(
                "Failed to parse SQS message: %s",
                type(e).__name__,
                extra={"message_id": record.get("messageId")},
            )
            # Skip invalid messages
            continue

    return messages


def _process_collections(
    messages_by_collection: Dict[Any, List[StreamMessage]],
    op_logger: Any,
    metrics: Any = None,
) -> CollectionProcessingResult:
    """Process each collection and collect failures.

    Args:
        messages_by_collection: Messages grouped by collection
        op_logger: Logger instance
        metrics: Optional metrics accumulator

    Returns:
        CollectionProcessingResult with failures and success status
    """
    failed_message_ids: List[str] = []
    processing_successful = True

    for collection, msgs in messages_by_collection.items():
        try:
            result = process_collection(
                collection=collection,
                messages=msgs,
                op_logger=op_logger,
                metrics=metrics,
            )
            if result.get("failed_message_ids"):
                failed_message_ids.extend(result["failed_message_ids"])
                processing_successful = False
        # Broad exception needed: process_collection can fail from S3,
        # DynamoDB, ChromaDB, or file system errors
        except Exception:  # pylint: disable=broad-exception-caught
            op_logger.exception(
                "Collection processing failed",
                collection=collection.value,
            )
            if metrics:
                metrics.count("CompactionCollectionProcessingError", 1)
            failed_message_ids.extend([m.stream_record_id for m in msgs])
            processing_successful = False

    return CollectionProcessingResult(
        failed_message_ids, processing_successful
    )


def _collect_failed_message_ids(
    result: Any, messages: List[StreamMessage]
) -> List[str]:
    """Collect message IDs for failed processing operations.

    Args:
        result: CompactionResult with metadata_updates and label_updates
        messages: Original stream messages to match against

    Returns:
        List of stream record IDs for messages that failed processing
    """
    failed_ids: List[str] = []

    # Check metadata update failures
    for meta_result in result.metadata_updates:
        if meta_result.error:
            for msg in messages:
                entity_data = msg.entity_data
                if (
                    entity_data.get("image_id") == meta_result.image_id
                    and entity_data.get("receipt_id") == meta_result.receipt_id
                ):
                    failed_ids.append(msg.stream_record_id)

    # Check label update failures
    for label_result in result.label_updates:
        if label_result.error:
            for msg in messages:
                entity_data = msg.entity_data
                image_id = entity_data.get("image_id")
                chromadb_prefix = f"IMAGE#{image_id}"
                if (
                    image_id is not None
                    and entity_data.get("word_id") is not None
                    and label_result.chromadb_id.startswith(chromadb_prefix)
                ):
                    failed_ids.append(msg.stream_record_id)

    return failed_ids


# Local variables are inherent to 3-phase atomic operation (lock, download,
# update, upload) - splitting would break transactional semantics
def process_collection(  # pylint: disable=too-many-locals
    collection: ChromaDBCollection,
    messages: List[StreamMessage],
    op_logger: Any,
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
        op_logger: OperationLogger instance (named to avoid shadowing)
        metrics: MetricsAccumulator for EMF logging

    Returns:
        Dict with processing results and failed message IDs
    """
    # Environment configuration
    bucket = os.environ["CHROMADB_BUCKET"]
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    op_logger.info(
        "Processing collection",
        collection=collection.value,
        message_count=len(messages),
    )

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Initialize lock manager
    lock_manager = LockManager(
        dynamo_client=dynamo_client,
        collection=collection,
    )

    # Acquire lock for atomic snapshot upload
    lock_id = f"chroma-{collection.value}-compaction"
    if not lock_manager.acquire(lock_id):
        op_logger.error("Failed to acquire lock for snapshot upload")
        if metrics:
            metrics.count("CompactionLockAcquisitionFailed", 1)
        return {"failed_message_ids": [m.stream_record_id for m in messages]}

    # Create temp directory for snapshot
    temp_dir = tempfile.mkdtemp(prefix=f"chroma-{collection.value}-")

    try:
        # Phase 1: Download snapshot from S3
        with op_logger.operation_timer("snapshot_download"):
            download_result = download_snapshot_atomic(
                bucket=bucket,
                collection=collection.value,
                local_path=temp_dir,
                verify_integrity=True,
            )

        if download_result.get("status") == "error":
            op_logger.error(
                "Snapshot download failed",
                error=download_result.get("error"),
            )
            if metrics:
                metrics.count("CompactionSnapshotDownloadError", 1)
            return {
                "failed_message_ids": [m.stream_record_id for m in messages]
            }

        op_logger.info(
            "Snapshot downloaded",
            version=download_result.get("version"),
        )

        # Phase 2: Open ChromaDB client and apply updates
        with op_logger.operation_timer("in_memory_updates"):
            with ChromaClient(
                persist_directory=temp_dir, mode="write", metadata_only=True
            ) as client:
                # Call receipt_chroma package for business logic
                result = process_collection_updates(
                    stream_messages=messages,
                    collection=collection,
                    chroma_client=client,
                    logger=op_logger,
                    metrics=metrics,
                    dynamo_client=dynamo_client,
                )

        op_logger.info(
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
            metrics.gauge(
                "CompactionDeltaMergeCount", result.delta_merge_count
            )
            if result.has_errors:
                metrics.count("CompactionProcessingErrors", 1)

        # Collect failed message IDs for retry
        failed_message_ids = (
            _collect_failed_message_ids(result, messages)
            if result.has_errors
            else []
        )

        # Phase 3: Upload snapshot atomically with lock
        with op_logger.operation_timer("snapshot_upload"):
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
            op_logger.error(
                "Snapshot upload failed", error=upload_result.get("error")
            )
            if metrics:
                metrics.count("CompactionSnapshotUploadError", 1)
            return {
                "failed_message_ids": [m.stream_record_id for m in messages]
            }

        op_logger.info(
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
        # Release lock if acquired
        try:
            lock_manager.release()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Log but don't fail - lock will expire naturally
            op_logger.debug(
                "Failed to release lock during cleanup", error=str(e)
            )

        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Aggressive memory cleanup to prevent OOM on warm container reuse
        # (repeated gc.collect() releases cyclic refs with finalizers).
        gc.collect()
        gc.collect()
        gc.collect()


def process_sqs_messages(
    records: List[Dict[str, Any]], op_logger: Any, metrics: Any = None
) -> Dict[str, Any]:
    """Parse SQS messages and route to collection processors.

    Phase 2 optimization: After receiving initial batch from Lambda trigger,
    greedily fetch more messages from the same queue to process in one
    compaction cycle, amortizing S3 transfer costs.

    Args:
        records: List of SQS record dicts
        op_logger: Logger instance (named to avoid shadowing module logger)
        metrics: Optional metrics accumulator

    Returns:
        Dict with batchItemFailures for partial batch retry
    """
    # Phase 2: Fetch additional messages to batch with initial records
    phase2 = fetch_phase2_messages(records, op_logger, metrics)
    records = phase2.all_records

    # Parse StreamMessage objects from SQS records
    try:
        stream_messages = parse_sqs_messages(records)
    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
        op_logger.exception("Failed to parse SQS messages")
        if metrics:
            metrics.count("CompactionMessageParseError", 1)
        # Return all as failures for retry
        # Note: manually fetched messages will become visible again after
        # visibility timeout expires
        return {
            "batchItemFailures": [
                {"itemIdentifier": r["messageId"]} for r in records
            ]
        }

    if not stream_messages:
        op_logger.warning("No valid messages parsed from SQS records")
        return {"batchItemFailures": []}

    # Track metrics
    if metrics:
        metrics.count("CompactionStreamMessage", len(stream_messages))

    # Group messages by collection
    messages_by_collection = defaultdict(list)
    for msg in stream_messages:
        for collection in msg.collections:
            messages_by_collection[collection].append(msg)

    op_logger.info(
        "Grouped messages by collection",
        collections=[c.value for c in messages_by_collection.keys()],
        total_messages=len(stream_messages),
    )

    # Process each collection
    result = _process_collections(messages_by_collection, op_logger, metrics)

    # Clean up manually-fetched messages
    cleanup_manual_messages(phase2, result, op_logger, metrics)

    return {
        "batchItemFailures": [
            {"itemIdentifier": msg_id}
            for msg_id in result.failed_message_ids
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
                op_logger=logger,
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

    # pylint: disable-next=broad-exception-caught
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

        # Final memory cleanup before warm container reuse
        # This ensures ChromaDB data doesn't accumulate across invocations
        gc.collect()
        gc.collect()
        gc.collect()
