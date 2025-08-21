"""X-Ray tracing utilities for Lambda functions."""

import os
from contextlib import contextmanager
from typing import Optional, Dict, Any
from functools import wraps

try:
    from aws_xray_sdk.core import xray_recorder, patch_all
    from aws_xray_sdk.core.context import Context
    from aws_xray_sdk.core.models.segment import Segment
    from aws_xray_sdk.core.models.subsegment import Subsegment
    XRAY_AVAILABLE = True
except ImportError:
    XRAY_AVAILABLE = False

from .logging import get_operation_logger


class XRayTracer:
    """X-Ray tracing utilities for instrumenting Lambda functions."""

    def __init__(self):
        """Initialize X-Ray tracer."""
        self.enabled = (
            XRAY_AVAILABLE 
            and os.environ.get("ENABLE_XRAY", "false").lower() == "true"
        )
        self.logger = get_operation_logger(__name__)
        
        if self.enabled:
            try:
                # Patch common AWS SDK calls
                patch_all()
                self.logger.info("X-Ray tracing enabled and AWS SDKs patched")
            except Exception as e:
                self.logger.error("Failed to initialize X-Ray tracing", error=str(e))
                self.enabled = False

    @contextmanager
    def subsegment(
        self,
        name: str,
        namespace: str = "local",
        metadata: Optional[Dict[str, Any]] = None,
        annotations: Optional[Dict[str, str]] = None,
    ):
        """Create a subsegment for tracing operations.

        Args:
            name: Subsegment name
            namespace: Namespace for metadata (local, remote, aws)
            metadata: Additional metadata to attach
            annotations: Key-value annotations for filtering
        """
        if not self.enabled:
            yield None
            return

        try:
            subsegment = xray_recorder.begin_subsegment(name)
            
            # Add namespace
            if namespace:
                subsegment.namespace = namespace
            
            # Add metadata
            if metadata:
                for key, value in metadata.items():
                    subsegment.put_metadata(key, value, namespace)
            
            # Add annotations
            if annotations:
                for key, value in annotations.items():
                    subsegment.put_annotation(key, str(value))
            
            self.logger.debug(
                "Started X-Ray subsegment",
                subsegment_name=name,
                subsegment_id=subsegment.id,
            )
            
            yield subsegment
            
        except Exception as e:
            if self.enabled:
                xray_recorder.current_subsegment().add_exception(e)
                self.logger.error(
                    "X-Ray subsegment failed",
                    subsegment_name=name,
                    error=str(e),
                )
            raise
        finally:
            if self.enabled:
                try:
                    xray_recorder.end_subsegment()
                    self.logger.debug(
                        "Ended X-Ray subsegment",
                        subsegment_name=name,
                    )
                except Exception as e:
                    self.logger.error(
                        "Failed to end X-Ray subsegment",
                        subsegment_name=name,
                        error=str(e),
                    )

    def trace_function(
        self,
        name: Optional[str] = None,
        namespace: str = "local",
        capture_response: bool = False,
        capture_error: bool = True,
    ):
        """Decorator for tracing function execution.

        Args:
            name: Custom subsegment name (defaults to function name)
            namespace: Namespace for metadata
            capture_response: Whether to capture return value as metadata
            capture_error: Whether to capture exceptions

        Returns:
            Decorated function
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                subsegment_name = name or f"{func.__module__}.{func.__name__}"
                
                with self.subsegment(
                    subsegment_name,
                    namespace,
                    annotations={"function": func.__name__, "module": func.__module__}
                ) as subseg:
                    try:
                        result = func(*args, **kwargs)
                        
                        if capture_response and subseg and self.enabled:
                            subseg.put_metadata("response", result, namespace)
                        
                        return result
                        
                    except Exception as e:
                        if capture_error and subseg and self.enabled:
                            subseg.add_exception(e)
                            subseg.put_metadata("error", str(e), namespace)
                        raise
                        
            return wrapper
        return decorator

    def add_annotation(self, key: str, value: str):
        """Add annotation to current segment/subsegment.

        Args:
            key: Annotation key
            value: Annotation value
        """
        if not self.enabled:
            return

        try:
            current = xray_recorder.current_subsegment() or xray_recorder.current_segment()
            if current:
                current.put_annotation(key, str(value))
                self.logger.debug("Added X-Ray annotation", key=key, value=value)
        except Exception as e:
            self.logger.error("Failed to add X-Ray annotation", key=key, error=str(e))

    def add_metadata(self, key: str, value: Any, namespace: str = "local"):
        """Add metadata to current segment/subsegment.

        Args:
            key: Metadata key
            value: Metadata value
            namespace: Namespace for metadata
        """
        if not self.enabled:
            return

        try:
            current = xray_recorder.current_subsegment() or xray_recorder.current_segment()
            if current:
                current.put_metadata(key, value, namespace)
                self.logger.debug(
                    "Added X-Ray metadata",
                    key=key,
                    namespace=namespace,
                    value_type=type(value).__name__,
                )
        except Exception as e:
            self.logger.error("Failed to add X-Ray metadata", key=key, error=str(e))

    def set_user(self, user_id: str):
        """Set user ID for current segment.

        Args:
            user_id: User identifier
        """
        if not self.enabled:
            return

        try:
            current = xray_recorder.current_segment()
            if current:
                current.set_user(user_id)
                self.logger.debug("Set X-Ray user", user_id=user_id)
        except Exception as e:
            self.logger.error("Failed to set X-Ray user", user_id=user_id, error=str(e))

    def capture_aws_operation(self, service: str, operation: str):
        """Create subsegment for AWS service operations.

        Args:
            service: AWS service name (e.g., 'DynamoDB', 'S3')
            operation: Operation name (e.g., 'GetItem', 'PutObject')

        Returns:
            Context manager for AWS operation tracing
        """
        return self.subsegment(
            f"{service}.{operation}",
            namespace="aws",
            annotations={
                "aws.service": service,
                "aws.operation": operation,
            }
        )


# Global tracer instance
tracer = XRayTracer()


# Convenience decorators for common operations
def trace_openai_api_call(operation: str = "poll"):
    """Trace OpenAI API calls."""
    return tracer.trace_function(
        f"OpenAI.{operation}",
        namespace="remote",
        capture_error=True,
    )


def trace_s3_operation(operation: str):
    """Trace S3 operations."""
    return tracer.trace_function(
        f"S3.{operation}",
        namespace="aws",
        capture_error=True,
    )


def trace_chromadb_operation(operation: str):
    """Trace ChromaDB operations."""
    return tracer.trace_function(
        f"ChromaDB.{operation}",
        namespace="local",
        capture_error=True,
    )


def trace_dynamodb_operation(operation: str):
    """Trace DynamoDB operations."""
    return tracer.trace_function(
        f"DynamoDB.{operation}",
        namespace="aws",
        capture_error=True,
    )


# Context managers for manual tracing
@contextmanager
def trace_openai_batch_poll(batch_id: str, openai_batch_id: str):
    """Trace OpenAI batch polling operation."""
    with tracer.subsegment(
        "OpenAI.BatchPoll",
        namespace="remote",
        annotations={
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "service": "OpenAI",
        },
        metadata={
            "batch_details": {
                "batch_id": batch_id,
                "openai_batch_id": openai_batch_id,
            }
        }
    ) as subseg:
        yield subseg


@contextmanager
def trace_s3_snapshot_operation(operation: str, bucket: str, key: str):
    """Trace S3 snapshot operations."""
    with tracer.subsegment(
        f"S3.{operation}",
        namespace="aws",
        annotations={
            "aws.service": "S3",
            "aws.operation": operation,
            "aws.bucket": bucket,
        },
        metadata={
            "s3_details": {
                "bucket": bucket,
                "key": key,
                "operation": operation,
            }
        }
    ) as subseg:
        yield subseg


@contextmanager
def trace_chromadb_delta_save(collection: str, delta_count: int):
    """Trace ChromaDB delta save operation."""
    with tracer.subsegment(
        "ChromaDB.SaveDelta",
        namespace="local",
        annotations={
            "chromadb.collection": collection,
            "chromadb.operation": "save_delta",
        },
        metadata={
            "chromadb_details": {
                "collection": collection,
                "delta_count": delta_count,
            }
        }
    ) as subseg:
        yield subseg