"""Centralized logging configuration for Lambda functions."""

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from typing import Optional


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


class OperationLogger:
    """Logger with operation timing and context management."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.correlation_id = str(uuid.uuid4())

    def info(self, message: str, **kwargs):
        """Log info message with additional context."""
        self._log_with_context(logging.INFO, message, **kwargs)

    def error(self, message: str, **kwargs):
        """Log error message with additional context."""
        self._log_with_context(logging.ERROR, message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message with additional context."""
        self._log_with_context(logging.WARNING, message, **kwargs)

    def debug(self, message: str, **kwargs):
        """Log debug message with additional context."""
        self._log_with_context(logging.DEBUG, message, **kwargs)

    def _log_with_context(self, level: int, message: str, **kwargs):
        """Log message with correlation ID and extra context."""
        extra_fields = {"correlation_id": self.correlation_id, **kwargs}

        # Create a new LogRecord with extra fields
        record = self.logger.makeRecord(
            self.logger.name, level, "", 0, message, (), None
        )
        record.extra_fields = extra_fields
        self.logger.handle(record)

    @contextmanager
    def operation_timer(self, operation_name: str, **context):
        """Context manager for timing operations."""
        start_time = time.time()
        operation_id = str(uuid.uuid4())

        self.info(
            f"Starting operation: {operation_name}",
            operation_id=operation_id,
            operation_name=operation_name,
            **context,
        )

        try:
            yield operation_id
        except Exception as e:
            duration = time.time() - start_time
            self.error(
                f"Operation failed: {operation_name}",
                operation_id=operation_id,
                operation_name=operation_name,
                duration_seconds=duration,
                error=str(e),
                **context,
            )
            raise

        # Operation completed successfully
        duration = time.time() - start_time
        self.info(
            f"Operation completed: {operation_name}",
            operation_id=operation_id,
            operation_name=operation_name,
            duration_seconds=duration,
            **context,
        )

    def time_function(self, operation_name: Optional[str] = None):
        """Decorator for timing function execution."""

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                op_name = (
                    operation_name or f"{func.__module__}.{func.__name__}"
                )
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
            os.environ.get("ENABLE_STRUCTURED_LOGGING", "true").lower()
            == "true"
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


def get_operation_logger(name: Optional[str] = None) -> OperationLogger:
    """Get an operation logger with enhanced features.

    Args:
        name: Logger name (defaults to calling module name)

    Returns:
        OperationLogger instance with timing and correlation features
    """
    base_logger = get_logger(name)
    return OperationLogger(base_logger)
