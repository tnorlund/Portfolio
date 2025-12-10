"""
Agentic workflow for finding complete receipt metadata.

This workflow uses an LLM agent to find ALL missing metadata for a receipt:
- place_id (Google Place ID)
- merchant_name (business name)
- address (formatted address)
- phone_number (phone number)

The agent intelligently:
1. Examines receipt content (lines, words, labels)
2. Extracts metadata from receipt itself
3. Searches Google Places API for missing fields
4. Uses similar receipts for verification
5. Reasons about the best values for each field

Key Improvements Over Place ID Finder:
- Finds ALL missing metadata, not just place_id
- Can extract metadata from receipt content even if Google Places fails
- Handles partial metadata intelligently
- Better reasoning about what's missing and how to find it
"""

import logging
import os
from typing import Annotated, Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.tools.agentic import ReceiptContext, create_agentic_tools
from receipt_agent.utils.agent_common import (
    create_agent_node_with_retry,
    create_ollama_llm,
)


# Helper function for building line IDs (matches agentic.py)
def _build_line_id(image_id: str, receipt_id: int, line_id: int) -> str:
    """Build ChromaDB document ID for a line."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


logger = logging.getLogger(__name__)


# ==============================================================================
# Agent State
# ==============================================================================


class ReceiptMetadataFinderState(BaseModel):
    """State for the receipt metadata finder workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID to find metadata for")
    receipt_id: int = Field(description="Receipt ID to find metadata for")

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


# ==============================================================================
# System Prompt
# ==============================================================================

RECEIPT_METADATA_FINDER_PROMPT = """You are a receipt metadata finder. Your job is to find ALL missing metadata for a receipt, not just the place_id.

## Your Task

Find complete metadata for this receipt:
1. **place_id** - Google Place ID (if missing)
2. **merchant_name** - Business name (if missing or incorrect)
3. **address** - Formatted address (if missing or incomplete)
4. **phone_number** - Phone number (if missing or incorrect)

## Available Tools

### Context Tools (understand this receipt)
- `get_my_metadata`: See what metadata already exists (may be incomplete)
- `get_my_lines`: See all text lines on the receipt
- `get_my_words`: See labeled words (MERCHANT_NAME, PHONE, ADDRESS, etc.)
- `get_receipt_text`: View formatted receipt text (receipt order, grouped rows)

### Similarity Search Tools (find matching receipts)
- `find_similar_to_my_line`: Use one of YOUR line embeddings to find similar lines elsewhere
- `find_similar_to_my_word`: Use one of YOUR word embeddings to find similar words elsewhere
- `search_lines`: Search by arbitrary text (address, phone, merchant name, etc.)
- `search_words`: Search for specific labeled words (MERCHANT_NAME, ADDRESS, PHONE)

### Aggregation Tools (understand consensus)
- `get_merchant_consensus`: Get canonical data for a merchant based on all receipts
- `get_place_id_info`: Get all receipts using a specific Place ID

### Google Places Tools (Source of Truth)
- `verify_with_google_places`: Search Google Places API using:
  - phone_number: Most reliable if available
  - address: Good if you have a complete address
  - merchant_name: Text search as fallback
  - place_id: If you find a candidate place_id to verify
- `find_businesses_at_address`: Find businesses at a specific address
  - **CRITICAL: Use this when Google Places returns an address as the merchant name**
  - **NEVER accept an address (e.g., "10601 Magnolia Blvd") as a merchant name**

### Decision Tool (REQUIRED at the end)
- `submit_metadata`: Submit ALL metadata you found (place_id, merchant_name, address, phone)

## Strategy

1. **Start** by examining the receipt:
   - Get metadata to see what's already known
   - Get lines to see all text on the receipt
   - Get words to see labeled fields (MERCHANT_NAME, ADDRESS, PHONE, etc.)
   - Use `get_receipt_text` for a human-readable view of the receipt text

2. **Extract metadata from receipt content**:
   - Look for MERCHANT_NAME labels in words
   - Look for ADDRESS labels in words
   - Look for PHONE labels in words
   - Extract from lines if labels aren't available
   - This is your PRIMARY source - the receipt itself

3. **Fill in missing fields**:
   - If place_id is missing: Search Google Places API
   - If merchant_name is missing: Extract from receipt or get from Google Places
   - If address is missing: Extract from receipt or get from Google Places
   - If phone is missing: Extract from receipt or get from Google Places

4. **Search Google Places** (for place_id and validation):
   - **ALWAYS** use `verify_with_google_places` to search Google Places API
   - Try phone number first (most reliable)
   - Then try address if available
   - Finally try merchant name text search
   - Use Google Places data to fill in missing fields

5. **Verify with similar receipts** (optional):
   - Use similarity search to find other receipts from the same merchant
   - Use get_merchant_consensus to verify your findings
   - Similar receipts can help fill in missing fields

6. **Handle address-like merchant names** (CRITICAL):
   - **NEVER accept an address as a merchant name**
   - If Google Places returns an address as the name, use `find_businesses_at_address`
   - Match business names with receipt content

7. **Submit your findings**:
   - Submit ALL metadata you found (even if some fields are still missing)
   - Include confidence scores for each field
   - Explain how you found each field
   - **ALWAYS call submit_metadata** - never end without calling it

## Important Rules

1. ALWAYS start by getting receipt content (get_my_metadata, get_my_lines, get_my_words)
2. Extract metadata from receipt FIRST (it's the most reliable source)
3. Use Google Places to find place_id and validate/fill in other fields
4. **NEVER accept an address as a merchant name**
5. Fill in as many fields as possible, even if place_id can't be found
6. **ALWAYS end with submit_metadata** - submit all fields you found

## Field Priority

For each field, use this priority:
1. **Receipt content** (labels, lines) - Most reliable, comes from the receipt itself
2. **Google Places** - Official source, use for validation and missing fields
3. **Similar receipts** - Verification only, may be wrong

## What Gets Updated

When you submit metadata, ALL fields you provide will be used:
- `place_id` ← Google Place ID (if found)
- `merchant_name` ← From receipt or Google Places
- `address` ← From receipt or Google Places
- `phone_number` ← From receipt or Google Places

Begin by examining the receipt content, then systematically find all missing metadata."""


# ==============================================================================
# Metadata Submission Tool
# ==============================================================================


def create_metadata_submission_tool(state_holder: dict):
    """Create a tool for submitting found metadata."""
    from pydantic import BaseModel, Field

    class SubmitMetadataInput(BaseModel):
        """Input for submit_metadata tool."""

        place_id: Optional[str] = Field(
            default=None,
            description="Google Place ID found (or None if not found)",
        )
        merchant_name: Optional[str] = Field(
            default=None,
            description="Merchant name (from receipt or Google Places)",
        )
        address: Optional[str] = Field(
            default=None, description="Address (from receipt or Google Places)"
        )
        phone_number: Optional[str] = Field(
            default=None,
            description="Phone number (from receipt or Google Places)",
        )
        confidence: float = Field(
            default=0.0,
            ge=0.0,
            le=1.0,
            description="Overall confidence in the metadata (0.0 to 1.0)",
        )
        field_confidence: dict[str, float] = Field(
            default_factory=dict,
            description="Confidence for each field: {'place_id': 0.9, 'merchant_name': 0.95, ...}",
        )
        reasoning: str = Field(
            description="Explanation of how you found the metadata and why you're confident"
        )
        sources: dict[str, str] = Field(
            default_factory=dict,
            description="Source for each field: {'place_id': 'google_places', 'merchant_name': 'receipt_content', ...}",
        )
        search_methods_used: list[str] = Field(
            default_factory=list,
            description="List of search methods used (phone, address, text, similar_receipts, etc.)",
        )

    @tool(args_schema=SubmitMetadataInput)
    def submit_metadata(
        place_id: Optional[str] = None,
        merchant_name: Optional[str] = None,
        address: Optional[str] = None,
        phone_number: Optional[str] = None,
        confidence: float = 0.0,
        field_confidence: dict[str, float] = None,
        reasoning: str = "",
        sources: dict[str, str] = None,
        search_methods_used: list[str] = None,
    ) -> dict:
        """
        Submit ALL metadata you found for this receipt.

        Call this when you've found metadata (or determined what can't be found).
        This ends the workflow.

        Args:
            place_id: Google Place ID (or None if not found)
            merchant_name: Merchant name (or None if not found)
            address: Address (or None if not found)
            phone_number: Phone number (or None if not found)
            confidence: Overall confidence (0.0 to 1.0)
            field_confidence: Confidence for each field
            reasoning: How you found the metadata
            sources: Source for each field (receipt_content, google_places, similar_receipts)
            search_methods_used: Methods you used (phone, address, text, etc.)
        """
        if field_confidence is None:
            field_confidence = {}
        if sources is None:
            sources = {}
        if search_methods_used is None:
            search_methods_used = []

        # Calculate which fields were found
        fields_found = []
        if place_id:
            fields_found.append("place_id")
        if merchant_name:
            fields_found.append("merchant_name")
        if address:
            fields_found.append("address")
        if phone_number:
            fields_found.append("phone_number")

        result = {
            "place_id": place_id,
            "merchant_name": merchant_name,
            "address": address,
            "phone_number": phone_number,
            "confidence": confidence,
            "field_confidence": field_confidence,
            "reasoning": reasoning,
            "sources": sources,
            "search_methods_used": search_methods_used,
            "fields_found": fields_found,
            "found": len(fields_found) > 0,
        }

        state_holder["metadata_result"] = result
        logger.info(
            f"Metadata submitted: {len(fields_found)} fields found "
            f"(place_id={'✓' if place_id else '✗'}, "
            f"merchant_name={'✓' if merchant_name else '✗'}, "
            f"address={'✓' if address else '✗'}, "
            f"phone={'✓' if phone_number else '✗'})"
        )

        return {
            "status": "submitted",
            "message": f"Metadata submitted: {len(fields_found)} fields found",
            "result": result,
        }

    return submit_metadata


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_receipt_metadata_finder_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
    settings: Optional[Settings] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[Any, dict]:
    """
    Create the receipt metadata finder workflow.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client (may be None, will be lazy-loaded if bucket provided)
        embed_fn: Function to generate embeddings (may be None, will be lazy-loaded if bucket provided)
        places_api: Google Places API client
        settings: Optional settings
        chromadb_bucket: Optional S3 bucket name for lazy loading ChromaDB collections

    Returns:
        (compiled_graph, state_holder) - The graph and state dict
    """
    if settings is None:
        settings = get_settings()

    # Create base agentic tools (pass chromadb_bucket for lazy loading)
    tools, state_holder = create_agentic_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places_api,
        chromadb_bucket=chromadb_bucket,
    )

    # Add metadata submission tool
    submit_tool = create_metadata_submission_tool(state_holder)
    tools.append(submit_tool)

    # Create LLM with tools bound using shared utility
    llm = create_ollama_llm(settings=settings, temperature=0.0)
    llm = llm.bind_tools(tools)

    # Create agent node with retry logic using shared utility
    agent_node = create_agent_node_with_retry(
        llm=llm,
        agent_name="metadata_finder",
    )

    # Define tool node
    tool_node = ToolNode(tools)

    # Define routing function
    def should_continue(state: ReceiptMetadataFinderState) -> str:
        """Check if we should continue or end."""
        # Check if metadata was submitted
        if state_holder.get("metadata_result") is not None:
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    return "tools"
                # Give it another chance if no tool calls
                if len(state.messages) > 10:
                    logger.warning(
                        "Agent has made many steps without submitting - may need reminder"
                    )
                return "agent"

        return "agent"

    # Build the graph
    workflow = StateGraph(ReceiptMetadataFinderState)

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
            "agent": "agent",  # Loop back if no tool calls
            "end": END,
        },
    )

    # After tools, go back to agent
    workflow.add_edge("tools", "agent")

    # Compile
    compiled = workflow.compile()

    return compiled, state_holder


# ==============================================================================
# Metadata Finder Runner
# ==============================================================================


async def run_receipt_metadata_finder(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    line_embeddings: Optional[dict[str, list[float]]] = None,
    word_embeddings: Optional[dict[str, list[float]]] = None,
    receipt_lines: Optional[list] = None,
    receipt_words: Optional[list] = None,
) -> dict:
    """
    Run the receipt metadata finder workflow for a receipt.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        image_id: Receipt image ID
        receipt_id: Receipt ID
        line_embeddings: Pre-loaded line embeddings (optional)
        word_embeddings: Pre-loaded word embeddings (optional)
        receipt_lines: Pre-loaded receipt lines (optional, avoids DynamoDB read)
        receipt_words: Pre-loaded receipt words (optional, avoids DynamoDB read)

    Returns:
        Metadata result dict
    """
    # Convert entity objects to dict format if provided
    lines_dict = None
    words_dict = None

    if receipt_lines:
        # Convert ReceiptLine entities to dict format expected by tools
        lines_dict = [
            {
                "line_id": line.line_id,
                "text": line.text,
                "has_embedding": (
                    _build_line_id(image_id, receipt_id, line.line_id)
                    in (line_embeddings or {})
                    if hasattr(line, "line_id")
                    else False
                ),
            }
            for line in receipt_lines
        ]

    if receipt_words:
        # Convert ReceiptWord entities to dict format expected by tools
        words_dict = [
            {
                "line_id": word.line_id,
                "word_id": word.word_id,
                "text": word.text,
                "label": getattr(word, "label", None),
            }
            for word in receipt_words
        ]

    # Set up context
    state_holder["context"] = ReceiptContext(
        image_id=image_id,
        receipt_id=receipt_id,
        lines=lines_dict,
        words=words_dict,
        metadata=None,
        line_embeddings=line_embeddings,
        word_embeddings=word_embeddings,
    )
    state_holder["metadata_result"] = None

    # Create initial state
    initial_state = ReceiptMetadataFinderState(
        image_id=image_id,
        receipt_id=receipt_id,
        messages=[
            SystemMessage(content=RECEIPT_METADATA_FINDER_PROMPT),
            HumanMessage(
                content=f"Please find ALL missing metadata for receipt {image_id}#{receipt_id}. "
                f"Start by examining the receipt content, then find any missing fields."
            ),
        ],
    )

    logger.info(
        f"Starting receipt metadata finder for {image_id}#{receipt_id}"
    )

    # Run the workflow
    try:
        config = {
            "recursion_limit": 100,
            "configurable": {
                "thread_id": f"{image_id}#{receipt_id}",
            },
        }

        # Add LangSmith metadata if tracing is enabled
        if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
            config["metadata"] = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "workflow": "receipt_metadata_finder",
            }

        await graph.ainvoke(initial_state, config=config)

        # Get result from state holder
        result = state_holder.get("metadata_result")

        if result:
            logger.info(
                f"Metadata finder complete: {len(result.get('fields_found', []))} fields found "
                f"(confidence={result.get('confidence', 0):.2%})"
            )
            return result
        else:
            # Agent ended without submitting result
            logger.warning(
                f"Agent ended without submitting metadata for {image_id}#{receipt_id}"
            )
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "place_id": None,
                "merchant_name": None,
                "address": None,
                "phone_number": None,
                "found": False,
                "confidence": 0.0,
                "reasoning": "Agent did not submit metadata result",
                "fields_found": [],
            }

    except Exception as e:
        logger.error(f"Error in receipt metadata finder: {e}")
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "place_id": None,
            "merchant_name": None,
            "address": None,
            "phone_number": None,
            "found": False,
            "confidence": 0.0,
            "reasoning": f"Error during metadata finding: {str(e)}",
            "fields_found": [],
        }
