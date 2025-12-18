"""Graceful shutdown utilities for Lambda functions."""

# import signal  # Not currently used
import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from .logging import get_operation_logger
from .metrics import metrics
from .timeout_handler import timeout_protection


class GracefulShutdownManager:
    """Manages graceful shutdown for Lambda functions."""

    def __init__(self):
        """Initialize graceful shutdown manager."""
        self.logger = get_operation_logger(__name__)
        self.shutdown_callbacks: List[Callable[[], Any]] = []
        self.cleanup_callbacks: List[Callable[[], Any]] = []
        self.is_shutting_down = False
        self.shutdown_lock = threading.Lock()
        self.active_operations: Dict[str, threading.Event] = {}
        self.operation_lock = threading.Lock()

    def register_shutdown_callback(self, callback: Callable[[], Any]):
        """Register a callback to run during shutdown.

        Args:
            callback: Function to call during shutdown
        """
        self.shutdown_callbacks.append(callback)
        self.logger.debug(
            "Registered shutdown callback",
            callback_name=callback.__name__,
            total_callbacks=len(self.shutdown_callbacks),
        )

    def register_cleanup_callback(self, callback: Callable[[], Any]):
        """Register a callback for final cleanup.

        Args:
            callback: Function to call during final cleanup
        """
        self.cleanup_callbacks.append(callback)
        self.logger.debug(
            "Registered cleanup callback",
            callback_name=callback.__name__,
            total_cleanup_callbacks=len(self.cleanup_callbacks),
        )

    def register_active_operation(self, operation_id: str) -> threading.Event:
        """Register an active operation that needs to complete gracefully.

        Args:
            operation_id: Unique identifier for the operation

        Returns:
            Event that will be set when shutdown is initiated
        """
        with self.operation_lock:
            stop_event = threading.Event()
            self.active_operations[operation_id] = stop_event

            self.logger.debug(
                "Registered active operation",
                operation_id=operation_id,
                total_operations=len(self.active_operations),
            )

            return stop_event

    def unregister_active_operation(self, operation_id: str):
        """Unregister an active operation.

        Args:
            operation_id: Unique identifier for the operation
        """
        with self.operation_lock:
            if operation_id in self.active_operations:
                del self.active_operations[operation_id]
                self.logger.debug(
                    "Unregistered active operation",
                    operation_id=operation_id,
                    remaining_operations=len(self.active_operations),
                )

    def initiate_shutdown(self, reason: str = "timeout_approaching"):
        """Initiate graceful shutdown process.

        Args:
            reason: Reason for shutdown (timeout_approaching, manual, etc.)
        """
        with self.shutdown_lock:
            if self.is_shutting_down:
                self.logger.debug("Shutdown already in progress")
                return

            self.is_shutting_down = True
            self.logger.warning(
                "Initiating graceful shutdown",
                reason=reason,
                active_operations=len(self.active_operations),
                shutdown_callbacks=len(self.shutdown_callbacks),
            )

            # Publish shutdown metric
            metrics.count(
                "GracefulShutdownInitiated",
                1,
                dimensions={
                    "reason": reason,
                    "active_operations": str(len(self.active_operations)),
                },
            )

        # Signal all active operations to stop
        with self.operation_lock:
            for operation_id, stop_event in self.active_operations.items():
                stop_event.set()
                self.logger.debug(
                    "Signaled operation to stop",
                    operation_id=operation_id,
                )

        # Run shutdown callbacks
        for callback in self.shutdown_callbacks:
            try:
                self.logger.debug(
                    "Running shutdown callback",
                    callback_name=callback.__name__,
                )
                callback()
            except Exception as e:
                self.logger.error(
                    "Error in shutdown callback",
                    callback_name=callback.__name__,
                    error=str(e),
                )

    def wait_for_operations(self, max_wait_time: float = 10.0) -> bool:
        """Wait for active operations to complete.

        Args:
            max_wait_time: Maximum time to wait for operations (seconds)

        Returns:
            True if all operations completed, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            with self.operation_lock:
                if not self.active_operations:
                    self.logger.info("All operations completed gracefully")
                    return True

            self.logger.debug(
                "Waiting for operations to complete",
                remaining_operations=len(self.active_operations),
                elapsed_time=time.time() - start_time,
            )

            time.sleep(0.5)

        # Timeout reached
        with self.operation_lock:
            remaining = list(self.active_operations.keys())

        self.logger.warning(
            "Timeout waiting for operations to complete",
            remaining_operations=remaining,
            wait_time=max_wait_time,
        )

        metrics.count(
            "GracefulShutdownTimeout",
            1,
            dimensions={"remaining_operations": str(len(remaining))},
        )

        return False

    def cleanup(self):
        """Run final cleanup callbacks."""
        self.logger.info("Running final cleanup")

        for callback in self.cleanup_callbacks:
            try:
                self.logger.debug(
                    "Running cleanup callback",
                    callback_name=callback.__name__,
                )
                callback()
            except Exception as e:
                self.logger.error(
                    "Error in cleanup callback",
                    callback_name=callback.__name__,
                    error=str(e),
                )

    @contextmanager
    def managed_operation(self, operation_id: str):
        """Context manager for managed operations during shutdown.

        Args:
            operation_id: Unique identifier for the operation

        Yields:
            Event that will be set when shutdown is initiated
        """
        if self.is_shutting_down:
            self.logger.warning(
                "Attempted to start operation during shutdown",
                operation_id=operation_id,
            )
            raise RuntimeError("Cannot start operation during shutdown")

        stop_event = self.register_active_operation(operation_id)

        try:
            yield stop_event
        finally:
            self.unregister_active_operation(operation_id)

    def shutdown_protected(
        self, operation_name: Optional[str] = None, max_wait_time: float = 10.0
    ):
        """Decorator for operations needing shutdown protection.

        Args:
            operation_name: Custom operation name
            max_wait_time: Maximum time to wait for graceful completion

        Returns:
            Decorated function
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                op_name = operation_name or f"{func.__module__}.{func.__name__}"

                with self.managed_operation(op_name) as stop_event:
                    # Check if shutdown was initiated before we started
                    if stop_event.is_set():
                        self.logger.warning(
                            "Operation aborted due to shutdown",
                            operation_name=op_name,
                        )
                        raise RuntimeError("Operation aborted due to shutdown")

                    return func(*args, **kwargs)

            return wrapper

        return decorator


class TimeoutAwareShutdownManager(GracefulShutdownManager):
    """Shutdown manager that integrates with timeout protection."""

    def __init__(self):
        """Initialize timeout-aware shutdown manager."""
        super().__init__()

        # Register with timeout protection
        timeout_protection.register_shutdown_callback(self._on_timeout_approaching)

    def _on_timeout_approaching(self):
        """Called when Lambda timeout is approaching."""
        self.logger.warning("Lambda timeout approaching - initiating graceful shutdown")
        self.initiate_shutdown("timeout_approaching")

    def check_and_handle_shutdown(self) -> bool:
        """Check if shutdown should be initiated based on timeout.

        Returns:
            True if shutdown was initiated, False otherwise
        """
        if timeout_protection.should_abort() and not self.is_shutting_down:
            self.initiate_shutdown("timeout_threshold_reached")
            return True
        return False

    @contextmanager
    def timeout_aware_operation(
        self,
        operation_id: str,
        check_interval: float = 1.0,
    ):
        """Context manager for operations that should check timeout periodically.

        Args:
            operation_id: Unique identifier for the operation
            check_interval: How often to check for timeout (seconds)

        Yields:
            Tuple of (stop_event, timeout_check_function)
        """
        with self.managed_operation(operation_id) as stop_event:
            last_check = time.time()

            def should_stop() -> bool:
                nonlocal last_check

                # Check stop event
                if stop_event.is_set():
                    return True

                # Periodic timeout check
                current_time = time.time()
                if current_time - last_check >= check_interval:
                    last_check = current_time
                    return self.check_and_handle_shutdown()

                return False

            yield stop_event, should_stop


# Global shutdown manager
shutdown_manager = TimeoutAwareShutdownManager()


# Convenience functions
def register_shutdown_callback(callback: Callable[[], Any]):
    """Register a callback to run during shutdown."""
    shutdown_manager.register_shutdown_callback(callback)


def register_cleanup_callback(callback: Callable[[], Any]):
    """Register a callback for final cleanup."""
    shutdown_manager.register_cleanup_callback(callback)


@contextmanager
def managed_operation(operation_id: str):
    """Context manager for operations that should be managed during shutdown."""
    with shutdown_manager.managed_operation(operation_id) as stop_event:
        yield stop_event


@contextmanager
def timeout_aware_operation(operation_id: str, check_interval: float = 1.0):
    """Context manager for operations that should check timeout periodically."""
    with shutdown_manager.timeout_aware_operation(operation_id, check_interval) as (
        stop_event,
        should_stop,
    ):
        yield stop_event, should_stop


def shutdown_protected(
    operation_name: Optional[str] = None,
    max_wait_time: float = 10.0,
):
    """Decorator for operations that need graceful shutdown protection."""
    return shutdown_manager.shutdown_protected(operation_name, max_wait_time)


def initiate_graceful_shutdown(reason: str = "manual"):
    """Initiate graceful shutdown process."""
    shutdown_manager.initiate_shutdown(reason)


def wait_for_graceful_completion(max_wait_time: float = 10.0) -> bool:
    """Wait for all operations to complete gracefully."""
    return shutdown_manager.wait_for_operations(max_wait_time)


def final_cleanup():
    """Run final cleanup callbacks."""
    shutdown_manager.cleanup()
