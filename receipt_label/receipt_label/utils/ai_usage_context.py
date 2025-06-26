"""
AI Usage Context Manager for consistent tracking across operations.

This module implements Phase 2 of the AI Usage Tracking system,
providing a context manager pattern for automatic context propagation
and metric flushing.
"""

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional

from .ai_usage_tracker import AIUsageTracker
from .environment_config import AIUsageEnvironmentConfig, Environment

# Thread-local storage for context isolation
_thread_local = threading.local()


def get_current_context() -> Optional[Dict[str, Any]]:
    """Get the current thread's AI usage context."""
    return getattr(_thread_local, "context", None)


def set_current_context(context: Optional[Dict[str, Any]]) -> None:
    """Set the current thread's AI usage context."""
    _thread_local.context = context


@contextmanager
def ai_usage_context(
    operation_type: str,
    tracker: Optional[AIUsageTracker] = None,
    environment: Optional[Environment] = None,
    **kwargs
) -> Generator[AIUsageTracker, None, None]:
    """
    Context manager for consistent AI usage tracking.

    Ensures all AI operations within the context have consistent metadata
    and automatically flushes metrics on exit.

    Args:
        operation_type: Type of operation being performed (e.g., 'batch_processing')
        tracker: Optional existing tracker instance. If not provided, creates new one.
        environment: Optional environment override. Defaults to auto-detection.
        **kwargs: Additional context metadata

    Yields:
        AIUsageTracker: Configured tracker instance

    Example:
        with ai_usage_context('receipt_processing', batch_id='123') as tracker:
            result = await tracker.track_openai_completion(
                model="gpt-4",
                messages=[...],
                context={'receipt_id': 'abc'}
            )
    """
    # Get or create tracker
    if tracker is None:
        tracker = AIUsageTracker.create_for_environment(environment=environment)

    # Build context
    context = {
        "operation_type": operation_type,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "thread_id": threading.get_ident(),
        **kwargs,
    }

    # Store previous context for nesting support
    previous_context = get_current_context()

    # If there's a parent context, merge it
    if previous_context:
        context["parent_operation"] = previous_context.get("operation_type")
        context["parent_context"] = {
            k: v
            for k, v in previous_context.items()
            if k not in ["start_time", "thread_id"]
        }

    try:
        # Set context on tracker and thread-local
        tracker.set_context(context)
        set_current_context(context)

        yield tracker

    finally:
        # Calculate operation duration
        if "start_time" in context:
            start = datetime.fromisoformat(context["start_time"])
            duration_ms = int(
                (datetime.now(timezone.utc) - start).total_seconds() * 1000
            )
            tracker.add_context_metadata({"operation_duration_ms": duration_ms})

        # Flush any pending metrics
        tracker.flush_metrics()

        # Restore previous context
        set_current_context(previous_context)


@contextmanager
def batch_ai_usage_context(
    batch_id: str, operation_type: str = "batch_processing", **kwargs
) -> Generator[AIUsageTracker, None, None]:
    """
    Specialized context manager for batch operations.

    Automatically adds batch-specific metadata and applies batch pricing
    where applicable.

    Args:
        batch_id: Unique identifier for the batch
        operation_type: Type of batch operation
        **kwargs: Additional context metadata

    Example:
        with batch_ai_usage_context('batch-123', item_count=100) as tracker:
            for item in items:
                await process_with_ai(item, tracker)
    """
    with ai_usage_context(
        operation_type=operation_type, batch_id=batch_id, is_batch=True, **kwargs
    ) as tracker:
        # Enable batch pricing for compatible operations
        tracker.set_batch_mode(True)
        yield tracker
