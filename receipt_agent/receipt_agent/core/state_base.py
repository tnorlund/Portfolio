"""
Base state classes and utilities for agent workflows.

Provides common state management patterns, merge functions, and type helpers.
"""

from typing import Any, Callable, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class BaseAgentState(BaseModel):
    """
    Base state class for all agent workflows.

    Provides common fields and patterns used across agents.
    Subclasses should extend this with agent-specific fields.
    """

    # Common conversation messages
    messages: Any = Field(
        default_factory=list, description="Agent conversation messages"
    )

    # Common error tracking
    errors: list[str] = Field(
        default_factory=list, description="Errors encountered during execution"
    )

    class Config:
        arbitrary_types_allowed = True


def create_state_merge_fn(
    merge_strategy: Optional[Callable[[dict, dict], dict]] = None,
) -> Callable[[dict, dict], dict]:
    """
    Create a state merge function for LangGraph.

    Args:
        merge_strategy: Optional custom merge function. If None, uses default
                       merge that combines lists and updates dicts.

    Returns:
        Merge function compatible with LangGraph state reducers
    """
    if merge_strategy:
        return merge_strategy

    def default_merge(current: dict, update: dict) -> dict:
        """Default merge: combine lists, update dicts, replace scalars."""
        result = current.copy()
        for key, value in update.items():
            if key in result:
                if isinstance(result[key], list) and isinstance(value, list):
                    result[key] = result[key] + value
                elif isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = {**result[key], **value}
                else:
                    result[key] = value
            else:
                result[key] = value
        return result

    return default_merge


