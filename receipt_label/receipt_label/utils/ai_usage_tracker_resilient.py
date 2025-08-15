"""
Resilient AI Usage Tracker that leverages resilient DynamoDB client.
"""

import os
from typing import Any, Optional

from receipt_dynamo import ResilientDynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from .ai_usage_tracker import AIUsageTracker


class ResilientAIUsageTracker(AIUsageTracker):
    """
    AI usage tracker that uses ResilientDynamoClient for improved reliability.

    This tracker delegates all resilience concerns (circuit breaker, retry logic,
    batching) to the ResilientDynamoClient, maintaining proper separation of concerns
    between the receipt_label and receipt_dynamo packages.
    """

    def __init__(
        self,
        dynamo_client: Optional[Any] = None,
        table_name: Optional[str] = None,
        user_id: Optional[str] = None,
        track_to_dynamo: bool = True,
        track_to_file: bool = False,
        log_file: str = "/tmp/ai_usage.jsonl",
        # Resilience configuration passed to DynamoClient
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

        If no dynamo_client is provided, creates a ResilientDynamoClient with
        the specified resilience parameters.
        """
        # Track if we need to create a client
        created_client = False

        # Create resilient client if not provided
        if dynamo_client is None and track_to_dynamo:
            table_name = table_name or os.environ.get("DYNAMO_TABLE_NAME")
            if table_name:
                dynamo_client = ResilientDynamoClient(
                    table_name=table_name,
                    circuit_breaker_threshold=circuit_breaker_threshold,
                    circuit_breaker_timeout=circuit_breaker_timeout,
                    max_retry_attempts=max_retry_attempts,
                    retry_base_delay=retry_base_delay,
                    batch_size=batch_size,
                    batch_flush_interval=batch_flush_interval,
                    enable_batch_processing=enable_batch_processing,
                )
                created_client = True

        # Initialize parent with resilient client
        super().__init__(
            dynamo_client=dynamo_client,
            table_name=table_name,
            user_id=user_id,
            track_to_dynamo=track_to_dynamo,
            track_to_file=track_to_file,
            log_file=log_file,
        )

        # Store reference for cleanup
        self._created_client = created_client

    def flush(self) -> None:
        """Flush any pending metrics in the batch queue."""
        if hasattr(self.dynamo_client, "flush"):
            self.dynamo_client.flush()

    def close(self) -> None:
        """Clean up resources."""
        # Flush pending metrics
        self.flush()

        # Close client if we created it
        if self._created_client and hasattr(self.dynamo_client, "close"):
            self.dynamo_client.close()

    def get_stats(self) -> dict:
        """Get statistics from the resilient client."""
        stats = {}

        if hasattr(self.dynamo_client, "circuit_state"):
            stats["circuit_breaker_state"] = {
                "state": self.dynamo_client.circuit_state,
                "failure_count": self.dynamo_client.failure_count,
            }

        if hasattr(self.dynamo_client, "enable_batch_processing"):
            stats["batch_processing_enabled"] = (
                self.dynamo_client.enable_batch_processing
            )

        if hasattr(self.dynamo_client, "metric_queue"):
            stats["batch_queue"] = {
                "size": len(self.dynamo_client.metric_queue),
                "enabled": True,
            }

        return stats
