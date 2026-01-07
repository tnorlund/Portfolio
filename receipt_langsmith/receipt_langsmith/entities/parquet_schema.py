"""Raw Parquet schema for LangSmith bulk exports.

This module defines the schema matching the 29-column Parquet format
from LangSmith's bulk export feature. JSON fields are kept as strings
and parsed separately.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LangSmithRunRaw(BaseModel):
    """Raw LangSmith Parquet export schema (29 columns).

    This schema matches the exact structure of LangSmith bulk export
    Parquet files. JSON fields (inputs, outputs, extra, tags, events,
    parent_run_ids) are kept as strings for efficient storage and
    parsed on demand.

    Columns:
        Trace hierarchy (5): id, trace_id, parent_run_id, dotted_order, is_root
        Parent list (1): parent_run_ids (JSON array)
        Execution (8): name, run_type, status, error, start_time, end_time,
                       first_token_time, trace_tier
        Token usage (6): total_tokens, prompt_tokens, completion_tokens,
                         total_cost, prompt_cost, completion_cost
        Data (4): inputs, outputs, extra, events (all JSON strings)
        Metadata (4): tags, feedback_stats, reference_example_id, session_id
        Tenant (1): tenant_id
    """

    model_config = {"extra": "ignore"}

    # Trace hierarchy
    id: str
    """Unique run identifier (UUID string)."""

    trace_id: str
    """Shared identifier for all runs in a workflow."""

    parent_run_id: Optional[str] = None
    """Immediate parent run ID."""

    dotted_order: Optional[str] = None
    """Sortable execution order string (e.g., '20260104T...')."""

    is_root: Optional[bool] = None
    """Whether this is a root (parent-less) run."""

    parent_run_ids: Optional[str] = None
    """JSON array string of all parent run IDs."""

    # Execution info
    name: str
    """Run name (e.g., 'ReceiptEvaluation')."""

    run_type: str
    """Run type: 'chain', 'llm', or 'tool'."""

    status: str
    """Execution status: 'success', 'error', etc."""

    error: Optional[str] = None
    """Error message if status is 'error'."""

    start_time: Optional[datetime] = None
    """When the run started (timestamp)."""

    end_time: Optional[datetime] = None
    """When the run completed (timestamp)."""

    first_token_time: Optional[datetime] = None
    """When the first token was generated (LLM runs only)."""

    trace_tier: Optional[str] = None
    """Trace tier classification (e.g., 'shortlived')."""

    # Token usage
    total_tokens: Optional[int] = None
    """Total tokens used (prompt + completion)."""

    prompt_tokens: Optional[int] = None
    """Input/prompt tokens."""

    completion_tokens: Optional[int] = None
    """Output/completion tokens."""

    total_cost: Optional[float] = None
    """Total cost in USD (if available)."""

    prompt_cost: Optional[float] = None
    """Prompt cost in USD (if available)."""

    completion_cost: Optional[float] = None
    """Completion cost in USD (if available)."""

    # JSON string fields (parsed separately)
    inputs: Optional[str] = None
    """JSON string of run inputs."""

    outputs: Optional[str] = None
    """JSON string of run outputs."""

    extra: Optional[str] = None
    """JSON string with metadata and runtime info."""

    events: Optional[str] = None
    """JSON array string of events."""

    tags: Optional[str] = None
    """JSON array string of tags."""

    # Metadata
    feedback_stats: Optional[str] = None
    """JSON string of feedback statistics."""

    reference_example_id: Optional[str] = None
    """Reference example ID if this is an evaluation run."""

    session_id: Optional[str] = None
    """LangSmith project/session ID."""

    tenant_id: Optional[str] = None
    """LangSmith tenant/workspace ID."""

    latency: Optional[float] = None
    """Latency in seconds (if computed)."""
