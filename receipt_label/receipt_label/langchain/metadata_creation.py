"""LangGraph workflow for creating ReceiptMetadata using Google Places API."""

import os
import time
from typing import Any, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langchain_core.tracers.langchain import LangChainTracer

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.langchain.state.metadata_creation import MetadataCreationState
from receipt_label.langchain.nodes.metadata_creation import (
    load_receipt_data_for_metadata,
    extract_merchant_info,
    create_receipt_metadata,
)
from receipt_label.langchain.nodes.metadata_creation.search_places_agent import (
    search_places_for_merchant_with_agent,
)


def create_metadata_creation_graph(
    google_places_api_key: str,
    ollama_api_key: str,
    thinking_strength: str = "medium",
) -> CompiledStateGraph:
    """Create LangGraph workflow for metadata creation with secure API key injection.

    Uses Ollama Cloud exclusively with the latest cloud models and thinking strength features.

    Args:
        google_places_api_key: Google Places API key (injected via closure)
        ollama_api_key: Ollama Cloud API key (injected via closure, required)
        thinking_strength: Ollama thinking strength - "low", "medium", or "high" (default: "medium")

    Returns:
        Compiled LangGraph state graph
    """
    workflow = StateGraph(MetadataCreationState)

    # Create nodes with API keys injected via closures (secure)
    async def extract_with_key(state):
        """Extract merchant info node with Ollama Cloud API key from closure scope"""
        try:
            return await extract_merchant_info(
                state,
                ollama_api_key=ollama_api_key,
                thinking_strength=thinking_strength,
            )
        except Exception as e:
            return {
                "extracted_merchant_name": "",
                "extracted_address": "",
                "extracted_phone": "",
                "extracted_merchant_words": [],
                "error_count": state.error_count + 1,
                "last_error": str(e),
            }

    async def search_with_key(state):
        """Search Places API node with Ollama Cloud API key using intelligent agent"""
        try:
            return await search_places_for_merchant_with_agent(
                state,
                google_places_api_key,
                ollama_api_key=ollama_api_key,
                thinking_strength=thinking_strength,
            )
        except Exception as e:
            return {
                "places_search_results": [],
                "selected_place": None,
                "error_count": state.error_count + 1,
                "last_error": str(e),
            }

    # Add nodes
    workflow.add_node("load_data", load_receipt_data_for_metadata)
    workflow.add_node("extract_merchant_info", extract_with_key)
    workflow.add_node("search_places", search_with_key)
    workflow.add_node("create_metadata", create_receipt_metadata)

    # Define the flow
    workflow.add_edge(START, "load_data")
    workflow.add_edge("load_data", "extract_merchant_info")
    workflow.add_edge("extract_merchant_info", "search_places")
    workflow.add_edge("search_places", "create_metadata")
    workflow.add_edge("create_metadata", END)

    return workflow.compile()


async def create_receipt_metadata_simple(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    google_places_api_key: str,
    ollama_api_key: str,
    langsmith_api_key: str,  # Required for tracing
    thinking_strength: str = "medium",  # low, medium, high
    receipt_lines: Optional[list] = None,
    receipt_words: Optional[list] = None,
) -> Any:  # Returns ReceiptMetadata
    """Create ReceiptMetadata for a receipt using LangGraph workflow with Ollama Cloud.

    This is the main entry point for metadata creation, similar to analyze_receipt_simple.
    Uses Ollama Cloud exclusively with the latest cloud models (gpt-oss:120b-cloud).

    Args:
        client: DynamoDB client
        image_id: Image UUID
        receipt_id: Receipt ID
        google_places_api_key: Google Places API key
        ollama_api_key: Ollama Cloud API key (required)
        langsmith_api_key: LangSmith API key for tracing (required)
        thinking_strength: Ollama thinking strength - "low", "medium", or "high" (default: "medium")
        receipt_lines: Optional pre-fetched receipt lines
        receipt_words: Optional pre-fetched receipt words

    Returns:
        ReceiptMetadata object (or None if creation failed)
    """
    start_time = time.time()

    print(f"\n🚀 Starting metadata creation workflow for {image_id}/{receipt_id}")
    print(f"   Using Ollama Cloud (gpt-oss:120b-cloud)")
    print(f"   Thinking strength: {thinking_strength}")

    # Validate API key
    if not ollama_api_key:
        raise ValueError("ollama_api_key is required")

    # Initial state
    initial_state: MetadataCreationState = MetadataCreationState(
        receipt_id=f"{image_id}/{receipt_id}",
        image_id=image_id,
        lines=receipt_lines or [],
        words=receipt_words or [],
        formatted_text="",
        dynamo_client=client,
    )

    # Create and run the graph with secure API key injection
    graph = create_metadata_creation_graph(
        google_places_api_key=google_places_api_key,
        ollama_api_key=ollama_api_key,
        thinking_strength=thinking_strength,
    )

    # Setup LangSmith tracing with secure API key handling (required)
    # LangChain API key is required for proper tracing and observability
    if not langsmith_api_key or not langsmith_api_key.strip():
        raise ValueError("langsmith_api_key is required for metadata creation workflow")

    # Set environment variables for LangSmith (required before creating tracer)
    os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = f"metadata-creation-{image_id[:8]}"
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"

    try:
        tracer = LangChainTracer()
        print(f"✅ LangSmith tracing enabled")
        print(f"   Project: metadata-creation-{image_id[:8]}")
        print(f"   View at: https://smith.langchain.com/")
    except Exception as e:
        print(f"⚠️  Failed to initialize LangSmith tracer: {e}")
        tracer = None
        # Clean up env vars if tracer creation failed
        os.environ.pop("LANGCHAIN_API_KEY", None)
        os.environ.pop("LANGCHAIN_TRACING_V2", None)

    config = {
        "metadata": {
            "receipt_id": f"{image_id}/{receipt_id}",
            "workflow": "metadata_creation",
        },
        "tags": ["metadata_creation", "places_api"],
    }
    if tracer:
        config["callbacks"] = [tracer]

    # Run the workflow with proper cleanup
    try:
        result = await graph.ainvoke(initial_state.to_graph(), config=config)
    finally:
        # Always clean up LangSmith env vars after execution
        if langsmith_api_key:
            os.environ.pop("LANGCHAIN_API_KEY", None)
            os.environ.pop("LANGCHAIN_TRACING_V2", None)

    processing_time = time.time() - start_time
    print(f"\n⚡ METADATA CREATION TIME: {processing_time:.2f}s")

    # Extract result
    final_state = MetadataCreationState.from_graph(result)

    if final_state.metadata_created and final_state.receipt_metadata:
        print(f"✅ Successfully created ReceiptMetadata")
        return final_state.receipt_metadata
    else:
        error_msg = final_state.error_message or "Unknown error"
        print(f"❌ Failed to create ReceiptMetadata: {error_msg}")
        return None

