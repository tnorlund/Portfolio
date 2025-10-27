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
import shutil
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
from receipt_label.utils.lock_manager import LockManager

# Import modular components - flexible for both Lambda and test environments
print("🔍 DEBUG: Starting import attempt...")
print(f"🔍 DEBUG: Current working directory: {os.getcwd()}")
print(f"🔍 DEBUG: Python path: {os.sys.path}")
print(f"🔍 DEBUG: Lambda task root: {os.environ.get('LAMBDA_TASK_ROOT', 'NOT_SET')}")

# Check if compaction directory exists
lambda_task_root = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
compaction_path = os.path.join(lambda_task_root, 'compaction')
print(f"🔍 DEBUG: Checking if compaction directory exists at: {compaction_path}")
if os.path.exists(compaction_path):
    print("🔍 DEBUG: ✅ Compaction directory exists")
    print(f"🔍 DEBUG: Compaction directory contents: {os.listdir(compaction_path)}")
else:
    print("🔍 DEBUG: ❌ Compaction directory does not exist")
    print(f"🔍 DEBUG: Lambda task root contents: {os.listdir(lambda_task_root)}")

try:
    # Try absolute import first (Lambda environment)
    print("🔍 DEBUG: Attempting absolute import from 'compaction'...")
    from compaction import (
        process_sqs_messages,
        categorize_stream_messages,
        group_messages_by_collection,
        process_metadata_updates,
        process_label_updates,
        process_compaction_run_messages,
        merge_compaction_deltas,
        apply_metadata_updates_in_memory,
        apply_label_updates_in_memory,
        LambdaResponse,
        StreamMessage,
        MetadataUpdateResult,
        LabelUpdateResult,
        get_efs_snapshot_manager,
    )
    MODULAR_MODE = True
    print("✅ Modular mode: Using compaction package")
except ImportError as e:
    print(f"🔍 DEBUG: Absolute import failed with error: {e}")
    print(f"🔍 DEBUG: Error type: {type(e)}")
    try:
        # Try relative import (test environment)
        print("🔍 DEBUG: Attempting relative import from '.compaction'...")
        from .compaction import (
            process_sqs_messages,
            categorize_stream_messages,
            group_messages_by_collection,
            process_metadata_updates,
            process_label_updates,
            process_compaction_run_messages,
            merge_compaction_deltas,
            apply_metadata_updates_in_memory,
            apply_label_updates_in_memory,
            LambdaResponse,
            StreamMessage,
            MetadataUpdateResult,
            LabelUpdateResult,
            get_efs_snapshot_manager,
        )
        MODULAR_MODE = True
        print("✅ Modular mode: Using compaction package (relative import)")
    except ImportError as e2:
        print(f"🔍 DEBUG: Relative import failed with error: {e2}")
        print(f"🔍 DEBUG: Error type: {type(e2)}")
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
# Optimized lock parameters for performance
heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "120"))  # 2 minutes (reduced frequency)
lock_duration_minutes = int(os.environ.get("LOCK_DURATION_MINUTES", "1"))      # 1 minute (reduced duration)
max_heartbeat_failures = int(os.environ.get("MAX_HEARTBEAT_FAILURES", "2"))     # 2 failures (faster recovery)
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
    failed_receipt_handles: List[str] = []

    for collection, msgs in messages_by_collection.items():
        metadata_msgs, label_msgs, compaction_run_msgs = categorize_stream_messages(
            msgs
        )

        # Phase A: off-lock snapshot download/open once
        import tempfile
        from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic, upload_snapshot_atomic
        from receipt_label.utils.chroma_client import ChromaDBClient

        # Storage mode configuration - easily switchable
        storage_mode = os.environ.get("CHROMADB_STORAGE_MODE", "auto").lower()
        efs_root = os.environ.get("CHROMA_ROOT")
        
        # Determine storage mode
        if storage_mode == "s3":
            use_efs = False
            mode_reason = "explicitly set to S3-only"
        elif storage_mode == "efs":
            use_efs = True
            mode_reason = "explicitly set to EFS"
        elif storage_mode == "auto":
            # Auto-detect based on EFS availability
            use_efs = efs_root and efs_root != "/tmp/chroma"
            mode_reason = f"auto-detected (efs_root={'available' if use_efs else 'not available'})"
        else:
            # Default to S3-only for unknown modes
            use_efs = False
            mode_reason = f"unknown mode '{storage_mode}', defaulting to S3-only"
        
        logger.info(
            "Storage mode configuration",
            storage_mode=storage_mode,
            efs_root=efs_root,
            use_efs=use_efs,
            mode_reason=mode_reason,
            collection=collection.value
        )
        
        if use_efs:
            logger.info("Using EFS + S3 hybrid approach", collection=collection.value)
            # Use EFS + S3 hybrid approach with optimized locking
            efs_manager = get_efs_snapshot_manager(collection.value, logger, metrics)
            
            # Phase A: Optimized lock strategy - minimal lock time
            lock_id = f"chroma-{collection.value}-update"
            lm = LockManager(
                get_dynamo_client(),
                collection=collection,
                heartbeat_interval=heartbeat_interval,
                lock_duration_minutes=lock_duration_minutes,
                max_heartbeat_failures=max_heartbeat_failures,
            )
            
            try:
                # Step 1: Read operations OFF-LOCK (optimization)
                latest_version = efs_manager.get_latest_s3_version()
                if not latest_version:
                    logger.error("Failed to get latest S3 version", collection=collection.value)
                    failed_receipt_handles.extend(
                        [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                    )
                    continue
                
                snapshot_result = efs_manager.ensure_snapshot_available(latest_version)
                if snapshot_result["status"] != "available":
                    logger.error("Failed to ensure snapshot availability", result=snapshot_result)
                    failed_receipt_handles.extend(
                        [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                    )
                    continue
                
                # Step 2: Quick CAS validation (minimal lock time)
                phase1_lock_start = time.time()
                if not lm.acquire(lock_id):
                    # Track lock collision in Phase 1
                    if metrics:
                        metrics.count(
                            "CompactionLockCollision",
                            1,
                            {"phase": "1", "collection": collection.value, "type": "validation"}
                        )
                    logger.info(
                        "Lock busy during validation, skipping",
                        collection=collection.value,
                        message_count=len(msgs)
                    )
                    failed_receipt_handles.extend(
                        [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                    )
                    continue
                
                phase1_lock_duration = time.time() - phase1_lock_start
                if metrics:
                    metrics.put_metric(
                        "CompactionLockDuration",
                        phase1_lock_duration * 1000,  # Convert to milliseconds
                        unit="Milliseconds",
                        dimensions={"phase": "1", "collection": collection.value, "type": "validation"}
                    )
                
                try:
                    lm.start_heartbeat()
                    # CAS: Validate pointer hasn't changed since we read it
                    # (Quick validation under lock, then release for heavy I/O)
                    pointer_key = f"{collection.value}/snapshot/latest-pointer.txt"
                    bucket = os.environ["CHROMADB_BUCKET"]
                    import boto3 as _boto
                    s3_client = _boto.client("s3")
                    try:
                        resp = s3_client.get_object(Bucket=bucket, Key=pointer_key)
                        current_pointer = resp["Body"].read().decode("utf-8").strip()
                    except Exception:
                        current_pointer = "latest-direct"
                    
                    if latest_version != current_pointer:
                        logger.info(
                            "Pointer drift detected during initial validation",
                            collection=collection.value,
                            expected=latest_version,
                            current=current_pointer,
                        )
                        failed_receipt_handles.extend(
                            [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                        )
                        continue
                finally:
                    lm.stop_heartbeat()
                    lm.release()
                
                # Store expected pointer for Phase 3 validation
                expected_pointer = latest_version
                
                # Step 3: Perform heavy operations off-lock
                efs_snapshot_path = snapshot_result["efs_path"]
                local_snapshot_path = tempfile.mkdtemp()
                
                copy_start_time = time.time()
                shutil.copytree(efs_snapshot_path, local_snapshot_path, dirs_exist_ok=True)
                copy_time_ms = (time.time() - copy_start_time) * 1000
                
                snapshot_path = local_snapshot_path
                
                logger.info(
                    "Using EFS snapshot (copied to local)",
                    collection=collection.value,
                    version=latest_version,
                    efs_path=efs_snapshot_path,
                    local_path=local_snapshot_path,
                    copy_time_ms=copy_time_ms,
                    source=snapshot_result.get("source", "unknown")
                )
            except Exception as e:
                logger.error("Failed during EFS setup", error=str(e), collection=collection.value)
                failed_receipt_handles.extend(
                    [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                )
                continue
        else:
            logger.info("Using S3-only approach", collection=collection.value, mode_reason=mode_reason)
            # Fallback to S3-only approach
            temp_dir = tempfile.mkdtemp()
            bucket = os.environ["CHROMADB_BUCKET"]
            dl = download_snapshot_atomic(
                bucket=bucket,
                collection=collection.value,
                local_path=temp_dir,
                verify_integrity=True,
            )
            if dl.get("status") != "downloaded":
                logger.error("Failed to download snapshot", result=dl)
                failed_receipt_handles.extend(
                    [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                )
                continue

            snapshot_path = temp_dir
            expected_pointer = dl.get("version_id")
            
            logger.info(
                "Using S3 snapshot",
                collection=collection.value,
                version=expected_pointer,
                temp_path=snapshot_path
            )

        # Initialize ChromaDB client
        chroma_client = ChromaDBClient(persist_directory=snapshot_path, mode="snapshot")
        
        try:
            # Merge deltas first
            if compaction_run_msgs:
                merged = merge_compaction_deltas(
                    chroma_client=chroma_client,
                    compaction_runs=compaction_run_msgs,
                    collection=collection,
                    logger=logger,
                )
                total_compaction_merged += merged

            # Apply metadata updates
            if metadata_msgs:
                md_results = apply_metadata_updates_in_memory(
                    chroma_client=chroma_client,
                    metadata_updates=metadata_msgs,
                    collection=collection,
                    logger=logger,
                    metrics=metrics,
                    get_dynamo_client_func=get_dynamo_client,
                )
                total_metadata_updates += sum(
                    r.updated_count for r in md_results if getattr(r, "error", None) is None
                )

            # Apply label updates
            if label_msgs:
                lb_results = apply_label_updates_in_memory(
                    chroma_client=chroma_client,
                    label_updates=label_msgs,
                    collection=collection,
                    logger=logger,
                    metrics=metrics,
                    get_dynamo_client_func=get_dynamo_client,
                )
                total_label_updates += sum(
                    r.updated_count for r in lb_results if getattr(r, "error", None) is None
                )

            # Phase B: Optimized upload with minimal lock time
            published = False
            import boto3 as _boto
            s3_client = _boto.client("s3")
            
            if use_efs:
                # EFS case: Re-acquire lock only for critical S3 operations
                backoff_attempts = [0.1, 0.2]  # Shorter backoff for EFS case
                
                for attempt_idx, delay in enumerate(backoff_attempts, start=1):
                    phase3_lock_start = time.time()
                    if not lm.acquire(lock_id):
                        # Track lock collision in Phase 3
                        phase3_lock_duration = time.time() - phase3_lock_start
                        if metrics:
                            metrics.count(
                                "CompactionLockCollision",
                                1,
                                {"phase": "3", "collection": collection.value, "type": "upload", "attempt": str(attempt_idx)}
                            )
                            metrics.put_metric(
                                "CompactionLockWaitTime",
                                (delay + phase3_lock_duration) * 1000,
                                unit="Milliseconds",
                                dimensions={"phase": "3", "collection": collection.value, "type": "backoff"}
                            )
                        logger.info(
                            "Lock busy during upload, backing off",
                            collection=collection.value,
                            attempt=attempt_idx,
                            delay_ms=int(delay * 1000),
                        )
                        time.sleep(delay)
                        continue
                    
                    phase3_lock_duration = time.time() - phase3_lock_start
                    if metrics:
                        metrics.put_metric(
                            "CompactionLockDuration",
                            phase3_lock_duration * 1000,  # Convert to milliseconds
                            unit="Milliseconds",
                            dimensions={"phase": "3", "collection": collection.value, "type": "upload"}
                        )
                    
                    try:
                        lm.start_heartbeat()
                        
                        # CAS: re-read pointer and compare
                        pointer_key = f"{collection.value}/snapshot/latest-pointer.txt"
                        bucket = os.environ["CHROMADB_BUCKET"]
                        try:
                            resp = s3_client.get_object(Bucket=bucket, Key=pointer_key)
                            current_pointer = resp["Body"].read().decode("utf-8").strip()
                        except Exception:
                            current_pointer = "latest-direct"

                        if expected_pointer and current_pointer != expected_pointer:
                            logger.info(
                                "Pointer drift detected, skipping upload",
                                collection=collection.value,
                                expected=expected_pointer,
                                current=current_pointer,
                            )
                            failed_receipt_handles.extend(
                                [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                            )
                            break

                        up = upload_snapshot_atomic(
                            local_path=snapshot_path,
                            bucket=bucket,
                            collection=collection.value,
                            lock_manager=lm,
                            metadata={
                                "update_type": "batch_compaction",
                                "expected_pointer": expected_pointer or "",
                            },
                        )
                        if up.get("status") == "uploaded":
                            published = True
                            new_version = up.get("version_id")
                            break
                        else:
                            logger.error("Snapshot upload failed", result=up)
                            time.sleep(delay)
                            continue
                    finally:
                        try:
                            lm.stop_heartbeat()
                        finally:
                            lm.release()
                
                # Update EFS cache AFTER lock release (off-lock optimization)
                if published and new_version:
                    try:
                        # Copy the updated local snapshot back to EFS
                        efs_snapshot_path = os.path.join(efs_manager.efs_snapshots_dir, new_version)
                        if os.path.exists(efs_snapshot_path):
                            shutil.rmtree(efs_snapshot_path)
                        
                        copy_start_time = time.time()
                        shutil.copytree(snapshot_path, efs_snapshot_path)
                        copy_time_ms = (time.time() - copy_start_time) * 1000
                        
                        logger.info(
                            "Updated EFS snapshot (off-lock)",
                            collection=collection.value,
                            version=new_version,
                            efs_path=efs_snapshot_path,
                            copy_time_ms=copy_time_ms
                        )
                        
                        efs_manager.cleanup_old_snapshots()
                    except Exception as e:
                        logger.warning(
                            "Failed to update EFS cache (non-critical)",
                            error=str(e),
                            collection=collection.value,
                            version=new_version
                        )
            else:
                # S3-only case: Standard lock acquisition
                backoff_attempts = [0.15, 0.3]  # seconds
                
                for attempt_idx, delay in enumerate(backoff_attempts, start=1):
                    lock_id = f"chroma-{collection.value}-update"
                    lm = LockManager(
                        get_dynamo_client(),
                        collection=collection,
                        heartbeat_interval=heartbeat_interval,
                        lock_duration_minutes=lock_duration_minutes,
                        max_heartbeat_failures=max_heartbeat_failures,
                    )

                    phase3_s3_lock_start = time.time()
                    if not lm.acquire(lock_id):
                        phase3_s3_lock_duration = time.time() - phase3_s3_lock_start
                        if metrics:
                            metrics.count(
                                "CompactionLockCollision",
                                1,
                                {"phase": "3", "collection": collection.value, "type": "upload_s3", "attempt": str(attempt_idx)}
                            )
                            metrics.put_metric(
                                "CompactionLockWaitTime",
                                (delay + phase3_s3_lock_duration) * 1000,
                                unit="Milliseconds",
                                dimensions={"phase": "3", "collection": collection.value, "type": "backoff_s3"}
                            )
                        logger.info(
                            "Lock busy, backing off",
                            collection=collection.value,
                            attempt=attempt_idx,
                            delay_ms=int(delay * 1000),
                        )
                        time.sleep(delay)
                        continue
                    
                    phase3_s3_lock_duration = time.time() - phase3_s3_lock_start
                    if metrics:
                        metrics.put_metric(
                            "CompactionLockDuration",
                            phase3_s3_lock_duration * 1000,  # Convert to milliseconds
                            unit="Milliseconds",
                            dimensions={"phase": "3", "collection": collection.value, "type": "upload_s3"}
                        )

                    try:
                        lm.start_heartbeat()
                        # CAS: re-read pointer and compare
                        pointer_key = f"{collection.value}/snapshot/latest-pointer.txt"
                        bucket = os.environ["CHROMADB_BUCKET"]
                        try:
                            resp = s3_client.get_object(Bucket=bucket, Key=pointer_key)
                            current_pointer = resp["Body"].read().decode("utf-8").strip()
                        except Exception:
                            current_pointer = "latest-direct"

                        if expected_pointer and current_pointer != expected_pointer:
                            logger.info(
                                "Pointer drift detected, will retry",
                                collection=collection.value,
                                expected=expected_pointer,
                                current=current_pointer,
                            )
                            # Back off and retry
                            time.sleep(delay)
                            continue

                        up = upload_snapshot_atomic(
                            local_path=snapshot_path,
                            bucket=bucket,
                            collection=collection.value,
                            lock_manager=lm,
                            metadata={
                                "update_type": "batch_compaction",
                                "expected_pointer": expected_pointer or "",
                            },
                        )
                        if up.get("status") == "uploaded":
                            published = True
                            break
                        else:
                            logger.error("Snapshot upload failed", result=up)
                            time.sleep(delay)
                            continue
                    finally:
                        try:
                            lm.stop_heartbeat()
                        finally:
                            lm.release()

            if not published:
                # Mark this collection's messages as failed for partial retry
                failed_receipt_handles.extend(
                    [m.receipt_handle for m in msgs if getattr(m, "receipt_handle", None)]
                )
                continue
        finally:
            # Cleanup temp directories
            if use_efs and 'local_snapshot_path' in locals():
                shutil.rmtree(local_snapshot_path, ignore_errors=True)
            elif not use_efs and 'temp_dir' in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

    if failed_receipt_handles:
        return {
            "batchItemFailures": [
                {"itemIdentifier": h} for h in failed_receipt_handles if h
            ]
        }

    return LambdaResponse(
        status_code=200,
        message="Stream messages processed",
        metadata_updates=total_metadata_updates,
        label_updates=total_label_updates,
    ).to_dict()


# Alias for consistent naming with other handlers
handle = lambda_handler