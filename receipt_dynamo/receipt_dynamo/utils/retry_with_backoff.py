"""
Retry mechanism with exponential backoff for handling transient failures.
"""

import random
import time
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type, TypeVar, Union

T = TypeVar("T")


class RetryExhaustedError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, last_exception: Exception):
        super().__init__(message)
        self.last_exception = last_exception


def exponential_backoff_with_jitter(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
) -> float:
    """
    Calculate exponential backoff delay with optional jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        jitter: Whether to add random jitter to prevent thundering herd

    Returns:
        Delay in seconds
    """
    # Calculate exponential delay: base * 2^attempt
    delay: float = min(base_delay * (2**attempt), max_delay)

    if jitter:
        # Add random jitter between 0 and 25% of delay
        delay = delay * (1 + random.random() * 0.25)

    return delay


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: Union[Type[Exception], Tuple[Type[Exception], ...]] = Exception,
    jitter: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        base_delay: Base delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        exceptions: Exception types to catch and retry
        jitter: Whether to add random jitter to delays

    Returns:
        Decorated function
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:  # pylint: disable=broad-exception-caught
                    last_exception = e

                    # Don't sleep after the last attempt
                    if attempt < max_attempts - 1:
                        delay = exponential_backoff_with_jitter(
                            attempt, base_delay, max_delay, jitter
                        )
                        time.sleep(delay)

            # All attempts exhausted
            raise RetryExhaustedError(
                f"Failed after {max_attempts} attempts",
                last_exception or Exception("Unknown error"),
            )

        return wrapper

    return decorator


class RetryManager:
    """
    Context manager for retry logic with exponential backoff.

    Usage:
        with RetryManager(max_attempts=3) as retry:
            while retry.should_retry():
                try:
                    result = do_something()
                    retry.success()
                    return result
                except Exception as e:
                    retry.failure(e)
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

        self.attempt = 0
        self.succeeded = False
        self.last_exception: Optional[Exception] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.succeeded and self.last_exception:
            raise RetryExhaustedError(
                f"Failed after {self.attempt} attempts", self.last_exception
            )
        return False

    def should_retry(self) -> bool:
        """Check if more retry attempts are available."""
        return self.attempt < self.max_attempts and not self.succeeded

    def success(self) -> None:
        """Mark the operation as successful."""
        self.succeeded = True

    def failure(self, exception: Exception) -> None:
        """Record a failure and sleep before next attempt."""
        self.last_exception = exception
        self.attempt += 1

        # Sleep before next attempt (unless this was the last attempt)
        if self.attempt < self.max_attempts:
            delay = exponential_backoff_with_jitter(
                self.attempt - 1, self.base_delay, self.max_delay, self.jitter
            )
            time.sleep(delay)
