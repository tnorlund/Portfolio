"""Resilient DynamoDB client with circuit breaker, retry, and batching."""

import random
import threading
import time
from typing import List, Optional

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


class CircuitBreakerState:
    """States for the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ResilientDynamoClient(DynamoClient):
    """
    DynamoDB client with resilience patterns for production use.

    Features:
    - Circuit breaker to prevent cascading failures
    - Retry logic with exponential backoff
    - Batch queue for efficient writes
    - Graceful degradation
    """

    def __init__(
        self,
        table_name: str,
        region: str = "us-east-1",
        # Resilience configuration
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: float = 30.0,
        max_retry_attempts: int = 3,
        retry_base_delay: float = 1.0,
        batch_size: int = 25,
        batch_flush_interval: float = 5.0,
        enable_batch_processing: bool = True,
    ):
        """Initialize resilient DynamoDB client."""
        super().__init__(table_name, region)

        # Circuit breaker state
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_timeout = circuit_breaker_timeout
        self.circuit_state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.circuit_lock = threading.Lock()

        # Retry configuration
        self.max_retry_attempts = max_retry_attempts
        self.retry_base_delay = retry_base_delay

        # Batch processing for AI usage metrics
        self.batch_size = batch_size
        self.batch_flush_interval = batch_flush_interval
        self.enable_batch_processing = enable_batch_processing

        if self.enable_batch_processing:
            self.metric_queue: List[AIUsageMetric] = []
            self.queue_lock = threading.Lock()
            self.last_flush_time = time.time()
            self.flush_thread = threading.Thread(
                target=self._auto_flush_worker, daemon=True
            )
            self.stop_flag = threading.Event()
            self.flush_thread.start()

    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker allows operation."""
        with self.circuit_lock:
            if self.circuit_state == CircuitBreakerState.OPEN:
                # Check if timeout has passed
                if (
                    self.last_failure_time
                    and (time.time() - self.last_failure_time)
                    > self.circuit_breaker_timeout
                ):
                    self.circuit_state = CircuitBreakerState.HALF_OPEN
                    return True
                return False
            return True

    def _record_success(self) -> None:
        """Record successful operation."""
        with self.circuit_lock:
            self.failure_count = 0
            if self.circuit_state == CircuitBreakerState.HALF_OPEN:
                self.circuit_state = CircuitBreakerState.CLOSED

    def _record_failure(self) -> None:
        """Record failed operation."""
        with self.circuit_lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.circuit_breaker_threshold:
                self.circuit_state = CircuitBreakerState.OPEN

    def _exponential_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        delay = min(self.retry_base_delay * (2**attempt), 60.0)
        # Add jitter
        return float(delay * (1 + random.random() * 0.25))

    def put_ai_usage_metric(self, metric: AIUsageMetric) -> None:
        """
        Store AI usage metric with resilience patterns.

        Args:
            metric: The metric to store
        """
        if self.enable_batch_processing:
            # Add to queue for batch processing
            metrics_to_flush = None
            with self.queue_lock:
                self.metric_queue.append(metric)

                # Check if we should flush
                if len(self.metric_queue) >= self.batch_size:
                    metrics_to_flush = self._prepare_flush()

            # Execute batch write outside of lock
            if metrics_to_flush:
                self._batch_write_metrics_with_retry(metrics_to_flush)
        else:
            # Direct write with retry
            self._put_metric_with_retry(metric)

    def _put_metric_with_retry(self, metric: AIUsageMetric) -> None:
        """Put metric with retry logic."""
        last_exception = None

        for attempt in range(self.max_retry_attempts):
            if not self._check_circuit_breaker():
                raise RuntimeError("Circuit breaker is OPEN")

            try:
                super().put_ai_usage_metric(metric)
                self._record_success()
                return
            except (RuntimeError, ValueError, KeyError) as e:
                self._record_failure()
                last_exception = e

                if attempt < self.max_retry_attempts - 1:
                    time.sleep(self._exponential_backoff(attempt))

        raise last_exception or RuntimeError("Failed to store metric")

    def _auto_flush_worker(self) -> None:
        """Background worker for auto-flushing metrics."""
        while not self.stop_flag.is_set():
            time.sleep(0.1)

            metrics_to_flush = None
            with self.queue_lock:
                if (
                    self.metric_queue
                    and (time.time() - self.last_flush_time)
                    >= self.batch_flush_interval
                ):
                    metrics_to_flush = self._prepare_flush()

            # Execute batch write outside of lock
            if metrics_to_flush:
                self._batch_write_metrics_with_retry(metrics_to_flush)

    def _prepare_flush(self) -> Optional[List[AIUsageMetric]]:
        """Prepare metrics for flushing (must be called with lock held).

        Returns:
            Metrics to flush or None if queue is empty
        """
        if not self.metric_queue:
            return None

        # Copy and clear queue
        metrics_to_flush = self.metric_queue.copy()
        self.metric_queue.clear()
        self.last_flush_time = time.time()
        return metrics_to_flush

    def _batch_write_metrics_with_retry(
        self, metrics: List[AIUsageMetric]
    ) -> None:
        """Batch write metrics with retry logic."""
        remaining_metrics = metrics.copy()

        for attempt in range(self.max_retry_attempts):
            if not self._check_circuit_breaker():
                # Circuit breaker is open, skip
                print(
                    f"Circuit breaker OPEN, skipping "
                    f"{len(remaining_metrics)} metrics"
                )
                return

            try:
                # Use parent's batch write method
                failed_metrics = super().batch_put_ai_usage_metrics(
                    remaining_metrics
                )

                if not failed_metrics:
                    self._record_success()
                    return

                # Update remaining metrics for retry
                remaining_metrics = failed_metrics
                raise RuntimeError(
                    f"{len(failed_metrics)} metrics failed to write"
                )

            except (RuntimeError, ValueError, KeyError):
                self._record_failure()

                if attempt < self.max_retry_attempts - 1:
                    time.sleep(self._exponential_backoff(attempt))

        # Log final failure
        if remaining_metrics:
            print(
                f"Failed to write {len(remaining_metrics)} metrics after "
                f"{self.max_retry_attempts} attempts"
            )

    def flush(self) -> None:
        """Manually flush any pending metrics."""
        metrics_to_flush = None
        with self.queue_lock:
            metrics_to_flush = self._prepare_flush()

        # Execute batch write outside of lock
        if metrics_to_flush:
            self._batch_write_with_retry(metrics_to_flush)

    def close(self) -> None:
        """Clean up resources."""
        if self.enable_batch_processing:
            self.stop_flag.set()
            self.flush()
            if self.flush_thread.is_alive():
                self.flush_thread.join(timeout=1.0)
