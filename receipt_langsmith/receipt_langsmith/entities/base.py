"""Base trace schemas for LangSmith data.

This module defines the core schemas for parsed LangSmith traces:
- TraceMetadata: Extracted metadata from extra.metadata
- RuntimeInfo: Runtime environment information
- LangSmithRun: Fully parsed trace with typed fields
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TraceMetadata(BaseModel):
    """Parsed metadata from the extra.metadata JSON field.

    This contains business-level context added when creating traces.
    """

    execution_id: Optional[str] = None
    """Step Function execution ID or batch identifier."""

    merchant_name: Optional[str] = None
    """Name of the merchant on the receipt."""

    image_id: Optional[str] = None
    """UUID of the receipt image."""

    receipt_id: Optional[int] = None
    """Receipt number within the image (1-indexed)."""

    state_name: Optional[str] = None
    """Current state/step name in the workflow."""

    model: Optional[str] = None
    """LLM model used (e.g., 'gpt-4o-mini')."""

    temperature: Optional[float] = None
    """LLM temperature setting."""

    provider: Optional[str] = None
    """LLM provider (e.g., 'openai', 'anthropic')."""


class RuntimeInfo(BaseModel):
    """Runtime environment information from extra.runtime."""

    sdk_version: Optional[str] = None
    """LangSmith SDK version (e.g., '0.6.0')."""

    langchain_core_version: Optional[str] = None
    """LangChain core version."""

    runtime_version: Optional[str] = None
    """Python version (e.g., '3.12.12')."""

    platform: Optional[str] = None
    """Platform info (e.g., 'Linux-...')."""


class LangSmithRun(BaseModel):
    """Fully parsed LangSmith run with typed fields.

    This is the primary model for working with trace data. It includes:
    - Trace hierarchy information (id, parent_run_id, trace_id)
    - Execution details (name, run_type, status, timing)
    - Token usage
    - Parsed JSON fields (inputs, outputs, metadata)
    - Child runs for tree reconstruction
    """

    model_config = {"arbitrary_types_allowed": True}

    # Trace hierarchy
    id: str
    """Unique run identifier (UUID)."""

    trace_id: str
    """Shared identifier for all runs in a workflow."""

    parent_run_id: Optional[str] = None
    """Immediate parent run ID (None for root runs)."""

    dotted_order: Optional[str] = None
    """Sortable string for execution order within trace."""

    is_root: bool = False
    """Whether this is a root (parent-less) run."""

    # Execution info
    name: str
    """Run name (e.g., 'ReceiptEvaluation', 'EvaluateCurrencyLabels')."""

    run_type: str
    """Run type: 'chain', 'llm', or 'tool'."""

    status: str
    """Execution status: 'success', 'error', etc."""

    start_time: Optional[datetime] = None
    """When the run started (None if missing from trace data)."""

    end_time: Optional[datetime] = None
    """When the run completed (None if missing from trace data)."""

    duration_ms: int = 0
    """Duration in milliseconds (computed from start_time/end_time)."""

    error: Optional[str] = None
    """Error message if status is 'error'."""

    # Token usage
    total_tokens: int = 0
    """Total tokens used (prompt + completion)."""

    prompt_tokens: int = 0
    """Input/prompt tokens."""

    completion_tokens: int = 0
    """Output/completion tokens."""

    # Parsed JSON fields
    inputs: dict[str, Any] = Field(default_factory=dict)
    """Parsed inputs from the run."""

    outputs: dict[str, Any] = Field(default_factory=dict)
    """Parsed outputs from the run."""

    metadata: TraceMetadata = Field(default_factory=TraceMetadata)
    """Parsed metadata from extra.metadata."""

    runtime: RuntimeInfo = Field(default_factory=RuntimeInfo)
    """Runtime environment info from extra.runtime."""

    tags: list[str] = Field(default_factory=list)
    """Tags/labels attached to the run."""

    # Hierarchy (populated by TraceTreeBuilder)
    children: list[LangSmithRun] = Field(default_factory=list)
    """Child runs (populated when building trace tree)."""

    def get_child_by_name(self, name: str) -> Optional[LangSmithRun]:
        """Get a child run by name.

        Args:
            name: The run name to find (e.g., 'EvaluateCurrencyLabels')

        Returns:
            The matching child run, or None if not found
        """
        for child in self.children:
            if child.name == name:
                return child
        return None

    def get_children_by_type(self, run_type: str) -> list[LangSmithRun]:
        """Get all children of a specific run type.

        Args:
            run_type: The run type to filter by ('chain', 'llm', 'tool')

        Returns:
            List of matching child runs
        """
        return [c for c in self.children if c.run_type == run_type]

    @property
    def llm_children(self) -> list[LangSmithRun]:
        """Get all LLM-type child runs."""
        return self.get_children_by_type("llm")

    @property
    def chain_children(self) -> list[LangSmithRun]:
        """Get all chain-type child runs."""
        return self.get_children_by_type("chain")
