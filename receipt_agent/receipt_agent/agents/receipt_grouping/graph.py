"""
Receipt grouping workflow for determining correct receipt groupings in images.

This workflow helps identify if receipts are incorrectly split by trying
different combinations and evaluating which makes the most sense.
"""

import logging
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.agents.receipt_grouping.state import GroupingState
from receipt_agent.agents.receipt_grouping.tools import (
    ImageContext,
    create_receipt_grouper_tools,
)
from receipt_agent.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


# ==============================================================================
# System Prompt
# ==============================================================================

SYSTEM_PROMPT = """You are a receipt grouping analyzer. Your job is to determine if receipts in an image are correctly grouped, or if some should be merged together.

## Your Task

You are analyzing an image that currently has 3 receipts, but there should only be 2. You need to:
1. Understand the current grouping (get_current_receipts)
2. Compare coordinates between receipt lines and image lines (compare_coordinates for each receipt)
3. Try merging receipt 1 + receipt 2 (try_merge)
4. Try merging receipt 2 + receipt 3 (try_merge)
5. Determine which merge makes sense, or if no merge is needed
6. Submit your final recommendation

## Available Tools

### Context Tools (understand the image)
- `get_image_lines`: See all OCR lines from the image with coordinates
- `get_image_words`: See all OCR words from the image
- `get_current_receipts`: See how receipts are currently grouped

### Analysis Tools
- `compare_coordinates`: Compare receipt line coordinates with image line coordinates for a specific receipt
- `try_merge`: Try merging two receipts and evaluate if it makes sense
- `try_grouping`: Try a specific grouping of lines into receipts
- `evaluate_grouping`: Evaluate a grouping more thoroughly

### Decision Tool (REQUIRED at the end)
- `submit_grouping`: Submit your final recommended grouping

## Strategy

1. **Start** by getting current receipts and image lines
2. **Compare coordinates** for each receipt to verify line assignments
3. **Try merging 1+2** and evaluate the result
4. **Try merging 2+3** and evaluate the result
5. **Compare** the two merge options:
   - Which has better coherence?
   - Which has fewer issues?
   - Which makes more spatial sense?
   - Which has matching merchant/address/phone?
6. **Decide** which merge is correct (or if no merge is needed)
7. **Submit** your final grouping recommendation

## Important Rules

1. ALWAYS start by getting current receipts and image lines
2. Use compare_coordinates to verify line assignments match
3. Try BOTH merge combinations (1+2 and 2+3)
4. Compare the results carefully
5. Consider:
   - Spatial layout (are lines close together?)
   - Merchant name matches
   - Address matches
   - Phone number matches
   - Text coherence
   - Presence of receipt elements (total, etc.)
6. ALWAYS end with submit_grouping
7. If neither merge makes sense, submit the original grouping

## Good Signs for a Merge

- Same merchant name
- Same address
- Same phone number
- Lines are spatially close together
- No large gaps in Y coordinates
- Combined text forms a coherent receipt
- Has all expected receipt elements (merchant, address, phone, total)

## Bad Signs for a Merge

- Different merchant names
- Different addresses
- Different phone numbers
- Large spatial gaps between lines
- Very large vertical spread
- Combined text doesn't make sense as one receipt

Begin by getting the current receipts and image lines, then systematically try both merge combinations."""


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_receipt_grouping_graph(
    dynamo_client: Any,
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the receipt grouping workflow.

    Args:
        dynamo_client: DynamoDB client
        settings: Optional settings

    Returns:
        (compiled_graph, state_holder) - The graph and the state dict to inject context
    """
    if settings is None:
        settings = get_settings()

    # Create tools with injected dependencies
    tools, state_holder = create_receipt_grouper_tools(
        dynamo_client=dynamo_client,
    )

    # Create LLM with tools bound
    api_key = settings.ollama_api_key.get_secret_value()
    llm = ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": ({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            "timeout": 120,
        },
        temperature=0.0,
    ).bind_tools(tools)

    # Define the agent node (calls LLM)
    def agent_node(state: GroupingState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages
        response = llm.invoke(messages)

        # Log tool calls for debugging
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_names = [tc.get("name", "unknown") for tc in response.tool_calls]
            logger.info(f"Agent tool calls: {tool_names}")
            # Check if submit_grouping was called
            if "submit_grouping" in tool_names:
                logger.info(
                    "submit_grouping called - workflow should end after tool execution"
                )
        else:
            logger.info("Agent response with no tool calls")

        # Only return new message - add_messages reducer handles accumulation
        return {"messages": [response]}

    # Define tool node
    tool_node = ToolNode(tools)

    # Define routing function
    def should_continue(state: GroupingState) -> str:
        """Check if we should continue or end."""
        # Check if grouping was submitted (check state_holder first)
        if state_holder.get("grouping") is not None:
            logger.info("Grouping submitted, ending workflow")
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                # If no tool calls and no grouping, we're done
                if not last_message.tool_calls:
                    logger.info("No tool calls, ending workflow")
                    return "end"
                # If there are tool calls, execute them
                logger.debug(
                    f"Tool calls detected: {[tc.get('name') for tc in last_message.tool_calls]}"
                )
                return "tools"

        # Default to end if no messages
        logger.info("No messages, ending workflow")
        return "end"

    # Build the graph
    workflow = StateGraph(GroupingState)

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
# Grouping Runner
# ==============================================================================


async def run_receipt_grouping(
    graph: Any,
    state_holder: dict,
    image_id: str,
) -> dict:
    """
    Run the receipt grouping workflow for an image.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict (to inject context)
        image_id: Image ID to analyze

    Returns:
        Grouping recommendation dict
    """
    # Set up context for this analysis
    state_holder["context"] = ImageContext(
        image_id=image_id,
        lines=None,
        words=None,
        receipts=None,
    )
    state_holder["grouping"] = None

    # Create initial state
    initial_state = GroupingState(
        image_id=image_id,
        messages=[
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=f"Please analyze the receipt groupings for image {image_id}. "
                f"The image currently has 3 receipts but should have 2. "
                f"Try merging receipts 1+2 and 2+3, then determine which combination is correct."
            ),
        ],
    )

    logger.info(f"Starting receipt grouping analysis for {image_id}")

    # Run the workflow with increased recursion limit
    try:
        config = {"recursion_limit": 100}
        _final_state = await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        logger.error(f"Error in receipt grouping: {e}")
        import traceback

        traceback.print_exc()
        return {
            "image_id": image_id,
            "status": "ERROR",
            "confidence": 0.0,
            "reasoning": f"Error during analysis: {str(e)}",
            "grouping": None,
        }

    # Get grouping from state holder
    grouping = state_holder.get("grouping")

    if grouping:
        logger.info(f"Grouping complete: confidence={grouping['confidence']:.2%}")
        return grouping

    # Agent ended without submitting grouping
    logger.warning(f"Agent ended without submitting grouping for {image_id}")
    return {
        "image_id": image_id,
        "status": "INCOMPLETE",
        "confidence": 0.0,
        "reasoning": "Agent did not submit a grouping",
        "grouping": None,
    }


# ==============================================================================
# Synchronous Wrapper
# ==============================================================================


def run_receipt_grouping_sync(
    graph: Any,
    state_holder: dict,
    image_id: str,
) -> dict:
    """Synchronous wrapper for run_receipt_grouping."""
    import asyncio

    return asyncio.run(
        run_receipt_grouping(
            graph=graph,
            state_holder=state_holder,
            image_id=image_id,
        )
    )
