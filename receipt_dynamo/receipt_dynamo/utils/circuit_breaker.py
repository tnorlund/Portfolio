"""
Circuit breaker pattern implementation for resilient DynamoDB operations.
"""

import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, TypeVar, Union


class CircuitBreakerState(Enum):
    """States of the circuit breaker."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Blocking calls due to failures
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and blocking calls."""


T = TypeVar("T")


class CircuitBreaker:
    """
    Circuit breaker pattern implementation to prevent cascading failures.

    The circuit breaker monitors failures and opens the circuit (blocks calls)
    when failure threshold is reached. After a timeout period, it allows a test
    call to check if the service has recovered.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: float = 30.0,
        expected_exception: type = Exception,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before opening
                circuit
            timeout_seconds: Time to wait before attempting recovery
            expected_exception: Exception type to catch and count as failure
        """
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.expected_exception = expected_exception

        # State tracking
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.consecutive_successes = 0

    def _record_success(self) -> None:
        """Record a successful call."""
        self.failure_count = 0
        self.consecutive_successes += 1

        # Move from HALF_OPEN to CLOSED after successful test
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED

    def _record_failure(self) -> None:
        """Record a failed call."""
        self.failure_count += 1
        self.consecutive_successes = 0
        self.last_failure_time = time.time()

        # Open circuit if threshold reached
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        return (
            self.state == CircuitBreakerState.OPEN
            and self.last_failure_time is not None
            and time.time() - self.last_failure_time >= self.timeout_seconds
        )

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: If func raises exception
        """
        # Check if we should transition to HALF_OPEN
        if self._should_attempt_reset():
            self.state = CircuitBreakerState.HALF_OPEN

        # Block calls if circuit is OPEN
        if self.state == CircuitBreakerState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker is OPEN. Last failure: "
                f"{time.time() - (self.last_failure_time or 0):.1f}s ago"
            )

        # Attempt the call
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except self.expected_exception:  # type: ignore[misc]
            self._record_failure()
            raise

    def get_state(self) -> Dict[str, Union[str, int, float, None]]:
        """Get current state information."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "consecutive_successes": self.consecutive_successes,
            "last_failure_time": self.last_failure_time,
            "time_since_failure": (
                time.time() - self.last_failure_time
                if self.last_failure_time is not None
                else None
            ),
        }
