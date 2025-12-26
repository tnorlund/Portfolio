"""
Agentic workflow for receipt metadata validation using ReAct pattern.

This workflow gives the LLM full autonomy to decide which tools to call,
while the tools themselves enforce the guard rails.

Guard Rails:
- Tools construct ChromaDB record IDs internally
- Collections are hardcoded
- Current receipt excluded from searches
- submit_decision enforces valid status values

The agent is guided by a system prompt that explains:
- What it's validating
- What tools are available
- What constitutes good evidence
- When to submit a decision
"""

import asyncio
import logging
from typing import Annotated, Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.agents.agentic.state import AgentState
from receipt_agent.agents.agentic.tools import (
    ReceiptContext,
    create_agentic_tools,
)
from receipt_agent.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


# ==============================================================================
# System Prompt
# ==============================================================================

SYSTEM_PROMPT = """You are a receipt metadata validator. Your job is to verify that the stored metadata for a receipt is accurate by comparing it with other receipts in the database.

## Your Task

You are validating a specific receipt. You need to verify:
1. **Merchant Name** - Is this the correct business name?
2. **Place ID** - Is the Google Place ID correct?
3. **Address** - Is the address accurate?
4. **Phone Number** - Is the phone number correct?

## Available Tools

### Context Tools (understand this receipt)
- `get_my_metadata`: See the current metadata stored for this receipt
- `get_my_lines`: See all text lines on the receipt
- `get_my_words`: See labeled words (MERCHANT_NAME, PHONE, ADDRESS, etc.)

### Similarity Search Tools (find matching receipts)
- `find_similar_to_my_line`: Use one of YOUR line embeddings to find similar lines elsewhere
- `find_similar_to_my_word`: Use one of YOUR word embeddings to find similar words elsewhere
- `search_lines`: Search by arbitrary text (address, phone, etc.)
- `search_words`: Search for specific labeled words

### Aggregation Tools (understand consensus)
- `get_merchant_consensus`: Get canonical data for a merchant based on all receipts
- `get_place_id_info`: Get all receipts using a specific Place ID

### Comparison Tools
- `compare_with_receipt`: Detailed comparison with another specific receipt

### Decision Tool (REQUIRED at the end)
- `submit_decision`: Submit your final validation decision

## Strategy

1. **Start** by getting your receipt's metadata and content (get_my_metadata, get_my_lines, get_my_words)

2. **Search** for similar receipts using:
   - Lines containing the address, phone, or merchant name
   - Words labeled as MERCHANT_NAME, ADDRESS, or PHONE

3. **Verify** using:
   - get_merchant_consensus to see what other receipts say about this merchant
   - get_place_id_info to verify the Place ID is used consistently
   - compare_with_receipt for detailed comparison with promising matches

4. **Decide** based on evidence:
   - VALIDATED: Multiple receipts agree, metadata is consistent
   - INVALID: Clear evidence of incorrect metadata
   - NEEDS_REVIEW: Conflicting evidence or insufficient data

## Important Rules

1. ALWAYS start by getting your metadata to understand what you're validating
2. Use multiple evidence sources before deciding
3. The current receipt is automatically excluded from search results
4. ALWAYS end with submit_decision
5. Be efficient - don't make redundant searches

## Good Evidence

- Multiple receipts from the same merchant have matching Place IDs
- Address and phone are consistent across receipts
- Similarity searches return relevant matches
- get_merchant_consensus shows high agreement

## Bad Signs

- Place ID appears on receipts with different merchant names
- Address/phone don't match across similar receipts
- No similar receipts found (might be first receipt for this merchant)
- Low similarity scores on searches

Begin by getting your metadata, then gather evidence systematically."""


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_agentic_validation_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the agentic validation workflow.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client (or DualChromaClient)
        embed_fn: Function to generate embeddings
        places_api: Optional Places API client
        settings: Optional settings

    Returns:
        (compiled_graph, state_holder) - The graph and the state dict to inject context
    """
    if settings is None:
        settings = get_settings()

    # Create tools with injected dependencies
    tools, state_holder = create_agentic_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places_api,
    )

    # Create LLM with tools bound
    api_key = settings.ollama_api_key.get_secret_value()
    llm = ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": (
                {"Authorization": f"Bearer {api_key}"} if api_key else {}
            ),
            "timeout": 120,
        },
        temperature=0.0,
    ).bind_tools(tools)

    # Define the agent node (calls LLM)
    def agent_node(state: AgentState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages
        response = llm.invoke(messages)

        # Log tool calls for debugging
        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.debug(
                "Agent tool calls: %s",
                [tc["name"] for tc in response.tool_calls],
            )

        # Only return new message - add_messages reducer handles accumulation
        return {"messages": [response]}

    # Define tool node
    tool_node = ToolNode(tools)

    # Define routing function
    def should_continue(state: AgentState) -> str:
        """Check if we should continue or end."""
        # Check if decision was submitted
        if state_holder.get("decision") is not None:
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    return "tools"

        return "end"

    # Build the graph
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add conditional edges
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )

    # After tools, go back to agent
    workflow.add_edge("tools", "agent")

    # Compile
    compiled = workflow.compile()

    return compiled, state_holder


# ==============================================================================
# Validation Runner
# ==============================================================================


async def run_agentic_validation(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    line_embeddings: Optional[dict[str, list[float]]] = None,
    word_embeddings: Optional[dict[str, list[float]]] = None,
) -> dict:
    """
    Run the agentic validation workflow for a receipt.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict (to inject context)
        image_id: Receipt image ID
        receipt_id: Receipt ID
        line_embeddings: Pre-loaded line embeddings (optional)
        word_embeddings: Pre-loaded word embeddings (optional)

    Returns:
        Validation decision dict
    """
    # Set up context for this validation
    state_holder["context"] = ReceiptContext(
        image_id=image_id,
        receipt_id=receipt_id,
        lines=None,  # Will be loaded on demand
        words=None,
        metadata=None,
        line_embeddings=line_embeddings,
        word_embeddings=word_embeddings,
    )
    state_holder["decision"] = None

    # Create initial state
    initial_state = AgentState(
        image_id=image_id,
        receipt_id=receipt_id,
        messages=[
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=f"Please validate the metadata for receipt {image_id}#{receipt_id}. "
                f"Start by getting the metadata, then gather evidence."
            ),
        ],
    )

    logger.info(
        "Starting agentic validation for %s#%s",
        image_id,
        receipt_id,
    )

    # Run the workflow with increased recursion limit
    try:
        config = {"recursion_limit": 50}
        final_state = await graph.ainvoke(initial_state, config=config)

        # Get decision from state holder
        decision = state_holder.get("decision")

        if decision:
            logger.info(
                "Validation complete: %s (%.2f%%)",
                decision["status"],
                decision["confidence"] * 100.0,
            )
            return decision
        else:
            # Agent ended without submitting decision
            logger.warning(
                "Agent ended without submitting decision for %s#%s",
                image_id,
                receipt_id,
            )
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "status": "NEEDS_REVIEW",
                "confidence": 0.0,
                "reasoning": "Agent did not submit a decision",
                "evidence": [],
            }

    except Exception as e:
        logger.error("Error in agentic validation: %s", e)
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "status": "NEEDS_REVIEW",
            "confidence": 0.0,
            "reasoning": f"Error during validation: {str(e)}",
            "evidence": [],
        }


# ==============================================================================
# Synchronous Wrapper
# ==============================================================================


def run_agentic_validation_sync(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    line_embeddings: Optional[dict[str, list[float]]] = None,
    word_embeddings: Optional[dict[str, list[float]]] = None,
) -> dict:
    """Synchronous wrapper for run_agentic_validation."""
    return asyncio.run(
        run_agentic_validation(
            graph=graph,
            state_holder=state_holder,
            image_id=image_id,
            receipt_id=receipt_id,
            line_embeddings=line_embeddings,
            word_embeddings=word_embeddings,
        )
    )
