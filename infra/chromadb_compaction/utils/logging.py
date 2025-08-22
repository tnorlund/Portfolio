"""Structured logging configuration for ChromaDB compaction operations."""

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Optional, Any
from functools import wraps


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured JSON logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured JSON."""
        log_obj = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "component": "chromadb_compaction",
        }

        # Add any extra fields from the log record
        if hasattr(record, "extra_fields"):
            log_obj.update(record.extra_fields)

        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Add X-Ray trace ID if available
        trace_id = os.environ.get("_X_AMZN_TRACE_ID")
        if trace_id:
            log_obj["trace_id"] = trace_id

        return json.dumps(log_obj)


class CompactionOperationLogger:
    """Logger with compaction operation timing and context management."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.correlation_id = str(uuid.uuid4())

    def info(self, message: str, **kwargs):
        """Log info message with compaction context."""
        self._log_with_context(logging.INFO, message, **kwargs)

    def error(self, message: str, **kwargs):
        """Log error message with compaction context."""
        self._log_with_context(logging.ERROR, message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message with compaction context."""
        self._log_with_context(logging.WARNING, message, **kwargs)

    def debug(self, message: str, **kwargs):
        """Log debug message with compaction context."""
        self._log_with_context(logging.DEBUG, message, **kwargs)

    def _log_with_context(self, level: int, message: str, **kwargs):
        """Log message with correlation ID and extra context."""
        extra_fields = {"correlation_id": self.correlation_id, **kwargs}

        # Use standard logging API with extra parameter
        self.logger.log(level, message, extra={"extra_fields": extra_fields})

    @contextmanager
    def operation_timer(
        self, operation_name: str, collection: Optional[str] = None, **context
    ):
        """Context manager for timing compaction operations."""
        start_time = time.time()
        operation_id = str(uuid.uuid4())

        # Build context with collection info
        op_context = {"operation_id": operation_id, "operation_name": operation_name}
        if collection:
            op_context["collection"] = collection
        op_context.update(context)

        self.info(f"Starting compaction operation: {operation_name}", **op_context)

        try:
            yield operation_id
        except Exception as e:
            duration = time.time() - start_time
            self.error(
                f"Compaction operation failed: {operation_name}",
                operation_id=operation_id,
                operation_name=operation_name,
                collection=collection,
                duration_seconds=duration,
                error=str(e),
                **context,
            )
            raise

        # Operation completed successfully
        duration = time.time() - start_time
        self.info(
            f"Compaction operation completed: {operation_name}",
            operation_id=operation_id,
            operation_name=operation_name,
            collection=collection,
            duration_seconds=duration,
            **context,
        )

    def time_function(self, operation_name: Optional[str] = None):
        """Decorator for timing function execution."""

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                op_name = operation_name or f"{func.__module__}.{func.__name__}"
                with self.operation_timer(op_name):
                    return func(*args, **kwargs)

            return wrapper

        return decorator


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: Logger name (defaults to calling module name)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name or __name__)

    # Only configure if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler()

        # Use structured JSON logging if enabled
        if (
            os.environ.get("ENABLE_STRUCTURED_LOGGING", "true").lower() == "true"
        ):
            formatter = StructuredFormatter()
        else:
            # Fallback to simple format
            formatter = logging.Formatter(
                "[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - "
                "%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Set level from environment or default to INFO
        level = os.environ.get("LOG_LEVEL", "INFO")
        logger.setLevel(getattr(logging, level, logging.INFO))

    return logger


def get_operation_logger(name: Optional[str] = None) -> CompactionOperationLogger:
    """Get an operation logger with enhanced features.

    Args:
        name: Logger name (defaults to calling module name)

    Returns:
        CompactionOperationLogger instance with timing and correlation features
    """
    base_logger = get_logger(name)
    return CompactionOperationLogger(base_logger)