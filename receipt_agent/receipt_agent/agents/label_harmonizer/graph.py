"""
Graph construction and execution for the Label Harmonizer agent.
"""

import logging
import os
from typing import TYPE_CHECKING, Any, Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.agents.label_harmonizer.state import (
    LabelHarmonizerAgentState,
)
from receipt_agent.agents.label_harmonizer.tools.factory import (
    create_label_harmonizer_tools,
)
from receipt_agent.agents.label_harmonizer.tools.helpers import label_to_dict
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.agent_common import (
    create_agent_node_with_retry,
    create_ollama_llm,
)
from receipt_agent.utils.receipt_fetching import (
    fetch_receipt_details_with_fallback,
)
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


LABEL_HARMONIZER_PROMPT = """You are a receipt label harmonizer. Focus on understanding table structure, validating financial consistency, and analyzing labels.

## Task
1) Read the receipt text (source of truth).
2) Find and analyze table structure in the receipt.
3) Validate financial information using the sub-agent.
4) Analyze labels using the label sub-agent.

## Tools (use sparingly and purposefully)
- `get_line_id_text_list`: Lines with IDs, top-to-bottom (no geometry).
- `run_table_subagent`: For a chosen financial/table block (line items/totals/tax), infer columns. Use its summary/columns for downstream financial labels.
- `validate_financial_consistency`: **Enhanced financial validation** - proves GRAND_TOTAL math, validates line-item completeness (each LINE_TOTAL needs PRODUCT_NAME/QUANTITY/UNIT_PRICE), checks QUANTITY × UNIT_PRICE = LINE_TOTAL.
- `run_label_subagent`: Focused pass for a single CORE_LABEL (optionally scoped to the table range).

## Strategy (focused workflow)
1. Call `get_line_id_text_list` to see the lines.
2. Identify the financial/table block; choose the tightest contiguous line-id range that covers line items/totals/tax.
3. **Call `run_table_subagent`** on that range to understand table structure and columns.
4. **Call `validate_financial_consistency`** to validate financial math and detect currency. This will prove GRAND_TOTAL = SUBTOTAL + TAX and other financial relationships.
5. **Call `run_label_subagent`** for key CORE_LABELs (scope to the table range when relevant) to analyze labeling.

## Financial Validation Focus
The enhanced financial validation sub-agent will:
- **Prove GRAND_TOTAL = SUBTOTAL + TAX** (±0.01 tolerance)
- **Prove SUBTOTAL = sum of LINE_TOTAL values** (±0.01 tolerance)  
- **Prove each QUANTITY × UNIT_PRICE = LINE_TOTAL** (±0.01 tolerance)
- **Find LINE_TOTALs missing required fields** (PRODUCT_NAME, QUANTITY, UNIT_PRICE)
- **Detect currency** and ensure consistency
- **Propose specific corrections** with detailed reasoning

## Rules
- Use receipt text as source of truth; avoid external info.
- **Financial math must be proven correct** - trust the enhanced validation sub-agent's corrections.
- Be systematic: table structure → financial validation → label analysis.
- Focus on understanding rather than immediate fixes.

Begin by listing the receipt lines, then run the table sub-agent, then validate financial consistency, then analyze key labels."""


def create_label_harmonizer_graph(
    dynamo_client: "DynamoClient",
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the label harmonizer agent graph.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client for similarity search
        embed_fn: Embedding function
        settings: Settings for the agent

    Returns:
        (graph, state_holder)
    """
    if settings is None:
        settings = get_settings()

    llm = create_ollama_llm(settings)

    # Create state holder (shared state for tools)
    state_holder = {
        "dynamo_client": dynamo_client,
        "chroma_client": chroma_client,
        "embed_fn": embed_fn,
        "receipt": {},
        "result": None,
    }

    # Create tools (they reference state_holder, so they'll see updates)
    tools, _ = create_label_harmonizer_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        receipt_data=state_holder["receipt"],  # Pass reference to state
        settings=settings,
    )

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # Create agent node with retry logic
    agent_node = create_agent_node_with_retry(
        llm=llm_with_tools,
        agent_name="label-harmonizer",
    )

    # Create tool node (handles async tools)
    tool_node = ToolNode(tools)

    # Create graph
    workflow = StateGraph(LabelHarmonizerAgentState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add edges
    def should_continue(state: LabelHarmonizerAgentState) -> str:
        """Check if we should continue to tools or end."""
        if not state.messages:
            return END
        last_message = state.messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")

    # Compile graph
    graph = workflow.compile()

    return graph, state_holder


async def run_label_harmonizer_agent(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    dry_run: bool = True,
) -> Any:
    """
    Run the label harmonizer agent for a single receipt.

    Args:
        graph: Compiled LangGraph graph
        state_holder: State holder dict
        image_id: Image ID containing the receipt
        receipt_id: Receipt ID within the image
        dry_run: If True, only report what would be updated

    Returns:
        ReceiptLabelResult object
    """
    from receipt_agent.agents.label_harmonizer.tools.label_harmonizer_v3 import (
        ReceiptLabelResult,
    )

    dynamo_client = state_holder.get("dynamo_client")
    if not dynamo_client:
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["DynamoDB client not available"],
        )

    # Load receipt data
    logger.info(
        "LH3 run: fetching receipt details image_id=%s receipt_id=%s",
        image_id,
        receipt_id,
    )
    receipt_details = fetch_receipt_details_with_fallback(
        dynamo_client=dynamo_client,
        image_id=image_id,
        receipt_id=receipt_id,
    )
    if receipt_details:
        logger.info(
            "LH3 receipt fetched: lines=%s words=%s labels=%s",
            len(receipt_details.lines or []),
            len(receipt_details.words or []),
            len(receipt_details.labels or []),
        )

    if not receipt_details or not receipt_details.lines:
        logger.info(
            "LH3 receipt missing or has no lines image_id=%s receipt_id=%s",
            image_id,
            receipt_id,
        )
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["Receipt not found or has no lines"],
        )

    # Extract data from receipt_details
    lines = receipt_details.lines
    words = receipt_details.words
    labels = receipt_details.labels

    # If labels are empty, try to fetch them separately (with pagination)
    if not labels:
        logger.info(
            "LH3 labels empty from ReceiptDetails, fetching word labels separately"
        )
        try:
            fetched: list[Any] = []
            lek = None
            while True:
                page, lek = dynamo_client.list_receipt_word_labels_for_receipt(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    last_evaluated_key=lek,
                )
                fetched.extend(page or [])
                if not lek:
                    break
            labels = fetched
            logger.info(
                "LH3 fetched %s labels via list_receipt_word_labels_for_receipt",
                len(labels),
            )
        except Exception as e:
            logger.info("LH3 could not fetch labels separately: %s", e)
            labels = []

    # Prepare receipt data for state
    def _safe_geom(obj: Any) -> dict:
        return {
            "bounding_box": getattr(obj, "bounding_box", None),
            "top_left": getattr(obj, "top_left", None),
            "top_right": getattr(obj, "top_right", None),
            "bottom_left": getattr(obj, "bottom_left", None),
            "bottom_right": getattr(obj, "bottom_right", None),
            "angle_degrees": getattr(obj, "angle_degrees", None),
            "angle_radians": getattr(obj, "angle_radians", None),
            "confidence": getattr(obj, "confidence", None),
        }

    words_data = []
    for w in words:
        entry = {
            "image_id": str(w.image_id),
            "receipt_id": int(w.receipt_id),
            "line_id": int(w.line_id),
            "word_id": int(w.word_id),
            "text": w.text or "",
        }
        entry.update(_safe_geom(w))
        words_data.append(entry)

    lines_data = []
    for l in lines:
        entry = {
            "image_id": str(l.image_id),
            "receipt_id": int(l.receipt_id),
            "line_id": int(l.line_id),
            "text": l.text or "",
        }
        entry.update(_safe_geom(l))
        lines_data.append(entry)

    labels_data = [label_to_dict(label) for label in labels]

    # Format receipt text; fall back to simple join if fields are missing
    try:
        from receipt_dynamo.entities import ReceiptLine

        receipt_lines = [
            ReceiptLine(**line) if isinstance(line, dict) else line
            for line in lines
        ]
        receipt_text = format_receipt_text_receipt_space(receipt_lines)
    except Exception as e:
        logger.info("LH3 receipt_text fallback formatting: %s", e)
        receipt_text = "\n".join(l.get("text", "") for l in lines if l)

    if not (receipt_text or "").strip():
        logger.info(
            "LH3 receipt_text empty after formatting, image_id=%s receipt_id=%s",
            image_id,
            receipt_id,
        )
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["Receipt has no text after formatting"],
        )

    # Load receipt metadata for context and tools
    receipt_metadata = None
    try:
        receipt_metadata = dynamo_client.get_receipt_metadata(image_id, receipt_id)
    except Exception as e:
        logger.debug(f"Could not load receipt metadata: {e}")

    # Update state holder with receipt data (mutate existing dict to preserve reference used by tools)
    receipt_state = state_holder.get("receipt")
    if receipt_state is None:
        receipt_state = {}
        state_holder["receipt"] = receipt_state

    receipt_state.clear()
    receipt_state.update(
        {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "words": words_data,
            "lines": lines_data,
            "labels": labels_data,
            "receipt_text": receipt_text,
        }
    )
    
    # Add receipt metadata to state if available
    if receipt_metadata:
        receipt_state["metadata"] = {
            "merchant_name": receipt_metadata.merchant_name,
            "place_id": receipt_metadata.place_id,
            "address": receipt_metadata.address,
            "phone_number": receipt_metadata.phone_number,
            "website": receipt_metadata.website,
        }

    logger.info(
        "LH3 state ready: words=%s lines=%s labels=%s text_len=%s",
        len(words_data),
        len(lines_data),
        len(labels_data),
        len(receipt_text or ""),
    )

    # Create initial state with system prompt
    initial_state = LabelHarmonizerAgentState(
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_text=receipt_text,
        words=words_data,
        lines=lines_data,
        labels=labels_data,
        messages=[
            SystemMessage(content=LABEL_HARMONIZER_PROMPT),
            HumanMessage(
                content=f"Please harmonize labels for receipt {image_id}#{receipt_id}. "
                f"Start by getting the receipt text, then validate financial consistency."
            ),
        ],
    )

    # Run agent
    try:
        config = {
            "recursion_limit": 50,
            "configurable": {"thread_id": f"{image_id}#{receipt_id}"},
        }

        # Add LangSmith metadata if tracing is enabled
        if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
            config["metadata"] = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "label_count": len(labels_data),
                "word_count": len(words_data),
                "line_count": len(lines_data),
                "workflow": "label_harmonizer_v3",
                "merchant_name": receipt_metadata.merchant_name if receipt_metadata else None,
                "place_id": receipt_metadata.place_id if receipt_metadata else None,
            }

        await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        logger.exception(f"Agent execution failed: {e}")
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=[str(e)],
        )

    # Extract result
    result_data = state_holder.get("result")
    if not result_data:
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["No harmonization result submitted"],
        )

    # Build updates list (already enriched by submit_harmonization tool)
    updates = result_data.get("updates", [])

    # Extract totals issues from validation
    totals_issues = []
    validation_result = result_data.get("totals_validation", {})
    if isinstance(validation_result, dict):
        totals_issues = validation_result.get("issues", [])

    # Build result
    result = ReceiptLabelResult(
        image_id=image_id,
        receipt_id=receipt_id,
        total_labels=len(labels_data),
        labels_updated=len(updates),
        currency_detected=result_data.get("currency"),
        totals_valid=result_data.get("totals_valid", False),
        totals_issues=totals_issues,
        confidence=result_data.get("confidence", 0.0),
        reasoning=result_data.get("reasoning", ""),
        updates=updates,
    )

    return result
