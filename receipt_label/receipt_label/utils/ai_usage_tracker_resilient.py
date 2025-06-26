"""
Resilient AI Usage Tracker with circuit breaker, retry logic, and batch processing.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI
from openai.types.chat import ChatCompletion
from openai.types.create_embedding_response import CreateEmbeddingResponse
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from .ai_usage_tracker import AIUsageTracker
from .batch_queue import BatchQueue
from .circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from .cost_calculator import AICostCalculator
from .retry_with_backoff import exponential_backoff_with_jitter


class ResilientAIUsageTracker(AIUsageTracker):
    """
    Enhanced AI usage tracker with resilience patterns for production use.

    Features:
    - Circuit breaker to prevent cascading failures
    - Retry logic with exponential backoff
    - Batch processing to reduce DynamoDB load
    - Graceful degradation under stress
    """

    def __init__(
        self,
        dynamo_client: Optional[Any] = None,
        table_name: Optional[str] = None,
        user_id: Optional[str] = None,
        track_to_dynamo: bool = True,
        track_to_file: bool = False,
        log_file: str = "/tmp/ai_usage.jsonl",
        # Resilience configuration
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: float = 30.0,
        max_retry_attempts: int = 3,
        retry_base_delay: float = 1.0,
        batch_size: int = 25,
        batch_flush_interval: float = 5.0,
        enable_batch_processing: bool = True,
    ) -> None:
        """
        Initialize resilient AI usage tracker.

        Args:
            dynamo_client: DynamoDB client for storing metrics
            table_name: DynamoDB table name
            user_id: User identifier for tracking
            track_to_dynamo: Whether to store metrics in DynamoDB
            track_to_file: Whether to log metrics to a file
            log_file: Path to the log file
            circuit_breaker_threshold: Failures before opening circuit
            circuit_breaker_timeout: Seconds before attempting recovery
            max_retry_attempts: Maximum retry attempts for DynamoDB writes
            retry_base_delay: Base delay for exponential backoff
            batch_size: Items to batch before writing
            batch_flush_interval: Maximum seconds before flushing batch
            enable_batch_processing: Whether to use batch processing
        """
        super().__init__(
            dynamo_client=dynamo_client,
            table_name=table_name,
            user_id=user_id,
            track_to_dynamo=track_to_dynamo,
            track_to_file=track_to_file,
            log_file=log_file,
        )

        # Circuit breaker for DynamoDB operations
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            timeout_seconds=circuit_breaker_timeout,
            expected_exception=Exception,
        )

        # Retry configuration
        self.max_retry_attempts = max_retry_attempts
        self.retry_base_delay = retry_base_delay

        # Batch processing
        self.enable_batch_processing = enable_batch_processing
        self.batch_queue: Optional[BatchQueue] = None

        if self.enable_batch_processing and self.track_to_dynamo:
            self.batch_queue = BatchQueue(
                flush_callback=self._batch_write_to_dynamo,
                batch_size=batch_size,
                flush_interval=batch_flush_interval,
                auto_flush=True,
            )

        # Metrics for monitoring
        self.metrics_lock = threading.Lock()
        self.metrics_stats = {
            "total_attempts": 0,
            "successful_writes": 0,
            "failed_writes": 0,
            "circuit_breaker_rejections": 0,
            "retry_exhausted": 0,
        }

    def _store_metric(self, metric: AIUsageMetric) -> None:
        """Store metric with resilience patterns."""
        # Always try file logging first (fast and reliable)
        if self.track_to_file:
            self._write_to_file(metric)

        # DynamoDB storage with resilience
        if self.track_to_dynamo and self.dynamo_client:
            if self.enable_batch_processing and self.batch_queue:
                # Add to batch queue
                item = metric.to_dynamodb_item()
                self.batch_queue.add_item(item)
            else:
                # Direct write with resilience
                self._write_to_dynamo_with_resilience(
                    [metric.to_dynamodb_item()]
                )

    def _write_to_file(self, metric: AIUsageMetric) -> None:
        """Write metric to file (fallback storage)."""
        try:
            log_entry = {
                "service": metric.service,
                "model": metric.model,
                "operation": metric.operation,
                "timestamp": metric.timestamp.isoformat(),
                "request_id": metric.request_id,
                "input_tokens": metric.input_tokens,
                "output_tokens": metric.output_tokens,
                "total_tokens": metric.total_tokens,
                "cost_usd": metric.cost_usd,
                "latency_ms": metric.latency_ms,
                "user_id": metric.user_id,
                "job_id": metric.job_id,
                "batch_id": metric.batch_id,
                "error": metric.error,
            }
            with open(self.log_file, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            # Silently fail - don't disrupt main operation
            pass

    def _write_to_dynamo_with_resilience(
        self, items: List[Dict[str, Any]]
    ) -> None:
        """Write items to DynamoDB with circuit breaker and retry logic."""
        with self.metrics_lock:
            self.metrics_stats["total_attempts"] += len(items)

        try:
            # Check circuit breaker
            def write_batch():
                return self._dynamo_batch_write_with_retry(items)

            self.circuit_breaker.call(write_batch)

            with self.metrics_lock:
                self.metrics_stats["successful_writes"] += len(items)

        except CircuitBreakerOpenError:
            # Circuit is open - fail fast
            with self.metrics_lock:
                self.metrics_stats["circuit_breaker_rejections"] += len(items)

            # Write to file as fallback
            for item in items:
                self._write_metric_to_file_fallback(item)

        except Exception as e:
            # Other failures
            with self.metrics_lock:
                self.metrics_stats["failed_writes"] += len(items)

            # Write to file as fallback
            for item in items:
                self._write_metric_to_file_fallback(item)

    def _dynamo_batch_write_with_retry(
        self, items: List[Dict[str, Any]]
    ) -> None:
        """Write batch to DynamoDB with retry logic."""
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retry_attempts):
            try:
                if len(items) == 1:
                    # Single item write
                    self.dynamo_client.put_item(
                        TableName=self.table_name, Item=items[0]
                    )
                else:
                    # Batch write (max 25 items per DynamoDB batch)
                    for i in range(0, len(items), 25):
                        batch = items[i : i + 25]
                        request_items = {
                            self.table_name: [
                                {"PutRequest": {"Item": item}}
                                for item in batch
                            ]
                        }

                        response = self.dynamo_client.batch_write_item(
                            RequestItems=request_items
                        )

                        # Handle unprocessed items
                        unprocessed = response.get("UnprocessedItems", {})
                        if unprocessed:
                            # This will trigger a retry
                            raise Exception(
                                f"Unprocessed items: {len(unprocessed)}"
                            )

                # Success - return without error
                return

            except Exception as e:
                last_exception = e
                # Don't sleep after the last attempt
                if attempt < self.max_retry_attempts - 1:
                    delay = exponential_backoff_with_jitter(
                        attempt, self.retry_base_delay, 10.0, True
                    )
                    time.sleep(delay)

        # All attempts exhausted
        raise last_exception or Exception("Failed to write to DynamoDB")

    def _batch_write_to_dynamo(self, items: List[Dict[str, Any]]) -> None:
        """Callback for batch queue to write items."""
        if items:
            self._write_to_dynamo_with_resilience(items)

    def _write_metric_to_file_fallback(self, item: Dict[str, Any]) -> None:
        """Write DynamoDB item format to file as fallback."""
        try:
            with open(self.log_file + ".fallback", "a") as f:
                f.write(json.dumps({"dynamodb_item": item}) + "\n")
        except:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """Get tracker statistics for monitoring."""
        with self.metrics_lock:
            stats = self.metrics_stats.copy()

        stats["circuit_breaker_state"] = self.circuit_breaker.get_state()

        if self.batch_queue:
            stats["batch_queue"] = self.batch_queue.get_stats()

        # Calculate success rate
        total = stats["successful_writes"] + stats["failed_writes"]
        stats["success_rate"] = (
            stats["successful_writes"] / total if total > 0 else 0
        )

        return stats

    def flush(self) -> None:
        """Manually flush any pending metrics."""
        if self.batch_queue:
            self.batch_queue.flush()

    def close(self) -> None:
        """Clean up resources."""
        if self.batch_queue:
            self.batch_queue.stop()
