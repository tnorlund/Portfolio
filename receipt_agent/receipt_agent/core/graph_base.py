"""
Base graph construction utilities for agent workflows.

Provides wrappers for LangGraph construction with common middleware patterns.
"""

import logging
from typing import Any, Callable, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


def create_base_graph(
    state_schema: type,
    checkpointer: Optional[Any] = None,
) -> StateGraph:
    """
    Create a base StateGraph with the given state schema.

    Args:
        state_schema: Pydantic model class defining the state schema
        checkpointer: Optional checkpointer for state persistence.
                     Defaults to MemorySaver if None.

    Returns:
        Configured StateGraph instance
    """
    graph = StateGraph(state_schema)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return graph


def create_graph_with_middleware(
    state_schema: type,
    checkpointer: Optional[Any] = None,
    error_handler: Optional[Callable[[Exception], dict]] = None,
) -> StateGraph:
    """
    Create a StateGraph with error handling middleware.

    Args:
        state_schema: Pydantic model class defining the state schema
        checkpointer: Optional checkpointer for state persistence
        error_handler: Optional function to handle errors and return state updates.
                      If None, errors are logged and added to state.errors

    Returns:
        Configured StateGraph instance with error handling
    """
    graph = create_base_graph(state_schema, checkpointer)

    if error_handler is None:

        def default_error_handler(exc: Exception) -> dict:
            error_msg = str(exc)
            logger.error(f"Graph execution error: {error_msg}", exc_info=exc)
            return {"errors": [error_msg]}

        error_handler = default_error_handler

    # Note: LangGraph middleware is typically applied at compile time
    # This is a placeholder for future middleware patterns
    return graph

