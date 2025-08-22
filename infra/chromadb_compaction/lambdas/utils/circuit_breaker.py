"""Circuit breaker pattern implementation for protecting against external API failures."""

import time
import threading
from enum import Enum
from typing import Optional, Callable, Any, Dict
from functools import wraps
from contextlib import contextmanager

from .logging import get_operation_logger
from .metrics import metrics


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit is open, requests are blocked
    HALF_OPEN = "half_open"  # Testing if service has recovered


class CircuitBreaker:
    """Circuit breaker implementation for external service calls."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type = Exception,
        success_threshold: int = 3,
    ):
        """Initialize circuit breaker.

        Args:
            name: Circuit breaker name for logging/metrics
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time to wait before attempting recovery (seconds)
            expected_exception: Exception type that triggers circuit opening
            success_threshold: Consecutive successes needed to close circuit in half-open state
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.success_threshold = success_threshold

        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
        self._lock = threading.Lock()

        self.logger = get_operation_logger(__name__)

    def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt to reset to half-open."""
        return (
            self.state == CircuitState.OPEN
            and time.time() - self.last_failure_time >= self.recovery_timeout
        )

    def _record_success(self):
        """Record a successful operation."""
        with self._lock:
            self.failure_count = 0

            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                self.logger.info(
                    "Circuit breaker success in half-open state",
                    circuit_name=self.name,
                    success_count=self.success_count,
                    success_threshold=self.success_threshold,
                )

                if self.success_count >= self.success_threshold:
                    self._transition_to_closed()
            elif self.state == CircuitState.CLOSED:
                self.success_count = 0  # Reset success count in closed state

    def _record_failure(self, exception: Exception):
        """Record a failed operation."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            self.success_count = 0  # Reset success count on failure

            self.logger.warning(
                "Circuit breaker recorded failure",
                circuit_name=self.name,
                failure_count=self.failure_count,
                failure_threshold=self.failure_threshold,
                exception=str(exception),
            )

            if (
                self.state == CircuitState.CLOSED
                and self.failure_count >= self.failure_threshold
            ):
                self._transition_to_open()
            elif self.state == CircuitState.HALF_OPEN:
                self._transition_to_open()

    def _transition_to_open(self):
        """Transition circuit to open state."""
        self.state = CircuitState.OPEN
        self.logger.error(
            "Circuit breaker opened",
            circuit_name=self.name,
            failure_count=self.failure_count,
        )

        # Publish metrics
        metrics.count(
            "CircuitBreakerStateChange",
            1,
            dimensions={
                "circuit_name": self.name,
                "new_state": "open",
                "failure_count": str(self.failure_count),
            },
        )

    def _transition_to_half_open(self):
        """Transition circuit to half-open state."""
        self.state = CircuitState.HALF_OPEN
        self.success_count = 0
        self.logger.info(
            "Circuit breaker transitioning to half-open",
            circuit_name=self.name,
        )

        # Publish metrics
        metrics.count(
            "CircuitBreakerStateChange",
            1,
            dimensions={
                "circuit_name": self.name,
                "new_state": "half_open",
            },
        )

    def _transition_to_closed(self):
        """Transition circuit to closed state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.logger.info(
            "Circuit breaker closed - service recovered",
            circuit_name=self.name,
        )

        # Publish metrics
        metrics.count(
            "CircuitBreakerStateChange",
            1,
            dimensions={
                "circuit_name": self.name,
                "new_state": "closed",
            },
        )

    @contextmanager
    def call(self):
        """Context manager for protected calls."""
        # Check if we should attempt reset
        if self._should_attempt_reset():
            self._transition_to_half_open()

        # Check current state
        if self.state == CircuitState.OPEN:
            self.logger.warning(
                "Circuit breaker is open - blocking call",
                circuit_name=self.name,
                time_until_retry=self.recovery_timeout
                - (time.time() - self.last_failure_time),
            )

            metrics.count(
                "CircuitBreakerBlocked",
                1,
                dimensions={"circuit_name": self.name},
            )

            raise CircuitBreakerOpenError(
                f"Circuit breaker {self.name} is open. "
                f"Service will be retried in {self.recovery_timeout - (time.time() - self.last_failure_time):.1f}s"
            )

        # Attempt the call
        try:
            start_time = time.time()
            yield
            duration = time.time() - start_time

            # Record success
            self._record_success()

            # Publish success metrics
            metrics.gauge(
                "CircuitBreakerCallDuration",
                duration,
                "Seconds",
                dimensions={
                    "circuit_name": self.name,
                    "result": "success",
                    "state": self.state.value,
                },
            )

        except self.expected_exception as e:
            duration = time.time() - start_time

            # Record failure
            self._record_failure(e)

            # Publish failure metrics
            metrics.gauge(
                "CircuitBreakerCallDuration",
                duration,
                "Seconds",
                dimensions={
                    "circuit_name": self.name,
                    "result": "failure",
                    "state": self.state.value,
                },
            )

            metrics.count(
                "CircuitBreakerFailure",
                1,
                dimensions={
                    "circuit_name": self.name,
                    "exception_type": type(e).__name__,
                },
            )

            # Re-raise the exception
            raise

    def protect(self, func: Callable):
        """Decorator for protecting function calls."""

        @wraps(func)
        def wrapper(*args, **kwargs):
            with self.call():
                return func(*args, **kwargs)

        return wrapper

    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "success_threshold": self.success_threshold,
            "last_failure_time": self.last_failure_time,
            "time_until_retry": (
                max(
                    0,
                    self.recovery_timeout
                    - (time.time() - self.last_failure_time),
                )
                if self.state == CircuitState.OPEN
                else None
            ),
        }


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and blocking calls."""

    pass


class CircuitBreakerManager:
    """Manages multiple circuit breakers for different services."""

    def __init__(self):
        """Initialize circuit breaker manager."""
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self.logger = get_operation_logger(__name__)

    def get_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type = Exception,
        success_threshold: int = 3,
    ) -> CircuitBreaker:
        """Get or create a circuit breaker.

        Args:
            name: Circuit breaker name
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time to wait before attempting recovery (seconds)
            expected_exception: Exception type that triggers circuit opening
            success_threshold: Consecutive successes needed to close circuit

        Returns:
            CircuitBreaker instance
        """
        if name not in self._breakers:
            with self._lock:
                if name not in self._breakers:
                    self._breakers[name] = CircuitBreaker(
                        name=name,
                        failure_threshold=failure_threshold,
                        recovery_timeout=recovery_timeout,
                        expected_exception=expected_exception,
                        success_threshold=success_threshold,
                    )
                    self.logger.info(
                        "Created new circuit breaker",
                        circuit_name=name,
                        failure_threshold=failure_threshold,
                        recovery_timeout=recovery_timeout,
                    )

        return self._breakers[name]

    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Get states of all circuit breakers."""
        return {
            name: breaker.get_state()
            for name, breaker in self._breakers.items()
        }

    def reset_breaker(self, name: str) -> bool:
        """Manually reset a circuit breaker to closed state.

        Args:
            name: Circuit breaker name

        Returns:
            True if breaker was reset, False if breaker doesn't exist
        """
        if name in self._breakers:
            with self._breakers[name]._lock:
                self._breakers[name]._transition_to_closed()
                self.logger.info(
                    "Manually reset circuit breaker", circuit_name=name
                )
                return True
        return False


# Global circuit breaker manager
circuit_manager = CircuitBreakerManager()


# Convenience functions for common external services
def openai_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for OpenAI API calls."""
    return circuit_manager.get_breaker(
        name="openai_api",
        failure_threshold=3,
        recovery_timeout=30.0,
        expected_exception=Exception,  # Catch all exceptions for OpenAI
        success_threshold=2,
    )


def s3_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for S3 operations."""
    return circuit_manager.get_breaker(
        name="s3_operations",
        failure_threshold=5,
        recovery_timeout=15.0,
        expected_exception=Exception,
        success_threshold=3,
    )


def chromadb_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for ChromaDB operations."""
    return circuit_manager.get_breaker(
        name="chromadb_operations",
        failure_threshold=4,
        recovery_timeout=20.0,
        expected_exception=Exception,
        success_threshold=2,
    )


def dynamodb_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for DynamoDB operations."""
    return circuit_manager.get_breaker(
        name="dynamodb_operations",
        failure_threshold=5,
        recovery_timeout=10.0,
        expected_exception=Exception,
        success_threshold=3,
    )


# Decorators for common services
def protect_openai_call(func):
    """Decorator to protect OpenAI API calls with circuit breaker."""
    return openai_circuit_breaker().protect(func)


def protect_s3_call(func):
    """Decorator to protect S3 operations with circuit breaker."""
    return s3_circuit_breaker().protect(func)


def protect_chromadb_call(func):
    """Decorator to protect ChromaDB operations with circuit breaker."""
    return chromadb_circuit_breaker().protect(func)


def protect_dynamodb_call(func):
    """Decorator to protect DynamoDB operations with circuit breaker."""
    return dynamodb_circuit_breaker().protect(func)
