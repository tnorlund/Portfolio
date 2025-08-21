"""Timeout protection and monitoring utilities for ChromaDB compaction Lambda functions."""

import os
import threading
import time
from contextlib import contextmanager
from typing import Optional, Callable, Any, List
from functools import wraps

from .logging import get_operation_logger
from .metrics import metrics


class CompactionTimeoutProtection:
    """Provides timeout protection and early warning for ChromaDB compaction Lambdas."""

    def __init__(self):
        """Initialize timeout protection with compaction-specific settings."""
        self.logger = get_operation_logger(__name__)
        self.lambda_timeout = self._get_lambda_timeout()
        
        # Compaction-specific thresholds (more conservative for long operations)
        self.warning_threshold = 0.85  # Warn at 85% of timeout
        self.abort_threshold = 0.90    # Abort at 90% of timeout (more cleanup time)
        
        self.start_time = time.time()
        self.heartbeat_interval = int(
            os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60")  # Less frequent for compaction
        )
        self._heartbeat_thread = None
        self._should_stop_heartbeat = threading.Event()
        self.shutdown_callbacks: List[Callable[[], Any]] = []

    def _get_lambda_timeout(self) -> int:
        """Get Lambda timeout from context or environment."""
        # Try to get from Lambda context first
        context_remaining = getattr(
            self, "_lambda_context_remaining_time_ms", None
        )
        if context_remaining:
            return int(context_remaining() / 1000)

        # Fallback to environment or default (15 minutes for compaction)
        return int(os.environ.get("AWS_LAMBDA_FUNCTION_TIMEOUT", "900"))

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

    def get_remaining_time(self) -> float:
        """Get remaining execution time in seconds."""
        elapsed = time.time() - self.start_time
        return max(0, self.lambda_timeout - elapsed)

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
        self.logger.info("Started compaction heartbeat monitoring")

    def stop_heartbeat(self):
        """Stop heartbeat logging thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._should_stop_heartbeat.set()
            self._heartbeat_thread.join(timeout=2)  # Longer timeout for compaction
            self.logger.info("Stopped compaction heartbeat monitoring")

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
                    "Error in compaction shutdown callback",
                    callback_name=callback.__name__,
                    error=str(e),
                )

    def _heartbeat_loop(self):
        """Heartbeat loop that logs progress and checks timeout."""
        while not self._should_stop_heartbeat.wait(self.heartbeat_interval):
            elapsed = self.get_elapsed_time()
            remaining = self.get_remaining_time()

            self.logger.info(
                "Compaction Lambda heartbeat",
                elapsed_seconds=elapsed,
                remaining_seconds=remaining,
                timeout_threshold_reached=self.is_approaching_timeout(),
            )

            # Publish heartbeat metric
            metrics.gauge(
                "CompactionLambdaRemainingTime",
                remaining,
                "Seconds",
                {"function": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "unknown")},
            )

            # Warn if approaching timeout
            if self.is_approaching_timeout():
                self.logger.warning(
                    "Compaction Lambda execution approaching timeout",
                    elapsed_seconds=elapsed,
                    remaining_seconds=remaining,
                )

            # Break if should abort
            if self.should_abort():
                self.logger.error(
                    "Compaction Lambda execution should abort to prevent timeout",
                    elapsed_seconds=elapsed,
                    remaining_seconds=remaining,
                )
                # Run shutdown callbacks before breaking
                self._run_shutdown_callbacks()
                break

    @contextmanager
    def compaction_operation_timeout(
        self,
        operation_name: str,
        max_duration: Optional[float] = None,
        check_lambda_timeout: bool = True,
    ):
        """Context manager for timing compaction operations with timeout protection.

        Args:
            operation_name: Name of the compaction operation
            max_duration: Maximum duration for operation (seconds)
            check_lambda_timeout: Whether to check Lambda timeout

        Raises:
            TimeoutError: If operation exceeds timeout
        """
        start_time = time.time()

        self.logger.info(
            f"Starting compaction operation with timeout protection: {operation_name}",
            max_duration=max_duration,
            remaining_lambda_time=self.get_remaining_time(),
        )

        try:
            yield start_time
        finally:
            duration = time.time() - start_time

            # Check if operation exceeded limits
            timeout_exceeded = False
            if max_duration and duration > max_duration:
                timeout_exceeded = True
                self.logger.error(
                    f"Compaction operation exceeded maximum duration: {operation_name}",
                    duration=duration,
                    max_duration=max_duration,
                )

            if check_lambda_timeout and self.should_abort():
                timeout_exceeded = True
                self.logger.error(
                    f"Compaction operation exceeded Lambda timeout threshold: {operation_name}",
                    duration=duration,
                    remaining_lambda_time=self.get_remaining_time(),
                )

            # Log completion
            self.logger.info(
                f"Completed compaction operation: {operation_name}",
                duration=duration,
                timeout_exceeded=timeout_exceeded,
                remaining_lambda_time=self.get_remaining_time(),
            )

            if timeout_exceeded:
                raise TimeoutError(
                    f"Compaction operation {operation_name} exceeded timeout limits"
                )

    def timeout_protected(
        self, max_duration: Optional[float] = None, operation_name: Optional[str] = None
    ):
        """Decorator for timeout protection on compaction functions.

        Args:
            max_duration: Maximum duration for operation (seconds)
            operation_name: Custom operation name

        Returns:
            Decorated function
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                op_name = operation_name or f"{func.__module__}.{func.__name__}"

                with self.compaction_operation_timeout(op_name, max_duration):
                    return func(*args, **kwargs)

            return wrapper

        return decorator


class CompactionGracefulShutdown:
    """Handles graceful shutdown when approaching Lambda timeout for compaction operations."""

    def __init__(self, timeout_protection: CompactionTimeoutProtection):
        """Initialize graceful timeout handler.

        Args:
            timeout_protection: CompactionTimeoutProtection instance
        """
        self.timeout_protection = timeout_protection
        self.logger = get_operation_logger(__name__)
        self.shutdown_callbacks: List[Callable[[], Any]] = []

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
                "Initiating graceful compaction timeout handling",
                remaining_time=self.timeout_protection.get_remaining_time(),
                elapsed_time=self.timeout_protection.get_elapsed_time(),
            )

            # Run shutdown callbacks
            for callback in self.shutdown_callbacks:
                try:
                    callback()
                except Exception as e:
                    self.logger.error(
                        "Error in compaction shutdown callback",
                        callback_name=callback.__name__,
                        error=str(e),
                    )

            # Publish timeout metric
            metrics.count(
                "CompactionLambdaTimeoutHandled",
                1,
                {"function": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "unknown")},
            )

            return True

        return False


# Global instances for compaction
timeout_protection = CompactionTimeoutProtection()
graceful_handler = CompactionGracefulShutdown(timeout_protection)


def with_compaction_timeout_protection(
    max_duration: Optional[float] = None, operation_name: Optional[str] = None
):
    """Decorator for adding timeout protection to compaction functions.

    Args:
        max_duration: Maximum duration for operation (seconds)
        operation_name: Custom operation name

    Returns:
        Decorated function
    """
    return timeout_protection.timeout_protected(max_duration, operation_name)


def check_compaction_timeout():
    """Check if Lambda is approaching timeout and handle gracefully."""
    return graceful_handler.check_and_handle_timeout()


@contextmanager
def compaction_operation_with_timeout(
    operation_name: str, max_duration: Optional[float] = None
):
    """Context manager for compaction operations with timeout protection.

    Args:
        operation_name: Name of the operation
        max_duration: Maximum duration for operation (seconds)
    """
    with timeout_protection.compaction_operation_timeout(
        operation_name, max_duration
    ):
        yield


def start_compaction_lambda_monitoring(context=None):
    """Start Lambda monitoring and timeout protection for compaction operations.

    Args:
        context: AWS Lambda context object
    """
    if context:
        timeout_protection.set_lambda_context(context)

    timeout_protection.start_heartbeat()


def stop_compaction_lambda_monitoring():
    """Stop Lambda monitoring and timeout protection for compaction operations."""
    timeout_protection.stop_heartbeat()