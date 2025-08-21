"""X-Ray distributed tracing for ChromaDB compaction operations."""

import os
from functools import wraps
from contextlib import contextmanager
from typing import Optional, Dict, Any

try:
    from aws_xray_sdk.core import xray_recorder
    from aws_xray_sdk.core import patch_all
    XRAY_AVAILABLE = True
except ImportError:
    XRAY_AVAILABLE = False
    xray_recorder = None

from .logging import get_operation_logger


# Patch AWS SDK calls for tracing if X-Ray is available
if XRAY_AVAILABLE and os.environ.get("ENABLE_XRAY", "true").lower() == "true":
    try:
        patch_all()
    except Exception:
        # Ignore patching errors in local development
        pass


logger = get_operation_logger(__name__)


def is_tracing_enabled() -> bool:
    """Check if X-Ray tracing is enabled and available."""
    return (
        XRAY_AVAILABLE
        and xray_recorder is not None
        and os.environ.get("ENABLE_XRAY", "true").lower() == "true"
    )


@contextmanager
def trace_compaction_operation(
    operation_name: str,
    collection: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Context manager for tracing compaction operations.

    Args:
        operation_name: Name of the operation to trace
        collection: ChromaDB collection name
        metadata: Additional metadata to add to the trace
    """
    if not is_tracing_enabled():
        # No-op context manager if tracing is disabled
        yield
        return

    try:
        with xray_recorder.in_subsegment(f"compaction_{operation_name}") as subsegment:
            # Add operation metadata
            if subsegment:
                subsegment.put_annotation("operation", operation_name)
                if collection:
                    subsegment.put_annotation("collection", collection)

                # Add custom metadata
                if metadata:
                    for key, value in metadata.items():
                        if isinstance(value, (str, int, float, bool)):
                            subsegment.put_metadata("compaction", {key: value})

            yield subsegment

    except Exception as e:
        logger.warning("X-Ray tracing error", error=str(e), operation=operation_name)
        yield None


def trace_function(
    operation_name: Optional[str] = None, 
    collection: Optional[str] = None
):
    """Decorator to trace function execution.

    Args:
        operation_name: Custom operation name (defaults to function name)
        collection: ChromaDB collection name
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            
            with trace_compaction_operation(op_name, collection):
                return func(*args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def trace_s3_operation(operation_type: str, bucket: str, key: str):
    """Trace S3 operations with specific metadata.

    Args:
        operation_type: Type of S3 operation (upload, download, delete)
        bucket: S3 bucket name
        key: S3 object key
    """
    metadata = {"bucket": bucket, "key": key, "operation_type": operation_type}

    with trace_compaction_operation(f"s3_{operation_type}", metadata=metadata) as segment:
        if segment and is_tracing_enabled():
            segment.put_annotation("service", "s3")
            segment.put_annotation("bucket", bucket)

        yield segment


@contextmanager
def trace_chromadb_operation(
    operation_type: str, collection: str, record_count: Optional[int] = None
):
    """Trace ChromaDB operations with specific metadata.

    Args:
        operation_type: Type of ChromaDB operation (update, query, add, delete)
        collection: ChromaDB collection name
        record_count: Number of records involved
    """
    metadata = {"operation_type": operation_type, "collection": collection}
    if record_count is not None:
        metadata["record_count"] = record_count

    with trace_compaction_operation(
        f"chromadb_{operation_type}", collection, metadata
    ) as segment:
        if segment and is_tracing_enabled():
            segment.put_annotation("service", "chromadb")
            segment.put_annotation("operation_type", operation_type)

        yield segment


@contextmanager
def trace_stream_processing(event_name: str, collection: str):
    """Trace DynamoDB stream processing operations.

    Args:
        event_name: DynamoDB event name (INSERT, MODIFY, REMOVE)
        collection: Target ChromaDB collection
    """
    metadata = {"event_name": event_name, "collection": collection}

    with trace_compaction_operation(
        f"stream_{event_name.lower()}", collection, metadata
    ) as segment:
        if segment and is_tracing_enabled():
            segment.put_annotation("service", "dynamodb_stream")
            segment.put_annotation("event_name", event_name)

        yield segment


def add_trace_metadata(key: str, value: Any):
    """Add metadata to the current trace segment.

    Args:
        key: Metadata key
        value: Metadata value
    """
    if not is_tracing_enabled():
        return

    try:
        current_segment = xray_recorder.current_segment()
        if current_segment:
            current_segment.put_metadata("compaction", {key: value})
    except Exception as e:
        logger.debug("Failed to add trace metadata", key=key, error=str(e))


def add_trace_annotation(key: str, value: str):
    """Add annotation to the current trace segment.

    Args:
        key: Annotation key
        value: Annotation value (must be string)
    """
    if not is_tracing_enabled():
        return

    try:
        current_segment = xray_recorder.current_segment()
        if current_segment:
            current_segment.put_annotation(key, str(value))
    except Exception as e:
        logger.debug("Failed to add trace annotation", key=key, error=str(e))


# Convenience functions for common tracing patterns

def trace_lambda_handler(handler_func):
    """Decorator to trace Lambda handler functions."""
    return trace_function("lambda_handler")(handler_func)


@contextmanager
def trace_batch_processing(batch_size: int, collection: str):
    """Trace batch processing operations."""
    metadata = {"batch_size": batch_size, "collection": collection}
    
    with trace_compaction_operation(
        "batch_processing", collection, metadata
    ) as segment:
        if segment and is_tracing_enabled():
            segment.put_annotation("batch_size", str(batch_size))
        
        yield segment