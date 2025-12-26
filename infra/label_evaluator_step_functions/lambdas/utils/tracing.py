"""LangSmith tracing utilities for Lambda functions.

Provides trace propagation across Step Function Lambda invocations,
allowing a unified trace to span the entire workflow.

Key concept: Deterministic trace IDs based on Step Function execution ARN.
This ensures:
- One trace per execution (no collisions between executions)
- Safe retries (same IDs regenerated on retry)
- Proper parent-child relationships across Lambda invocations

Usage:
    # In the FIRST Lambda (creates root trace):
    def handler(event, context):
        execution_arn = event["execution_arn"]  # From Step Function

        trace_info = create_execution_trace(
            execution_arn=execution_arn,
            state_name="DiscoverPatterns",
            name="label_evaluator",
        )

        # ... your business logic ...

        return {
            "result": "...",
            "trace_id": trace_info["trace_id"],
            "root_run_id": trace_info["root_run_id"],
        }

    # In subsequent Lambda handlers:
    def handler(event, context):
        with state_trace(
            execution_arn=event["execution_arn"],
            state_name="ComputePatterns",
            trace_id=event["trace_id"],
            root_run_id=event["root_run_id"],
        ) as trace_ctx:
            # ... your business logic ...
            result = {"patterns": "..."}
            return trace_ctx.wrap_output(result)
"""

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Version identifier for debugging - helps verify correct module is loaded in Lambda
TRACING_VERSION = "2024-12-26-v4-per-receipt"

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


# Namespace for deterministic UUID generation (UUID5)
# This ensures consistent trace IDs across retries
TRACE_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# Keys for trace propagation through Step Function
TRACE_ID_KEY = "trace_id"
ROOT_RUN_ID_KEY = "root_run_id"
ROOT_DOTTED_ORDER_KEY = "root_dotted_order"

# Legacy header key (for backwards compatibility)
LANGSMITH_HEADERS_KEY = "langsmith_headers"


def generate_trace_id(execution_arn: str) -> str:
    """Generate a deterministic trace ID from Step Function execution ARN.

    The same execution ARN always produces the same trace ID, ensuring:
    - One trace per execution
    - Safe retries (same ID regenerated)
    """
    return str(uuid.uuid5(TRACE_NAMESPACE, execution_arn))


def generate_root_run_id(execution_arn: str) -> str:
    """Generate a deterministic root run ID for the execution.

    IMPORTANT: For LangSmith, the root run's ID MUST equal the trace_id.
    This is because LangSmith uses dotted_order which must start with trace_id.
    """
    # Root run ID must equal trace_id for LangSmith compatibility
    return generate_trace_id(execution_arn)


def generate_state_run_id(
    execution_arn: str,
    state_name: str,
    map_index: Optional[int] = None,
    attempt: int = 0,
) -> str:
    """Generate a deterministic run ID for a state invocation.

    Args:
        execution_arn: Step Function execution ARN
        state_name: Name of the state (Lambda task)
        map_index: Index in Map state (None if not in a Map)
        attempt: Retry attempt number (0 for first attempt)

    Returns:
        Deterministic UUID string for this state invocation
    """
    parts = [execution_arn, state_name]
    if map_index is not None:
        parts.append(str(map_index))
    parts.append(str(attempt))
    return str(uuid.uuid5(TRACE_NAMESPACE, ":".join(parts)))


# =============================================================================
# Per-Receipt Trace ID Generation
# =============================================================================


def generate_receipt_trace_id(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
) -> str:
    """Generate a deterministic trace ID for a specific receipt.

    Creates a unique trace ID per receipt within an execution, ensuring:
    - One trace per receipt (not per execution)
    - Safe retries (same ID regenerated)
    - Proper isolation between receipts

    Args:
        execution_arn: Step Function execution ARN
        image_id: Unique image identifier
        receipt_id: Receipt number within the image

    Returns:
        Deterministic UUID string for this receipt's trace
    """
    parts = [execution_arn, image_id, str(receipt_id)]
    return str(uuid.uuid5(TRACE_NAMESPACE, ":".join(parts)))


def generate_receipt_root_run_id(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
) -> str:
    """Generate a deterministic root run ID for a receipt trace.

    IMPORTANT: For LangSmith, the root run's ID MUST equal the trace_id.
    This is because LangSmith uses dotted_order which must start with trace_id.

    Args:
        execution_arn: Step Function execution ARN
        image_id: Unique image identifier
        receipt_id: Receipt number within the image

    Returns:
        Root run ID (equals trace_id for LangSmith compatibility)
    """
    return generate_receipt_trace_id(execution_arn, image_id, receipt_id)


def generate_receipt_state_run_id(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
    state_name: str,
    attempt: int = 0,
) -> str:
    """Generate a deterministic run ID for a state within a receipt trace.

    Args:
        execution_arn: Step Function execution ARN
        image_id: Unique image identifier
        receipt_id: Receipt number within the image
        state_name: Name of the state (e.g., "EvaluateLabels", "LLMReview")
        attempt: Retry attempt number (0 for first attempt)

    Returns:
        Deterministic UUID string for this state invocation
    """
    parts = [execution_arn, image_id, str(receipt_id), state_name, str(attempt)]
    return str(uuid.uuid5(TRACE_NAMESPACE, ":".join(parts)))


@dataclass
class TraceContext:
    """Context manager result for trace operations."""

    run_tree: Optional[Any] = None
    headers: Optional[dict] = None
    # New fields for deterministic tracing
    trace_id: Optional[str] = None
    root_run_id: Optional[str] = None

    def wrap_output(self, output: dict) -> dict:
        """Add trace info to Lambda output for propagation.

        Adds both legacy headers and new deterministic IDs.
        """
        # Legacy headers (for backwards compatibility)
        output[LANGSMITH_HEADERS_KEY] = self.headers
        # New deterministic IDs
        if self.trace_id:
            output[TRACE_ID_KEY] = self.trace_id
        if self.root_run_id:
            output[ROOT_RUN_ID_KEY] = self.root_run_id
        return output

    def get_child_headers(self) -> Optional[dict]:
        """Get headers for creating child traces."""
        if self.run_tree and hasattr(self.run_tree, "to_headers"):
            return self.run_tree.to_headers()
        return self.headers

    def set_outputs(self, outputs: dict) -> None:
        """Set the outputs on the current run tree.

        Call this before the trace context exits to capture outputs.
        """
        if self.run_tree is not None:
            try:
                self.run_tree.outputs = outputs
            except Exception as e:
                logger.warning("Failed to set trace outputs: %s", e)


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


def _sanitize_event_for_trace(event: dict, max_str_len: int = 1000) -> dict:
    """Create a sanitized copy of event for trace inputs.

    Excludes sensitive fields and truncates large values.
    """
    # Fields to exclude entirely
    exclude_keys = {
        LANGSMITH_HEADERS_KEY,
        "langsmith_headers",
        "api_key",
        "secret",
        "password",
        "token",
    }

    # Fields to summarize (show count/type instead of full data)
    summarize_keys = {"words", "labels", "issues", "receipts", "patterns"}

    result = {}
    for key, value in event.items():
        if key.lower() in exclude_keys or key in exclude_keys:
            continue

        if key in summarize_keys and isinstance(value, list):
            result[key] = f"<list of {len(value)} items>"
        elif isinstance(value, str) and len(value) > max_str_len:
            result[key] = value[:max_str_len] + "..."
        elif isinstance(value, dict):
            # Recursively sanitize nested dicts (one level)
            result[key] = _sanitize_event_for_trace(value, max_str_len)
        else:
            result[key] = value

    return result


@contextmanager
def start_trace(
    name: str,
    event: dict,
    run_type: str = "chain",
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    inputs: Optional[dict] = None,
):
    """Start a new LangSmith trace (root of trace hierarchy).

    Use this in the first Lambda of a Step Function workflow.

    Args:
        name: Name for the trace (e.g., "label_evaluator_workflow")
        event: Lambda event dict (used to extract execution_id, etc.)
        run_type: Type of run ("chain", "llm", "tool", etc.)
        metadata: Optional metadata to attach
        tags: Optional tags for filtering in LangSmith
        inputs: Optional inputs to capture in the trace (defaults to event)

    Yields:
        TraceContext with run_tree and headers for propagation

    Example:
        def handler(event, context):
            with start_trace("fetch_training_data", event, inputs={"key": "value"}) as trace_ctx:
                result = do_work()
                trace_ctx.set_outputs(result)
                return trace_ctx.wrap_output(result)
    """
    if not HAS_LANGSMITH or _RunTree is None:
        logger.info("LangSmith not available (HAS_LANGSMITH=%s, _RunTree=%s)", HAS_LANGSMITH, _RunTree)
        yield TraceContext()
        return

    logger.info("Starting trace: %s (HAS_LANGSMITH=%s)", name, HAS_LANGSMITH)

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

    trace_tags = list(tags) if tags else []
    trace_tags.extend(["step-function", "label-evaluator"])

    # Use provided inputs or sanitize event for trace inputs
    trace_inputs = inputs
    if trace_inputs is None:
        # Create a sanitized copy of the event (exclude large/sensitive fields)
        trace_inputs = _sanitize_event_for_trace(event)

    try:
        run_tree = _RunTree(
            name=name,
            run_type=run_type,
            inputs=trace_inputs,
            extra={"metadata": trace_metadata},
            tags=trace_tags,
        )
        run_tree.post()

        ctx = TraceContext(
            run_tree=run_tree,
            headers=run_tree.to_headers(),
        )

        logger.info("Trace started, yielding context (run_id=%s)", run_tree.id)
        yield ctx

        # End the run successfully
        run_tree.end()
        run_tree.patch()
        logger.info("Trace ended and patched (run_id=%s)", run_tree.id)

    except Exception as e:
        logger.warning("Failed to create trace: %s", e, exc_info=True)
        yield TraceContext()


@contextmanager
def resume_trace(
    name: str,
    event: dict,
    run_type: str = "chain",
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    inputs: Optional[dict] = None,
):
    """Resume an existing trace as a child (or start new if no parent).

    Use this in Lambda handlers after the first one.

    Args:
        name: Name for this step (e.g., "compute_patterns")
        event: Lambda event dict (should contain langsmith_headers)
        run_type: Type of run ("chain", "llm", "tool", etc.)
        metadata: Optional metadata to attach
        tags: Optional tags for filtering
        inputs: Optional inputs to capture in the trace (defaults to sanitized event)

    Yields:
        TraceContext with headers for further propagation

    Example:
        def handler(event, context):
            with resume_trace("evaluate_labels", event) as trace_ctx:
                result = do_evaluation()
                trace_ctx.set_outputs(result)
                return trace_ctx.wrap_output(result)
    """
    parent_headers = extract_trace_headers(event)

    if not HAS_LANGSMITH:
        logger.debug("LangSmith not available, skipping trace")
        yield TraceContext(headers=parent_headers)
        return

    if not parent_headers:
        # No parent trace - start a new one
        with start_trace(name, event, run_type, metadata, tags, inputs) as ctx:
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

    trace_tags = list(tags) if tags else []
    trace_tags.append("step-function")

    # Use provided inputs or sanitize event for trace inputs
    trace_inputs = inputs
    if trace_inputs is None:
        trace_inputs = _sanitize_event_for_trace(event)

    try:
        if _RunTree is not None:
            # Create a child run for this step, linked to parent via headers
            child_run = _RunTree(
                name=name,
                run_type=run_type,
                inputs=trace_inputs,
                extra={"metadata": trace_metadata},
                tags=trace_tags,
                parent=parent_headers,  # Link to parent via headers
            )
            child_run.post()

            # Use headers for tracing_context (consistent across all helpers)
            child_headers = child_run.to_headers()
            if _tracing_context is not None:
                with _tracing_context(parent=child_headers):
                    ctx = TraceContext(
                        run_tree=child_run,
                        headers=child_headers,
                    )
                    yield ctx
            else:
                ctx = TraceContext(
                    run_tree=child_run,
                    headers=child_headers,
                )
                yield ctx

            child_run.end()
            child_run.patch()
        elif _tracing_context is not None:
            # Fallback: just set up tracing context without RunTree
            with _tracing_context(parent=parent_headers):
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
    inputs: Optional[dict] = None,
):
    """Create a child trace within an existing trace context.

    Use this for creating sub-spans within a Lambda handler.

    Args:
        name: Name for this child span
        parent_ctx: Parent TraceContext
        run_type: Type of run
        metadata: Optional metadata
        inputs: Optional inputs to capture in the trace

    Yields:
        TraceContext for the child span

    Example:
        with child_trace("llm_call", trace_ctx, run_type="llm", inputs={"prompt": prompt}) as child_ctx:
            response = llm.invoke(prompt)
            child_ctx.set_outputs({"response": response})
    """
    logger.info(
        "[child_trace v%s] Creating child '%s' (has_langsmith=%s, parent_run_tree=%s)",
        TRACING_VERSION,
        name,
        HAS_LANGSMITH,
        parent_ctx.run_tree is not None,
    )

    if not HAS_LANGSMITH or parent_ctx.run_tree is None:
        logger.warning("[child_trace] No LangSmith or no parent run_tree - yielding empty context")
        yield TraceContext(headers=parent_ctx.headers)
        return

    try:
        if hasattr(parent_ctx.run_tree, "create_child"):
            child = parent_ctx.run_tree.create_child(
                name=name,
                run_type=run_type,
                inputs=inputs or {},
                extra={"metadata": metadata or {}},
            )
            child.post()

            # Use headers for tracing_context (consistent across all helpers)
            child_headers = child.to_headers()
            logger.info(
                "[child_trace] Created child id=%s, trace_id=%s, headers=%s, using_tracing_context=%s",
                child.id,
                child.trace_id,
                list(child_headers.keys()) if child_headers else None,
                _tracing_context is not None,
            )

            if _tracing_context is not None:
                logger.info("[child_trace] Activating tracing_context with parent=headers")
                with _tracing_context(parent=child_headers):
                    yield TraceContext(
                        run_tree=child,
                        headers=child_headers,
                    )
            else:
                logger.warning("[child_trace] _tracing_context is None - LangGraph/LLM calls may not be nested")
                yield TraceContext(
                    run_tree=child,
                    headers=child_headers,
                )

            child.end()
            child.patch()
            logger.info("[child_trace] Child '%s' completed and patched", name)
        else:
            logger.warning("[child_trace] Parent run_tree has no create_child method")
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


# =============================================================================
# NEW: Deterministic trace management for Step Functions
# =============================================================================


@dataclass
class ExecutionTraceInfo:
    """Information about the execution-level trace."""

    trace_id: str
    root_run_id: str
    root_dotted_order: Optional[str] = None
    run_tree: Optional[Any] = None

    def to_dict(self) -> dict:
        """Return trace info for passing through Step Function."""
        result = {
            TRACE_ID_KEY: self.trace_id,
            ROOT_RUN_ID_KEY: self.root_run_id,
        }
        if self.root_dotted_order:
            result[ROOT_DOTTED_ORDER_KEY] = self.root_dotted_order
        return result


def create_execution_trace(
    execution_arn: str,
    name: str,
    inputs: Optional[dict] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    enable_tracing: bool = True,
) -> ExecutionTraceInfo:
    """Create the root trace for a Step Function execution.

    Call this ONCE in the FIRST Lambda of the workflow. The returned
    trace_id and root_run_id should be passed through the Step Function
    to all subsequent Lambdas.

    Args:
        execution_arn: Step Function execution ARN (from $$.Execution.Id)
        name: Name for the root trace (e.g., "label_evaluator")
        inputs: Optional inputs to capture
        metadata: Optional metadata
        tags: Optional tags
        enable_tracing: If False, skip trace creation (default: True)

    Returns:
        ExecutionTraceInfo with trace_id, root_run_id, and run_tree
    """
    trace_id = generate_trace_id(execution_arn)
    root_run_id = generate_root_run_id(execution_arn)

    if not enable_tracing:
        logger.info("Tracing disabled, skipping trace creation")
        return ExecutionTraceInfo(trace_id=trace_id, root_run_id=root_run_id)

    if not HAS_LANGSMITH or _RunTree is None:
        logger.info("LangSmith not available, returning empty trace info")
        return ExecutionTraceInfo(trace_id=trace_id, root_run_id=root_run_id)

    logger.info(
        "Creating execution trace: %s (trace_id=%s, root_run_id=%s)",
        name,
        trace_id[:8],
        root_run_id[:8],
    )

    try:
        run_tree = _RunTree(
            id=root_run_id,
            trace_id=trace_id,
            name=name,
            run_type="chain",
            inputs=inputs or {},
            extra={"metadata": metadata or {}},
            tags=tags or [],
        )
        run_tree.post()

        # Capture the root run's dotted_order for propagation to child runs
        root_dotted_order = getattr(run_tree, "dotted_order", None)
        logger.info(
            "Root trace created with dotted_order=%s",
            root_dotted_order[:30] if root_dotted_order else "None",
        )

        return ExecutionTraceInfo(
            trace_id=trace_id,
            root_run_id=root_run_id,
            root_dotted_order=root_dotted_order,
            run_tree=run_tree,
        )

    except Exception as e:
        logger.warning("Failed to create execution trace: %s", e, exc_info=True)
        return ExecutionTraceInfo(trace_id=trace_id, root_run_id=root_run_id)


def end_execution_trace(trace_info: ExecutionTraceInfo, outputs: Optional[dict] = None) -> None:
    """End the root execution trace.

    Call this at the end of the FIRST Lambda, after all work is done.
    """
    if trace_info.run_tree is None:
        return

    try:
        if outputs:
            trace_info.run_tree.outputs = outputs
        trace_info.run_tree.end()
        trace_info.run_tree.patch()
        logger.info("Execution trace ended (root_run_id=%s)", trace_info.root_run_id[:8])
    except Exception as e:
        logger.warning("Failed to end execution trace: %s", e)


# =============================================================================
# Per-Receipt Trace Management
# =============================================================================


@dataclass
class ReceiptTraceInfo:
    """Information about a per-receipt trace."""

    trace_id: str
    root_run_id: str
    root_dotted_order: Optional[str] = None
    run_tree: Optional[Any] = None
    # Receipt identification
    image_id: str = ""
    receipt_id: int = 0
    merchant_name: str = ""

    def to_dict(self) -> dict:
        """Return trace info for passing through Step Function."""
        result = {
            TRACE_ID_KEY: self.trace_id,
            ROOT_RUN_ID_KEY: self.root_run_id,
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "merchant_name": self.merchant_name,
        }
        if self.root_dotted_order:
            result[ROOT_DOTTED_ORDER_KEY] = self.root_dotted_order
        return result


def create_receipt_trace(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
    merchant_name: str,
    name: str = "ReceiptEvaluation",
    inputs: Optional[dict] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    enable_tracing: bool = True,
) -> ReceiptTraceInfo:
    """Create the root trace for a specific receipt.

    Each receipt gets its own trace, allowing for:
    - Per-receipt visibility in LangSmith
    - Complete trace including patterns, evaluation, and LLM review
    - Easy filtering by image_id, receipt_id, or merchant_name

    Call this in EvaluateLabels Lambda to create the receipt's root trace.

    Args:
        execution_arn: Step Function execution ARN (from $$.Execution.Id)
        image_id: Unique image identifier
        receipt_id: Receipt number within the image
        merchant_name: Name of the merchant
        name: Name for the root trace (default: "ReceiptEvaluation")
        inputs: Optional inputs to capture
        metadata: Optional additional metadata
        tags: Optional tags
        enable_tracing: If False, skip trace creation (default: True)

    Returns:
        ReceiptTraceInfo with trace_id, root_run_id, and run_tree
    """
    trace_id = generate_receipt_trace_id(execution_arn, image_id, receipt_id)
    root_run_id = generate_receipt_root_run_id(execution_arn, image_id, receipt_id)

    # Build base trace info (returned even if tracing disabled)
    base_info = ReceiptTraceInfo(
        trace_id=trace_id,
        root_run_id=root_run_id,
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
    )

    if not enable_tracing:
        logger.info("Tracing disabled, skipping receipt trace creation")
        return base_info

    if not HAS_LANGSMITH or _RunTree is None:
        logger.info("LangSmith not available, returning empty receipt trace info")
        return base_info

    # Build metadata with receipt identification
    trace_metadata = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "execution_arn": execution_arn,
        **(metadata or {}),
    }

    trace_tags = list(tags) if tags else []
    trace_tags.extend(["step-function", "label-evaluator", "per-receipt"])

    logger.info(
        "Creating receipt trace: %s (image_id=%s, receipt_id=%s, merchant=%s, trace_id=%s)",
        name,
        image_id[:8] if len(image_id) > 8 else image_id,
        receipt_id,
        merchant_name,
        trace_id[:8],
    )

    try:
        run_tree = _RunTree(
            id=root_run_id,
            trace_id=trace_id,
            name=name,
            run_type="chain",
            inputs=inputs or {},
            extra={"metadata": trace_metadata},
            tags=trace_tags,
        )
        run_tree.post()

        # Capture the root run's dotted_order for propagation to child runs
        root_dotted_order = getattr(run_tree, "dotted_order", None)
        logger.info(
            "Receipt trace created with dotted_order=%s",
            root_dotted_order[:30] if root_dotted_order else "None",
        )

        return ReceiptTraceInfo(
            trace_id=trace_id,
            root_run_id=root_run_id,
            root_dotted_order=root_dotted_order,
            run_tree=run_tree,
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
        )

    except Exception as e:
        logger.warning("Failed to create receipt trace: %s", e, exc_info=True)
        return base_info


def end_receipt_trace(
    trace_info: ReceiptTraceInfo,
    outputs: Optional[dict] = None,
) -> None:
    """End the receipt trace.

    Call this after all receipt processing is complete (after LLM review).
    """
    if trace_info.run_tree is None:
        return

    try:
        if outputs:
            trace_info.run_tree.outputs = outputs
        trace_info.run_tree.end()
        trace_info.run_tree.patch()
        logger.info(
            "Receipt trace ended (image_id=%s, receipt_id=%s)",
            trace_info.image_id[:8] if len(trace_info.image_id) > 8 else trace_info.image_id,
            trace_info.receipt_id,
        )
    except Exception as e:
        logger.warning("Failed to end receipt trace: %s", e)


@contextmanager
def receipt_state_trace(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
    state_name: str,
    trace_id: str,
    root_run_id: str,
    root_dotted_order: Optional[str] = None,
    attempt: int = 0,
    inputs: Optional[dict] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    enable_tracing: bool = True,
):
    """Create a child trace for a state within a receipt's trace.

    Use this in LLMReview Lambda to join the receipt's existing trace.

    Args:
        execution_arn: Step Function execution ARN
        image_id: Unique image identifier
        receipt_id: Receipt number within the image
        state_name: Name of this state (e.g., "LLMReview")
        trace_id: Receipt trace ID (from EvaluateLabels output)
        root_run_id: Receipt root run ID (from EvaluateLabels output)
        root_dotted_order: Parent's dotted_order for trace linking
        attempt: Retry attempt number
        inputs: Optional inputs to capture
        metadata: Optional metadata
        tags: Optional tags
        enable_tracing: If False, skip trace creation

    Yields:
        TraceContext with run_tree for this state
    """
    if not enable_tracing:
        logger.info("Tracing disabled for receipt state '%s', skipping", state_name)
        yield TraceContext()
        return

    state_run_id = generate_receipt_state_run_id(
        execution_arn, image_id, receipt_id, state_name, attempt
    )

    logger.info(
        "[receipt_state_trace v%s] Starting trace for '%s' (image_id=%s, receipt_id=%s)",
        TRACING_VERSION,
        state_name,
        image_id[:8] if len(image_id) > 8 else image_id,
        receipt_id,
    )

    if not HAS_LANGSMITH or _RunTree is None:
        logger.info("LangSmith not available, skipping receipt state trace")
        yield TraceContext()
        return

    # Generate child dotted_order from parent's
    child_dotted_order = None
    if root_dotted_order:
        child_dotted_order = _generate_child_dotted_order(root_dotted_order, state_run_id)
    else:
        logger.warning(
            "[receipt_state_trace] No root_dotted_order provided for %s - trace linking may fail!",
            state_name,
        )

    # Build metadata with receipt identification
    trace_metadata = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "state_name": state_name,
        **(metadata or {}),
    }

    try:
        run_tree_kwargs = {
            "id": state_run_id,
            "trace_id": trace_id,
            "parent_run_id": root_run_id,
            "name": state_name,
            "run_type": "chain",
            "inputs": inputs or {},
            "extra": {"metadata": trace_metadata},
            "tags": tags or [],
        }
        if child_dotted_order:
            run_tree_kwargs["dotted_order"] = child_dotted_order

        run_tree = _RunTree(**run_tree_kwargs)
        run_tree.post()

        run_tree_headers = run_tree.to_headers()

        if _tracing_context is not None:
            with _tracing_context(parent=run_tree_headers):
                ctx = TraceContext(
                    run_tree=run_tree,
                    headers=run_tree_headers,
                    trace_id=trace_id,
                    root_run_id=root_run_id,
                )
                yield ctx
        else:
            ctx = TraceContext(
                run_tree=run_tree,
                headers=run_tree_headers,
                trace_id=trace_id,
                root_run_id=root_run_id,
            )
            yield ctx

        run_tree.end()
        run_tree.patch()
        logger.info(
            "Receipt state trace ended (state=%s, image_id=%s, receipt_id=%s)",
            state_name,
            image_id[:8] if len(image_id) > 8 else image_id,
            receipt_id,
        )

    except Exception as e:
        logger.warning("Failed to create receipt state trace: %s", e, exc_info=True)
        yield TraceContext()


def _generate_child_dotted_order(parent_dotted_order: str, child_run_id: str) -> str:
    """Generate a dotted_order for a child run.

    LangSmith dotted_order format: {parent_dotted_order}.{timestamp}{run_id}
    - Timestamp: YYYYMMDDTHHMMSSffffffZ (22 chars: 8+1+6+6+1)
    - Run ID: UUID with dashes (36 chars)

    Example: 20251224T174151682753Z3fec4059-46c3-48e7-9abb-18a3de8277ca.20251224T175029523772Z019b517b-d893-7051-af16-c083cae44a11
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    # Format: 20251224T072227945197Z (22 chars)
    timestamp = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond:06d}Z"
    # Keep UUID with dashes (36 chars)
    return f"{parent_dotted_order}.{timestamp}{child_run_id}"


@contextmanager
def state_trace(
    execution_arn: str,
    state_name: str,
    trace_id: str,
    root_run_id: str,
    root_dotted_order: Optional[str] = None,
    map_index: Optional[int] = None,
    attempt: int = 0,
    inputs: Optional[dict] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    enable_tracing: bool = True,
):
    """Create a child trace for a Lambda state invocation.

    Use this in all Lambdas AFTER the first one. The trace will be a child
    of the root execution trace.

    IMPORTANT: root_dotted_order is required for proper trace linking.
    LangSmith validates that the first segment of dotted_order matches trace_id.

    Args:
        execution_arn: Step Function execution ARN
        state_name: Name of this state (used in trace name and ID generation)
        trace_id: Trace ID from first Lambda (via Step Function input)
        root_run_id: Root run ID from first Lambda (via Step Function input)
        root_dotted_order: Parent's dotted_order - REQUIRED for cross-Lambda traces
        map_index: Index in Map state (None if not in a Map)
        attempt: Retry attempt number
        inputs: Optional inputs to capture
        metadata: Optional metadata
        tags: Optional tags
        enable_tracing: If False, skip trace creation (default: True)

    Yields:
        TraceContext with run_tree for this state
    """
    if not enable_tracing:
        logger.info("Tracing disabled for state '%s', skipping", state_name)
        yield TraceContext()
        return

    state_run_id = generate_state_run_id(execution_arn, state_name, map_index, attempt)

    logger.info(
        "[state_trace v%s] Starting trace for '%s' (has_langsmith=%s, has_RunTree=%s, has_tracing_context=%s)",
        TRACING_VERSION,
        state_name,
        HAS_LANGSMITH,
        _RunTree is not None,
        _tracing_context is not None,
    )

    if not HAS_LANGSMITH or _RunTree is None:
        logger.info("LangSmith not available, skipping state trace")
        yield TraceContext()
        return

    # Generate child dotted_order from parent's (required for cross-Lambda trace linking)
    child_dotted_order = None
    if root_dotted_order:
        child_dotted_order = _generate_child_dotted_order(root_dotted_order, state_run_id)
        logger.info(
            "[state_trace v%s] Creating state trace: %s (run_id=%s, parent=%s, dotted_order_len=%d)",
            TRACING_VERSION,
            state_name,
            state_run_id[:8],
            root_run_id[:8],
            len(child_dotted_order),
        )
    else:
        logger.warning(
            "[state_trace v%s] No root_dotted_order provided for %s - trace linking may fail!",
            TRACING_VERSION,
            state_name,
        )

    try:
        # Build RunTree with our generated dotted_order
        # LangSmith validates first segment matches trace_id
        run_tree_kwargs = {
            "id": state_run_id,
            "trace_id": trace_id,
            "parent_run_id": root_run_id,
            "name": state_name,
            "run_type": "chain",
            "inputs": inputs or {},
            "extra": {"metadata": metadata or {}},
            "tags": tags or [],
        }
        if child_dotted_order:
            run_tree_kwargs["dotted_order"] = child_dotted_order

        run_tree = _RunTree(**run_tree_kwargs)
        run_tree.post()
        logger.info(
            "[state_trace] RunTree posted: id=%s, trace_id=%s",
            run_tree.id,
            run_tree.trace_id,
        )

        # Use headers for tracing_context (consistent across all helpers)
        run_tree_headers = run_tree.to_headers()
        logger.info(
            "[state_trace] Headers created: %s, activating tracing_context=%s",
            list(run_tree_headers.keys()) if run_tree_headers else None,
            _tracing_context is not None,
        )

        if _tracing_context is not None:
            logger.info("[state_trace] Activating tracing_context with parent=headers")
            with _tracing_context(parent=run_tree_headers):
                ctx = TraceContext(
                    run_tree=run_tree,
                    headers=run_tree_headers,
                    trace_id=trace_id,
                    root_run_id=root_run_id,
                )
                yield ctx
        else:
            logger.warning("[state_trace] _tracing_context is None - nested calls may not be linked")
            ctx = TraceContext(
                run_tree=run_tree,
                headers=run_tree_headers,
                trace_id=trace_id,
                root_run_id=root_run_id,
            )
            yield ctx

        run_tree.end()
        run_tree.patch()
        logger.info("State trace ended (run_id=%s)", state_run_id[:8])

    except Exception as e:
        logger.warning("Failed to create state trace: %s", e, exc_info=True)
        yield TraceContext()
