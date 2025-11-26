"""
Agentic workflow for finding Google Place IDs for receipts.

This workflow uses an LLM agent to reason about where a receipt is from by:
1. Examining receipt content (lines, words, labels)
2. Searching ChromaDB for similar receipts
3. Using Google Places API to find matching businesses
4. Reasoning about the best match

The agent has access to:
- Receipt context (lines, words, current metadata)
- ChromaDB similarity search
- Google Places API tools
- Similar receipts for comparison
"""

import logging
import os
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field
from typing import Annotated

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.tools.agentic import create_agentic_tools, ReceiptContext

logger = logging.getLogger(__name__)


# ==============================================================================
# Agent State
# ==============================================================================

class PlaceIdFinderState(BaseModel):
    """State for the place ID finder workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID to find place_id for")
    receipt_id: int = Field(description="Receipt ID to find place_id for")

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


# ==============================================================================
# System Prompt
# ==============================================================================

PLACE_ID_FINDER_PROMPT = """You are a receipt place ID finder. Your job is to find the Google Place ID for a receipt that doesn't have one yet.

## Your Task

Find the Google Place ID for this receipt by:
1. **Examining the receipt content** - Look at lines, words, and labels to understand what business this is
2. **Searching for similar receipts** - Find other receipts from the same merchant that might have place_ids
3. **Using Google Places API** - Search Google Places using merchant name, address, phone, or other clues
4. **Reasoning about the match** - Determine which place is most likely correct based on all evidence

## Available Tools

### Context Tools (understand this receipt)
- `get_my_metadata`: See the current metadata (may not have place_id)
- `get_my_lines`: See all text lines on the receipt
- `get_my_words`: See labeled words (MERCHANT_NAME, PHONE, ADDRESS, etc.)

### Similarity Search Tools (find matching receipts)
- `find_similar_to_my_line`: Use one of YOUR line embeddings to find similar lines elsewhere
- `find_similar_to_my_word`: Use one of YOUR word embeddings to find similar words elsewhere
- `search_lines`: Search by arbitrary text (address, phone, merchant name, etc.)
- `search_words`: Search for specific labeled words (MERCHANT_NAME, ADDRESS, PHONE)

### Aggregation Tools (understand consensus)
- `get_merchant_consensus`: Get canonical data for a merchant based on all receipts (if similar receipts found)

### Google Places Tools
- `verify_with_google_places`: Search Google Places API using:
  - phone_number: Most reliable if available
  - address: Good if you have a complete address
  - merchant_name: Text search as fallback
  - place_id: If you find a candidate place_id to verify
- `find_businesses_at_address`: Find businesses at a specific address
  - **CRITICAL: Use this when Google Places returns an address as the merchant name**
  - **NEVER accept an address (e.g., "10601 Magnolia Blvd") as a merchant name**
  - Returns multiple business candidates so you can choose the correct one
  - Match business names against receipt content to find the right one
  - Helps when a place_id points to a location rather than a specific business

### Decision Tool (REQUIRED at the end)
- `submit_place_id`: Submit the place_id you found (or indicate if none found)

## Strategy

**CRITICAL: You MUST use Google Places API to find place_ids. Do NOT just copy place_ids from similar receipts.**

1. **Start** by examining the receipt:
   - Get metadata to see what's already known
   - Get lines to see all text on the receipt
   - Get words to see labeled fields (MERCHANT_NAME, ADDRESS, PHONE, etc.)

2. **Extract clues** from the receipt:
   - Merchant name (from MERCHANT_NAME labels or line text)
   - Address (from ADDRESS labels or line text)
   - Phone number (from PHONE labels or line text)
   - Any other identifying information

3. **PRIMARY: Search Google Places API** (REQUIRED):
   - **ALWAYS** use `verify_with_google_places` to search Google Places API
   - Try phone number first (most reliable)
   - Then try address if available
   - Finally try merchant name text search
   - This is the PRIMARY source of truth for place_ids

4. **SECONDARY: Verify with similar receipts** (optional verification only):
   - Use similarity search to find other receipts from the same merchant
   - Use get_merchant_consensus ONLY to verify/confirm your Google Places result
   - Do NOT use similar receipts as the primary source - they may be wrong
   - If similar receipts disagree with Google Places, trust Google Places

5. **Handle address-like merchant names** (CRITICAL):
   - **NEVER accept an address as a merchant name** (e.g., "10601 Magnolia Blvd", "11000 Burbank Blvd")
   - If Google Places returns an address as the name, you MUST:
     1. Use `find_businesses_at_address` to find actual businesses at that address
     2. Compare business names with receipt content (lines, words)
     3. Select the business that matches what's on the receipt
     4. Use that business's place_id, not the address place_id
   - Merchant names should be business names (e.g., "USA Gasoline", "The HandleBar Barbershop"), not addresses

6. **Reason about the match**:
   - Does the Google Places result match the receipt content?
   - Does the name, address, and phone align?
   - **Is the merchant name a real business name, not an address?**
   - Is the confidence high enough?

7. **Submit your finding**:
   - If you found a place_id from Google Places: submit it with reasoning
   - **Ensure the merchant name is a business name, not an address**
   - If no match found in Google Places: indicate that with explanation
   - **ALWAYS call submit_place_id** - never end without calling it

## Important Rules

1. ALWAYS start by getting receipt content (get_my_metadata, get_my_lines, get_my_words)
2. Extract merchant information from the receipt itself first
3. **PRIMARY: ALWAYS use verify_with_google_places to search Google Places API** - this is required
4. **CRITICAL: NEVER accept an address as a merchant name** - if Google Places returns an address as the name:
   - You MUST use `find_businesses_at_address` to find actual businesses
   - Match business names with receipt content
   - Use the business's place_id, not the address place_id
5. **SECONDARY: Similar receipts are for verification only** - do NOT use them as primary source
6. Verify matches make sense (name, address, phone should align)
7. **Merchant names must be business names, not addresses** - reject any result where the name looks like an address
8. **ALWAYS end with submit_place_id** - never end without calling it, even if no place_id found
9. If Google Places returns a result, use that place_id (don't just copy from similar receipts)
10. Be thorough but efficient

## Good Evidence

- Google Places result matches merchant name, address, and phone from receipt
- Similar receipts from same merchant have the same place_id
- Multiple search methods (phone, address, text) all point to same place
- High confidence match with clear reasoning

## When No Match Found

- Receipt has no merchant name, address, or phone
- Google Places returns no results for all search methods
- Multiple candidates but none clearly match
- Receipt appears to be from a business not in Google Places

Begin by examining the receipt content, then systematically search for the place_id."""


# ==============================================================================
# Place ID Submission Tool
# ==============================================================================

def create_place_id_submission_tool(state_holder: dict):
    """Create a tool for submitting the found place_id."""
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field

    class SubmitPlaceIdInput(BaseModel):
        place_id: Optional[str] = Field(
            default=None,
            description="Google Place ID found (or None if not found)"
        )
        place_name: Optional[str] = Field(
            default=None,
            description="Official name from Google Places"
        )
        place_address: Optional[str] = Field(
            default=None,
            description="Formatted address from Google Places"
        )
        place_phone: Optional[str] = Field(
            default=None,
            description="Phone number from Google Places"
        )
        confidence: float = Field(
            default=0.0,
            ge=0.0,
            le=1.0,
            description="Confidence in the match (0.0 to 1.0)"
        )
        reasoning: str = Field(
            description="Explanation of how you found this place_id and why you're confident"
        )
        search_methods_used: list[str] = Field(
            default_factory=list,
            description="List of search methods used. MUST include at least one Google Places API method (phone, address, text, place_id). Verification methods like 'similar_receipts' or 'merchant_consensus' are optional."
        )

    @tool(args_schema=SubmitPlaceIdInput)
    def submit_place_id(
        place_id: Optional[str] = None,
        place_name: Optional[str] = None,
        place_address: Optional[str] = None,
        place_phone: Optional[str] = None,
        confidence: float = 0.0,
        reasoning: str = "",
        search_methods_used: list[str] = None,
    ) -> dict:
        """
        Submit the place_id you found for this receipt.

        Call this when you've found a place_id (or determined none can be found).
        This ends the workflow.

        Args:
            place_id: Google Place ID (or None if not found)
            place_name: Official name from Google Places
            place_address: Formatted address from Google Places
            place_phone: Phone number from Google Places
            confidence: Your confidence in this match (0.0 to 1.0)
            reasoning: How you found this and why you're confident
            search_methods_used: Methods you used (phone, address, text, similar_receipts, etc.)
        """
        if search_methods_used is None:
            search_methods_used = []

        result = {
            "place_id": place_id,
            "place_name": place_name,
            "place_address": place_address,
            "place_phone": place_phone,
            "confidence": confidence,
            "reasoning": reasoning,
            "search_methods_used": search_methods_used,
            "found": place_id is not None,
        }

        state_holder["place_id_result"] = result
        logger.info(
            f"Place ID submitted: {place_id} (confidence={confidence:.2%})"
        )

        return {
            "status": "submitted",
            "message": f"Place ID result submitted: {place_id or 'NOT FOUND'}",
            "result": result,
        }

    return submit_place_id


# ==============================================================================
# Workflow Builder
# ==============================================================================

def create_place_id_finder_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the agentic place ID finder workflow.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Function to generate embeddings
        places_api: Google Places API client
        settings: Optional settings

    Returns:
        (compiled_graph, state_holder) - The graph and state dict
    """
    if settings is None:
        settings = get_settings()

    # Create base agentic tools
    tools, state_holder = create_agentic_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places_api,
    )

    # Add place_id submission tool
    submit_tool = create_place_id_submission_tool(state_holder)
    tools.append(submit_tool)

    # Create LLM with tools bound
    api_key = settings.ollama_api_key.get_secret_value()
    llm = ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
            "timeout": 120,
        },
        temperature=0.0,
    ).bind_tools(tools)

    # Define the agent node
    def agent_node(state: PlaceIdFinderState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages
        response = llm.invoke(messages)

        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.debug(f"Agent tool calls: {[tc['name'] for tc in response.tool_calls]}")

        return {"messages": [response]}

    # Define tool node
    tool_node = ToolNode(tools)

    # Define routing function
    def should_continue(state: PlaceIdFinderState) -> str:
        """Check if we should continue or end."""
        # Check if place_id was submitted
        if state_holder.get("place_id_result") is not None:
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    return "tools"
                # If agent responded without tool calls and no result, check if it's trying to end
                # Give it one more chance to call submit_place_id
                if len(state.messages) > 10:  # After several rounds, remind to submit
                    logger.warning("Agent has made many steps without submitting - may need reminder")
                return "agent"  # Go back to agent to give it another chance

        # If no messages or no tool calls, go back to agent
        return "agent"

    # Build the graph
    workflow = StateGraph(PlaceIdFinderState)

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
            "agent": "agent",  # Loop back to agent if no tool calls
            "end": END,
        }
    )

    # After tools, go back to agent
    workflow.add_edge("tools", "agent")

    # Compile
    compiled = workflow.compile()

    return compiled, state_holder


# ==============================================================================
# Place ID Finder Runner
# ==============================================================================

async def run_place_id_finder(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    line_embeddings: Optional[dict[str, list[float]]] = None,
    word_embeddings: Optional[dict[str, list[float]]] = None,
) -> dict:
    """
    Run the place ID finder workflow for a receipt.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        image_id: Receipt image ID
        receipt_id: Receipt ID
        line_embeddings: Pre-loaded line embeddings (optional)
        word_embeddings: Pre-loaded word embeddings (optional)

    Returns:
        Place ID result dict
    """
    # Set up context
    state_holder["context"] = ReceiptContext(
        image_id=image_id,
        receipt_id=receipt_id,
        lines=None,
        words=None,
        metadata=None,
        line_embeddings=line_embeddings,
        word_embeddings=word_embeddings,
    )
    state_holder["place_id_result"] = None

    # Create initial state
    initial_state = PlaceIdFinderState(
        image_id=image_id,
        receipt_id=receipt_id,
        messages=[
            SystemMessage(content=PLACE_ID_FINDER_PROMPT),
            HumanMessage(
                content=f"Please find the Google Place ID for receipt {image_id}#{receipt_id}. "
                f"Start by examining the receipt content, then search for the place_id."
            ),
        ],
    )

    logger.info(f"Starting place ID finder for {image_id}#{receipt_id}")

    # Run the workflow
    try:
        # Configure LangSmith tracing if available
        config = {
            "recursion_limit": 100,  # Increased to prevent early termination
            "configurable": {
                "thread_id": f"{image_id}#{receipt_id}",  # Unique thread ID for this receipt
            },
        }

        # Add LangSmith metadata if tracing is enabled
        if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
            config["metadata"] = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "workflow": "place_id_finder",
            }

        final_state = await graph.ainvoke(initial_state, config=config)

        # Get result from state holder
        result = state_holder.get("place_id_result")

        if result:
            logger.info(
                f"Place ID finder complete: {'FOUND' if result['found'] else 'NOT FOUND'} "
                f"(confidence={result['confidence']:.2%})"
            )
            return result
        else:
            # Agent ended without submitting result
            logger.warning(f"Agent ended without submitting place_id for {image_id}#{receipt_id}")
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "place_id": None,
                "found": False,
                "confidence": 0.0,
                "reasoning": "Agent did not submit a place_id result",
                "search_methods_used": [],
            }

    except Exception as e:
        logger.error(f"Error in place ID finder: {e}")
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "place_id": None,
            "found": False,
            "confidence": 0.0,
            "reasoning": f"Error during place ID finding: {str(e)}",
            "search_methods_used": [],
        }

