"""
LangGraph workflow for receipt place validation.

This module defines the graph structure that orchestrates the validation
process, connecting nodes with conditional edges and managing state flow.
"""

import logging
from functools import partial
from typing import Any, Callable, Optional

from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.graph.nodes import (
    check_google_places,
    load_place,
    make_decision,
    search_similar_receipts,
    verify_consistency,
)
from receipt_agent.state.models import ValidationState

logger = logging.getLogger(__name__)


def should_continue(state: ValidationState) -> str:
    """
    Determine if the workflow should continue or end.

    This is called after each main node to decide the next step.
    """
    # Check for terminal conditions
    if not state.should_continue:
        return "end"

    if state.errors and len(state.errors) > 3:
        logger.warning("Too many errors, ending workflow")
        return "end"

    if state.iteration_count > 10:
        logger.warning("Max iterations reached")
        return "end"

    # Route based on current step
    step = state.current_step

    if step == "load_place":
        return "search_similar"
    elif step == "search_similar":
        return "verify_consistency"
    elif step == "verify_consistency":
        # Skip Google Places if we have high confidence from ChromaDB
        high_confidence = any(
            s.passed is True and any(e.confidence > 0.8 for e in s.evidence)
            for s in state.verification_steps
        )
        if high_confidence:
            return "make_decision"
        return "check_places"
    elif step == "check_places":
        return "make_decision"
    elif step == "decision":
        return "end"

    return "end"


def create_validation_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
    settings: Optional[Settings] = None,
    checkpointer: Optional[Any] = None,
) -> Any:
    """
    Create the LangGraph workflow for place validation.

    Args:
        dynamo_client: DynamoDB client for receipt data
        chroma_client: ChromaDB client for similarity search
        embed_fn: Function to generate text embeddings
        places_api: Optional Google Places API client
        settings: Configuration settings
        checkpointer: Optional checkpointer for state persistence

    Returns:
        Compiled LangGraph workflow
    """
    if settings is None:
        settings = get_settings()

    # Initialize LLM for decision making
    # Get API key for authentication
    api_key = settings.ollama_api_key.get_secret_value()
    if not api_key:
        logger.warning(
            "Ollama API key not set - LLM calls will fail. "
            "Set RECEIPT_AGENT_OLLAMA_API_KEY"
        )

    llm = ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
            "timeout": 120,  # 2 minute timeout for reliability
        },
        temperature=0.1,  # Low temperature for consistent reasoning
    )

    # Create graph with state schema
    graph = StateGraph(ValidationState)

    # Bind dependencies to nodes
    bound_load_place = partial(
        load_place,
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
    )
    bound_search_similar = partial(
        search_similar_receipts,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )
    bound_verify_consistency = partial(
        verify_consistency,
        dynamo_client=dynamo_client,
    )
    bound_check_places = partial(
        check_google_places,
        places_api=places_api,
    )
    bound_make_decision = partial(make_decision, llm=llm)

    # Add nodes
    graph.add_node("load_place", bound_load_place)
    graph.add_node("search_similar", bound_search_similar)
    graph.add_node("verify_consistency", bound_verify_consistency)
    graph.add_node("check_places", bound_check_places)
    graph.add_node("make_decision", bound_make_decision)

    # Set entry point
    graph.set_entry_point("load_place")

    # Add conditional edges
    graph.add_conditional_edges(
        "load_place",
        should_continue,
        {
            "search_similar": "search_similar",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "search_similar",
        should_continue,
        {
            "verify_consistency": "verify_consistency",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "verify_consistency",
        should_continue,
        {
            "check_places": "check_places",
            "make_decision": "make_decision",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "check_places",
        should_continue,
        {
            "make_decision": "make_decision",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "make_decision",
        should_continue,
        {
            "end": END,
        },
    )

    # Compile with optional checkpointer
    if checkpointer is None:
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)


async def run_validation(
    image_id: str,
    receipt_id: int,
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
    settings: Optional[Settings] = None,
    thread_id: Optional[str] = None,
) -> ValidationState:
    """
    Run the validation workflow for a single receipt.

    Args:
        image_id: UUID of the receipt image
        receipt_id: Receipt ID within the image
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Embedding function
        places_api: Optional Google Places API
        settings: Optional settings override
        thread_id: Optional thread ID for checkpointing

    Returns:
        Final validation state with results
    """
    if settings is None:
        settings = get_settings()

    # Create the graph
    graph = create_validation_graph(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places_api,
        settings=settings,
    )

    # Create initial state
    initial_state = ValidationState(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    # Configure thread for checkpointing
    config = {"configurable": {"thread_id": thread_id or f"{image_id}#{receipt_id}"}}

    # Run the graph
    logger.info(f"Starting validation for {image_id}#{receipt_id}")

    final_state = None
    async for event in graph.astream(initial_state, config):
        # event is a dict with node name -> output
        for node_name, output in event.items():
            logger.debug(f"Node '{node_name}' completed")
            if isinstance(output, dict):
                # Merge output into state tracking
                if "result" in output:
                    final_state = output

    # Get final state from graph
    final_snapshot = graph.get_state(config)
    if final_snapshot and final_snapshot.values:
        return ValidationState(**final_snapshot.values)

    logger.warning("No final state returned from graph")
    return initial_state

