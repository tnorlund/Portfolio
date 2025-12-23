"""LangSmith tracing utilities for Lambda functions.

Provides trace propagation across Step Function Lambda invocations,
allowing a unified trace to span the entire workflow.

Usage:
    # In a Lambda handler that starts a trace:
    def handler(event, context):
        with start_trace("fetch_training_data", event) as trace_ctx:
            # ... your business logic ...
            result = {"data": "..."}
            return trace_ctx.wrap_output(result)

    # In subsequent Lambda handlers:
    def handler(event, context):
        with resume_trace("evaluate_labels", event) as trace_ctx:
            # ... your business logic ...
            result = {"issues_found": 3}
            return trace_ctx.wrap_output(result)
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# LangSmith tracing - flush before Lambda exits
_get_langsmith_client: Optional[Callable[..., Any]] = None
_RunTree: Optional[type] = None
_tracing_context: Optional[Callable[..., Any]] = None
_get_current_run_tree: Optional[Callable[..., Any]] = None

try:
    from langsmith import tracing_context as _ls_tracing_context
    from langsmith.run_helpers import get_current_run_tree as _ls_get_current_run_tree
    from langsmith.run_trees import RunTree as _LsRunTree
    from langsmith.run_trees import get_cached_client

    _get_langsmith_client = get_cached_client
    _RunTree = _LsRunTree
    _tracing_context = _ls_tracing_context
    _get_current_run_tree = _ls_get_current_run_tree
    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False


# Header keys for trace propagation
LANGSMITH_HEADERS_KEY = "langsmith_headers"


@dataclass
class TraceContext:
    """Context manager result for trace operations."""

    run_tree: Optional[Any] = None
    headers: Optional[dict] = None

    def wrap_output(self, output: dict) -> dict:
        """Add trace headers to Lambda output for propagation."""
        if self.headers:
            output[LANGSMITH_HEADERS_KEY] = self.headers
        return output

    def get_child_headers(self) -> Optional[dict]:
        """Get headers for creating child traces."""
        if self.run_tree and hasattr(self.run_tree, "to_headers"):
            return self.run_tree.to_headers()
        return self.headers


def flush_langsmith_traces() -> None:
    """Flush pending LangSmith traces before Lambda returns.

    This should be called before Lambda exits to ensure all traces
    are sent to LangSmith. Safe to call even if LangSmith is not available.
    """
    if HAS_LANGSMITH and _get_langsmith_client is not None:
        try:
            client = _get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed")
        except Exception as e:
            logger.warning("Failed to flush LangSmith traces: %s", e)


def extract_trace_headers(event: dict) -> Optional[dict]:
    """Extract LangSmith trace headers from Lambda event."""
    return event.get(LANGSMITH_HEADERS_KEY)


@contextmanager
def start_trace(
    name: str,
    event: dict,
    run_type: str = "chain",
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
):
    """Start a new LangSmith trace (root of trace hierarchy).

    Use this in the first Lambda of a Step Function workflow.

    Args:
        name: Name for the trace (e.g., "label_evaluator_workflow")
        event: Lambda event dict (used to extract execution_id, etc.)
        run_type: Type of run ("chain", "llm", "tool", etc.)
        metadata: Optional metadata to attach
        tags: Optional tags for filtering in LangSmith

    Yields:
        TraceContext with run_tree and headers for propagation

    Example:
        def handler(event, context):
            with start_trace("fetch_training_data", event) as trace_ctx:
                result = do_work()
                return trace_ctx.wrap_output(result)
    """
    if not HAS_LANGSMITH or _RunTree is None:
        logger.debug("LangSmith not available, skipping trace")
        yield TraceContext()
        return

    # Build metadata from event
    trace_metadata = {
        "execution_id": event.get("execution_id", "unknown"),
        "lambda_name": name,
        **(metadata or {}),
    }

    # Add merchant info if available
    if "merchant_name" in event:
        trace_metadata["merchant_name"] = event["merchant_name"]
    if "merchant" in event and isinstance(event["merchant"], dict):
        trace_metadata["merchant_name"] = event["merchant"].get("merchant_name")

    trace_tags = tags or []
    trace_tags.extend(["step-function", "label-evaluator"])

    try:
        run_tree = _RunTree(
            name=name,
            run_type=run_type,
            extra={"metadata": trace_metadata},
            tags=trace_tags,
        )
        run_tree.post()

        ctx = TraceContext(
            run_tree=run_tree,
            headers=run_tree.to_headers(),
        )

        yield ctx

        # End the run successfully
        run_tree.end()
        run_tree.patch()

    except Exception as e:
        logger.warning("Failed to create trace: %s", e)
        yield TraceContext()


@contextmanager
def resume_trace(
    name: str,
    event: dict,
    run_type: str = "chain",
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
):
    """Resume an existing trace as a child (or start new if no parent).

    Use this in Lambda handlers after the first one.

    Args:
        name: Name for this step (e.g., "compute_patterns")
        event: Lambda event dict (should contain langsmith_headers)
        run_type: Type of run ("chain", "llm", "tool", etc.)
        metadata: Optional metadata to attach
        tags: Optional tags for filtering

    Yields:
        TraceContext with headers for further propagation

    Example:
        def handler(event, context):
            with resume_trace("evaluate_labels", event) as trace_ctx:
                result = do_evaluation()
                return trace_ctx.wrap_output(result)
    """
    parent_headers = extract_trace_headers(event)

    if not HAS_LANGSMITH:
        logger.debug("LangSmith not available, skipping trace")
        yield TraceContext(headers=parent_headers)
        return

    if not parent_headers:
        # No parent trace - start a new one
        with start_trace(name, event, run_type, metadata, tags) as ctx:
            yield ctx
        return

    # Resume as child of parent trace
    trace_metadata = {
        "execution_id": event.get("execution_id", "unknown"),
        "lambda_name": name,
        **(metadata or {}),
    }

    # Add receipt info if available
    if "image_id" in event:
        trace_metadata["image_id"] = event["image_id"]
    if "receipt_id" in event:
        trace_metadata["receipt_id"] = event["receipt_id"]
    if "receipt" in event and isinstance(event["receipt"], dict):
        trace_metadata["image_id"] = event["receipt"].get("image_id")
        trace_metadata["receipt_id"] = event["receipt"].get("receipt_id")

    trace_tags = tags or []
    trace_tags.append("step-function")

    try:
        if _tracing_context is not None:
            # Use tracing_context to resume the parent trace
            with _tracing_context(parent=parent_headers):
                # Create a child run for this step
                if _RunTree is not None:
                    child_run = _RunTree(
                        name=name,
                        run_type=run_type,
                        extra={"metadata": trace_metadata},
                        tags=trace_tags,
                    )
                    child_run.post()

                    ctx = TraceContext(
                        run_tree=child_run,
                        headers=child_run.to_headers(),
                    )

                    yield ctx

                    child_run.end()
                    child_run.patch()
                else:
                    yield TraceContext(headers=parent_headers)
        else:
            yield TraceContext(headers=parent_headers)

    except Exception as e:
        logger.warning("Failed to resume trace: %s", e)
        yield TraceContext(headers=parent_headers)


@contextmanager
def child_trace(
    name: str,
    parent_ctx: TraceContext,
    run_type: str = "chain",
    metadata: Optional[dict] = None,
):
    """Create a child trace within an existing trace context.

    Use this for creating sub-spans within a Lambda handler.

    Args:
        name: Name for this child span
        parent_ctx: Parent TraceContext
        run_type: Type of run
        metadata: Optional metadata

    Yields:
        TraceContext for the child span
    """
    if not HAS_LANGSMITH or parent_ctx.run_tree is None:
        yield TraceContext(headers=parent_ctx.headers)
        return

    try:
        if hasattr(parent_ctx.run_tree, "create_child"):
            child = parent_ctx.run_tree.create_child(
                name=name,
                run_type=run_type,
                extra={"metadata": metadata or {}},
            )
            child.post()

            yield TraceContext(
                run_tree=child,
                headers=child.to_headers(),
            )

            child.end()
            child.patch()
        else:
            yield TraceContext(headers=parent_ctx.headers)

    except Exception as e:
        logger.warning("Failed to create child trace: %s", e)
        yield TraceContext(headers=parent_ctx.headers)


def log_trace_event(
    ctx: TraceContext,
    event_name: str,
    data: Optional[dict] = None,
) -> None:
    """Log an event to the current trace.

    Args:
        ctx: Current TraceContext
        event_name: Name of the event
        data: Optional data to attach
    """
    if ctx.run_tree is None:
        return

    try:
        if hasattr(ctx.run_tree, "add_event"):
            ctx.run_tree.add_event(event_name, data or {})
    except Exception as e:
        logger.warning("Failed to log trace event: %s", e)
