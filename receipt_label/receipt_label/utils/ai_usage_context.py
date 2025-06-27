"""
AI Usage Context Manager for consistent tracking across operations.

This module implements Phase 2 of the AI Usage Tracking system,
providing a context manager pattern for automatic context propagation
and metric flushing.
"""

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional

from .ai_usage_tracker import AIUsageTracker
from .environment_config import Environment

logger = logging.getLogger(__name__)

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
    **kwargs,
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
        tracker = AIUsageTracker.create_for_environment(
            environment=environment
        )

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

    # Track if an error occurred
    error_occurred = False
    error_message = None

    try:
        # Set context on tracker and thread-local
        tracker.set_context(context)
        set_current_context(context)

        yield tracker

    except Exception as e:
        # Mark that an error occurred but don't suppress it
        error_occurred = True
        error_message = str(e)
        raise

    finally:
        # Calculate operation duration
        if "start_time" in context:
            start = datetime.fromisoformat(context["start_time"])
            duration_ms = int(
                (datetime.now(timezone.utc) - start).total_seconds() * 1000
            )
            tracker.add_context_metadata(
                {
                    "operation_duration_ms": duration_ms,
                    "error_occurred": error_occurred,
                    "error_message": error_message if error_occurred else None,
                }
            )

        # Always flush metrics, even on error
        try:
            tracker.flush_metrics()
        except Exception as flush_error:
            # Log flush errors but don't raise them to avoid masking the original error
            logger.error(f"Failed to flush metrics: {flush_error}")

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
        operation_type=operation_type,
        batch_id=batch_id,
        is_batch=True,
        **kwargs,
    ) as tracker:
        # Enable batch pricing for compatible operations
        tracker.set_batch_mode(True)
        yield tracker


def ai_usage_tracked(
    operation_type: Optional[str] = None,
    tracker: Optional[AIUsageTracker] = None,
    **context_kwargs,
):
    """
    Decorator for automatic AI usage tracking on functions/methods.

    Can be used with or without arguments:
    - @ai_usage_tracked
    - @ai_usage_tracked()
    - @ai_usage_tracked(operation_type="custom_op")
    - @ai_usage_tracked(job_id="job-123", batch_id="batch-456")

    Args:
        operation_type: Type of operation. Defaults to function name.
        tracker: Optional tracker instance to use.
        **context_kwargs: Additional context metadata to include.

    Returns:
        Decorated function that automatically tracks AI usage.

    Example:
        @ai_usage_tracked(operation_type="receipt_processing")
        def process_receipt(receipt_id: str, image_url: str):
            # Function automatically tracked
            result = openai_client.chat.completions.create(...)
            return result

        @ai_usage_tracked
        async def analyze_text(text: str):
            # Async functions also supported
            response = await anthropic_client.messages.create(...)
            return response
    """
    import functools
    import inspect

    def decorator(func):
        # Determine if function is async
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Use function name as default operation type
            op_type = operation_type or func.__name__

            # Extract any tracking-related kwargs from function call
            tracking_context = {}
            for key in list(kwargs.keys()):
                if key in ["job_id", "batch_id", "user_id", "github_pr"]:
                    tracking_context[key] = kwargs.pop(key)

            # Merge with decorator context
            merged_context = {**context_kwargs, **tracking_context}

            with ai_usage_context(
                op_type, tracker=tracker, **merged_context
            ) as ctx_tracker:
                # Make tracker available to function if it accepts it
                sig = inspect.signature(func)
                if "tracker" in sig.parameters:
                    kwargs["tracker"] = ctx_tracker

                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Use function name as default operation type
            op_type = operation_type or func.__name__

            # Extract any tracking-related kwargs from function call
            tracking_context = {}
            for key in list(kwargs.keys()):
                if key in ["job_id", "batch_id", "user_id", "github_pr"]:
                    tracking_context[key] = kwargs.pop(key)

            # Merge with decorator context
            merged_context = {**context_kwargs, **tracking_context}

            with ai_usage_context(
                op_type, tracker=tracker, **merged_context
            ) as ctx_tracker:
                # Make tracker available to function if it accepts it
                sig = inspect.signature(func)
                if "tracker" in sig.parameters:
                    kwargs["tracker"] = ctx_tracker

                return await func(*args, **kwargs)

        # Return appropriate wrapper
        return async_wrapper if is_async else sync_wrapper

    # Handle decorator being used with or without parentheses
    if callable(operation_type) and tracker is None and not context_kwargs:
        # Decorator used without parentheses: @ai_usage_tracked
        func = operation_type
        operation_type = None
        return decorator(func)
    else:
        # Decorator used with arguments: @ai_usage_tracked(...)
        return decorator


@contextmanager
def partial_failure_context(
    operation_type: str,
    tracker: Optional[AIUsageTracker] = None,
    continue_on_error: bool = True,
    **kwargs,
) -> Generator[Dict[str, Any], None, None]:
    """
    Context manager for operations that can partially fail.

    Tracks individual sub-operation successes and failures,
    allowing the overall operation to continue even if some parts fail.

    Args:
        operation_type: Type of operation being performed
        tracker: Optional existing tracker instance
        continue_on_error: Whether to continue processing after errors
        **kwargs: Additional context metadata

    Yields:
        Dict containing:
            - tracker: The AIUsageTracker instance
            - errors: List of errors that occurred
            - success_count: Number of successful sub-operations
            - failure_count: Number of failed sub-operations

    Example:
        with partial_failure_context('batch_processing', batch_id='123') as ctx:
            for item in items:
                try:
                    process_item(item, ctx['tracker'])
                    ctx['success_count'] += 1
                except Exception as e:
                    ctx['errors'].append({'item': item, 'error': str(e)})
                    ctx['failure_count'] += 1
                    if not continue_on_error:
                        raise
    """
    # Create operation context
    operation_ctx = {
        "tracker": tracker,
        "errors": [],
        "success_count": 0,
        "failure_count": 0,
        "continue_on_error": continue_on_error,
    }

    with ai_usage_context(
        operation_type, tracker=tracker, partial_failure_enabled=True, **kwargs
    ) as ctx_tracker:
        operation_ctx["tracker"] = ctx_tracker

        try:
            yield operation_ctx
        finally:
            # Add partial failure summary to context
            ctx_tracker.add_context_metadata(
                {
                    "partial_failure_summary": {
                        "total_operations": operation_ctx["success_count"]
                        + operation_ctx["failure_count"],
                        "successful": operation_ctx["success_count"],
                        "failed": operation_ctx["failure_count"],
                        "error_count": len(operation_ctx["errors"]),
                        "errors": operation_ctx["errors"][
                            :10
                        ],  # Limit to first 10 errors
                    }
                }
            )
