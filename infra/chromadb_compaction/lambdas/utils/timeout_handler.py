"""Timeout protection and monitoring utilities for Lambda functions."""

import os
import signal
import time
import threading
from contextlib import contextmanager
from typing import Optional, Callable, Any
from functools import wraps

from .logging import get_operation_logger
from .metrics import metrics


class TimeoutProtection:
    """Provides timeout protection and early warning for Lambda functions."""

    def __init__(self):
        """Initialize timeout protection."""
        self.logger = get_operation_logger(__name__)
        self.lambda_timeout = self._get_lambda_timeout()
        self.warning_threshold = 0.9  # Warn at 90% of timeout
        self.abort_threshold = 0.95  # Abort at 95% of timeout
        self.start_time = time.time()
        self.heartbeat_interval = int(
            os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "30")
        )
        self._heartbeat_thread = None
        self._should_stop_heartbeat = threading.Event()
        self.shutdown_callbacks = []
        # Track whether real Lambda context was provided
        self._has_context = False

    def _get_lambda_timeout(self) -> int:
        """Get Lambda timeout from context or environment."""
        # Try to get from Lambda context first
        context_remaining = getattr(
            self, "_lambda_context_remaining_time_ms", None
        )
        if context_remaining:
            return int(context_remaining() / 1000)

        # Fallback to environment or default
        try:
            val = int(os.environ.get("AWS_LAMBDA_FUNCTION_TIMEOUT", "900"))
            return max(val, 1)
        except Exception:
            return 900

    def set_lambda_context(self, context):
        """Set Lambda context for accurate timeout tracking.

        Args:
            context: AWS Lambda context object
        """
        if hasattr(context, "get_remaining_time_in_millis"):
            self._lambda_context_remaining_time_ms = (
                context.get_remaining_time_in_millis
            )
            self.lambda_timeout = int(
                context.get_remaining_time_in_millis() / 1000
            )
            self._has_context = True

    def get_remaining_time(self) -> float:
        """Get remaining execution time in seconds."""
        elapsed = time.time() - self.start_time
        # If no real context, return a generous default (prevents false timeouts)
        if not self._has_context:
            return float(os.environ.get("DEFAULT_REMAINING_SECONDS", "300"))
        remaining = self.lambda_timeout - elapsed
        if remaining <= 0:
            return 0.0
        return remaining

    def get_elapsed_time(self) -> float:
        """Get elapsed execution time in seconds."""
        return time.time() - self.start_time

    def is_approaching_timeout(self) -> bool:
        """Check if execution is approaching timeout."""
        remaining = self.get_remaining_time()
        warning_time = self.lambda_timeout * (1 - self.warning_threshold)
        return remaining <= warning_time

    def should_abort(self) -> bool:
        """Check if execution should abort to prevent timeout."""
        # Never abort based on synthetic timing when no real context is present
        if not self._has_context:
            return False
        remaining = self.get_remaining_time()
        abort_time = self.lambda_timeout * (1 - self.abort_threshold)
        return remaining <= abort_time

    def start_heartbeat(self):
        """Start heartbeat logging thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._should_stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()
        self.logger.info("Started heartbeat monitoring")

    def stop_heartbeat(self):
        """Stop heartbeat logging thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._should_stop_heartbeat.set()
            self._heartbeat_thread.join(timeout=1)
            self.logger.info("Stopped heartbeat monitoring")

    def register_shutdown_callback(self, callback: Callable[[], Any]):
        """Register a callback to run during graceful shutdown.

        Args:
            callback: Function to call during shutdown
        """
        self.shutdown_callbacks.append(callback)

    def _run_shutdown_callbacks(self):
        """Run all registered shutdown callbacks."""
        for callback in self.shutdown_callbacks:
            try:
                callback()
            except Exception as e:
                self.logger.error(
                    "Error in shutdown callback",
                    callback=callback.__name__,
                    error=str(e),
                )

    def _heartbeat_loop(self):
        """Heartbeat loop that logs progress and checks timeout."""
        while not self._should_stop_heartbeat.wait(self.heartbeat_interval):
            elapsed = self.get_elapsed_time()
            remaining = self.get_remaining_time()

            self.logger.info(
                "Lambda heartbeat",
                elapsed_seconds=elapsed,
                remaining_seconds=remaining,
                timeout_threshold_reached=self.is_approaching_timeout(),
            )

            # Publish heartbeat metric
            metrics.gauge(
                "LambdaRemainingTime",
                remaining,
                "Seconds",
                {
                    "function": os.environ.get(
                        "AWS_LAMBDA_FUNCTION_NAME", "unknown"
                    )
                },
            )

            # Warn if approaching timeout
            if self.is_approaching_timeout():
                self.logger.warning(
                    "Lambda execution approaching timeout",
                    elapsed_seconds=elapsed,
                    remaining_seconds=remaining,
                )

            # Break if should abort
            if self.should_abort():
                self.logger.error(
                    "Lambda execution should abort to prevent timeout",
                    elapsed_seconds=elapsed,
                    remaining_seconds=remaining,
                )
                # Run shutdown callbacks before breaking
                self._run_shutdown_callbacks()
                break

    @contextmanager
    def operation_timeout(
        self,
        operation_name: str,
        max_duration: Optional[float] = None,
        check_lambda_timeout: bool = True,
    ):
        """Context manager for timing operations with timeout protection.

        Args:
            operation_name: Name of the operation
            max_duration: Maximum duration for operation (seconds)
            check_lambda_timeout: Whether to check Lambda timeout

        Raises:
            TimeoutError: If operation exceeds timeout
        """
        start_time = time.time()

        # Determine effective budget: prefer explicit max_duration; otherwise use remaining
        remaining_time = self.get_remaining_time()
        effective_max = max_duration if max_duration else None
        if effective_max is None:
            # Use remaining minus a small safety margin when context is present
            safety_margin = 2.0  # seconds
            effective_max = (
                max(remaining_time - safety_margin, 0.5)
                if self._has_context
                else remaining_time  # use generous default when no context
            )

        self.logger.info(
            f"Starting operation with timeout protection: {operation_name}",
            max_duration=effective_max,
            remaining_lambda_time=remaining_time,
        )

        try:
            yield start_time
        finally:
            duration = time.time() - start_time

            # Check if operation exceeded limits
            timeout_exceeded = False
            if effective_max and duration > effective_max:
                timeout_exceeded = True
                self.logger.error(
                    f"Operation exceeded maximum duration: {operation_name}",
                    duration=duration,
                    max_duration=effective_max,
                )

            # Only warn (do not fail) if close to Lambda timeout at the end
            if check_lambda_timeout and self.should_abort():
                self.logger.warning(
                    f"Operation near Lambda timeout threshold at completion: {operation_name}",
                    duration=duration,
                    remaining_lambda_time=self.get_remaining_time(),
                )

            # Log completion
            self.logger.info(
                f"Completed operation: {operation_name}",
                duration=duration,
                timeout_exceeded=timeout_exceeded,
                remaining_lambda_time=self.get_remaining_time(),
            )

            if timeout_exceeded:
                raise TimeoutError(
                    f"Operation {operation_name} exceeded timeout limits"
                )

    def timeout_protected(
        self,
        max_duration: Optional[float] = None,
        operation_name: Optional[str] = None,
    ):
        """Decorator for timeout protection on functions.

        Args:
            max_duration: Maximum duration for operation (seconds)
            operation_name: Custom operation name

        Returns:
            Decorated function
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                op_name = (
                    operation_name or f"{func.__module__}.{func.__name__}"
                )

                with self.operation_timeout(op_name, max_duration):
                    return func(*args, **kwargs)

            return wrapper

        return decorator


class GracefulTimeoutHandler:
    """Handles graceful shutdown when approaching Lambda timeout."""

    def __init__(self, timeout_protection: TimeoutProtection):
        """Initialize graceful timeout handler.

        Args:
            timeout_protection: TimeoutProtection instance
        """
        self.timeout_protection = timeout_protection
        self.logger = get_operation_logger(__name__)
        self.shutdown_callbacks = []

    def register_shutdown_callback(self, callback: Callable[[], Any]):
        """Register a callback to run during graceful shutdown.

        Args:
            callback: Function to call during shutdown
        """
        self.shutdown_callbacks.append(callback)

    def check_and_handle_timeout(self) -> bool:
        """Check for timeout and handle gracefully if needed.

        Returns:
            True if timeout handling was triggered, False otherwise
        """
        if self.timeout_protection.should_abort():
            self.logger.warning(
                "Initiating graceful timeout handling",
                remaining_time=self.timeout_protection.get_remaining_time(),
                elapsed_time=self.timeout_protection.get_elapsed_time(),
            )

            # Run shutdown callbacks
            for callback in self.shutdown_callbacks:
                try:
                    callback()
                except Exception as e:
                    self.logger.error(
                        "Error in shutdown callback",
                        callback=callback.__name__,
                        error=str(e),
                    )

            # Publish timeout metric
            metrics.count(
                "LambdaTimeoutHandled",
                1,
                {
                    "function": os.environ.get(
                        "AWS_LAMBDA_FUNCTION_NAME", "unknown"
                    )
                },
            )

            return True

        return False


# Global timeout protection instance
timeout_protection = TimeoutProtection()
graceful_handler = GracefulTimeoutHandler(timeout_protection)


def with_timeout_protection(
    max_duration: Optional[float] = None,
    operation_name: Optional[str] = None,
):
    """Decorator for adding timeout protection to functions.

    Args:
        max_duration: Maximum duration for operation (seconds)
        operation_name: Custom operation name

    Returns:
        Decorated function
    """
    return timeout_protection.timeout_protected(max_duration, operation_name)


def check_timeout():
    """Check if Lambda is approaching timeout and handle gracefully."""
    return graceful_handler.check_and_handle_timeout()


@contextmanager
def operation_with_timeout(
    operation_name: str, max_duration: Optional[float] = None
):
    """Context manager for operations with timeout protection.

    Args:
        operation_name: Name of the operation
        max_duration: Maximum duration for operation (seconds)
    """
    with timeout_protection.operation_timeout(operation_name, max_duration):
        yield


def start_lambda_monitoring(context=None):
    """Start Lambda monitoring and timeout protection.

    Args:
        context: AWS Lambda context object
    """
    if context:
        timeout_protection.set_lambda_context(context)

    timeout_protection.start_heartbeat()


def stop_lambda_monitoring():
    """Stop Lambda monitoring and timeout protection."""
    timeout_protection.stop_heartbeat()
