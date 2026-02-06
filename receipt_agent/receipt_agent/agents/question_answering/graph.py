"""
LangGraph workflow for answering questions about receipts.

5-node ReAct RAG workflow:
- plan: Classify question, determine retrieval strategy
- agent: ReAct tool loop with retry logic
- tools: Standard ToolNode for tool execution
- shape: Post-retrieval context processing
- synthesize: Dedicated answer generation

Flow: START -> plan -> agent <-> tools -> shape -> synthesize -> END

This agent uses ChromaDB for semantic search and DynamoDB for receipt data
to answer questions like:
- "How much did I spend on coffee this year?"
- "Show me all receipts with dairy products"
- "How much tax did I pay last quarter?"
"""

import asyncio
import logging
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.agents.question_answering.state import (
    AmountItem,
    AnswerWithEvidence,
    QAState,
    QuestionClassification,
    ReceiptSummary,
    RetrievedContext,
)
from receipt_agent.agents.question_answering.tools import (
    SYSTEM_PROMPT,
    create_qa_tools,
)
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)


# ==============================================================================
# System Prompts
# ==============================================================================

PLAN_SYSTEM_PROMPT = """You are a question classifier for a receipt analysis system.

Analyze the user's question and classify it to determine the best retrieval strategy.

## Question Types
- specific_item: Questions about a specific product/item (e.g., "How much did I spend on coffee?")
- aggregation: Questions requiring summing across receipts (e.g., "Total spending this month?")
- time_based: Questions filtered by date/time (e.g., "Receipts from last week")
- comparison: Questions comparing categories (e.g., "Did I spend more on groceries or dining?")
- list_query: Questions asking to list/enumerate (e.g., "Show all dairy receipts")
- metadata_query: Questions about merchants/places (e.g., "Which stores have I visited?")

## Retrieval Strategies
- simple_lookup: Single search, expect few results (specific_item, metadata_query)
- multi_source: Multiple searches with different terms (comparison, list_query)
- exhaustive_scan: Need to check many receipts (aggregation)
- semantic_hybrid: Combine text and semantic search (when terms are unclear)

## Query Rewrites
Suggest alternative search terms if the initial query might not find results.
Include BOTH product terms AND merchant names for category queries.

Examples:
- "coffee" -> ["COFFEE", "ESPRESSO", "LATTE", "CAPPUCCINO", "Starbucks", "Blue Bottle"]
- "groceries" -> ["GROCERY", "PRODUCE", "Trader Joe's", "Sprouts", "Costco"]
- "restaurants" -> ["RESTAURANT", "DINING", "Sweetgreen", "Chipotle"]

## Tools to Use
Recommend which tools based on question type:
- specific_item: search_receipts, semantic_search, get_receipt_summaries (if merchant known)
- aggregation: get_receipt_summaries, search_receipts (label), aggregate_amounts
- time_based: get_receipt_summaries, search_receipts
- comparison: search_receipts (multiple), get_receipt_summaries, aggregate_amounts
- list_query: search_receipts (multiple), get_receipt
- metadata_query: list_merchants, get_receipts_by_merchant

IMPORTANT: For category queries (coffee, groceries, etc.), use BOTH:
1. search_product_lines for items mentioning the category
2. get_receipt_summaries with merchant_filter for known merchants
"""

SYNTHESIZE_SYSTEM_PROMPT = """You are synthesizing a final answer about receipts.

You have been given:
1. **Agent Analysis** — the reasoning and conclusions from the retrieval agent that searched the receipt database. This analysis has seen ALL tool results, not just the shaped subset below.
2. **Receipt Data** — structured receipt summaries for evidence and citation.
3. **Pre-computed Aggregates** — verified totals from the database.

## Critical Rules
1. **Trust the agent's analysis.** The agent saw the full tool output. If the agent concluded a total of $X across Y receipts, use that figure — do not re-derive a different total from the receipt data below.
2. **If the agent excluded items** (e.g., "excluding baking chocolate chips"), respect those exclusions. Do not re-include them.
3. **Use receipt data for evidence/citations**, not for re-computing totals that the agent already computed.
4. **Use pre-computed aggregates** when available — they are database-verified totals.
5. Be precise with amounts — use exact numbers.
6. For "how much" questions, always state the total.
7. If the agent found nothing relevant, say so clearly.

## Evidence Format
Include evidence array with:
- image_id: Receipt identifier
- receipt_id: Receipt number
- item: Relevant item/description
- amount: Dollar amount (if applicable)
"""


# ==============================================================================
# Plan Node
# ==============================================================================


def create_plan_node(llm: Any) -> Callable:
    """Create the plan node for question classification."""

    # Use structured output for classification
    classification_llm = llm.with_structured_output(QuestionClassification)

    def plan_node(state: QAState) -> dict:
        """Classify the question and determine retrieval strategy."""
        question = state.question

        # Build prompt for classification
        messages = [
            SystemMessage(content=PLAN_SYSTEM_PROMPT),
            HumanMessage(content=f"Classify this question: {question}"),
        ]

        try:
            classification = classification_llm.invoke(messages)
            logger.info(
                "Question classified: type=%s, strategy=%s",
                classification.question_type,
                classification.retrieval_strategy,
            )

            return {
                "classification": classification,
                "current_phase": "retrieve",
            }

        except Exception as e:
            logger.warning("Classification failed: %s, using defaults", e)
            # Default classification on failure
            return {
                "classification": QuestionClassification(
                    question_type="specific_item",
                    retrieval_strategy="simple_lookup",
                    query_rewrites=[],
                    tools_to_use=["search_receipts", "get_receipt"],
                ),
                "current_phase": "retrieve",
            }

    return plan_node


# ==============================================================================
# Agent Node
# ==============================================================================


def create_agent_node(
    llm: Any,
    tools: list,
    state_holder: dict,
) -> Callable:
    """Create the agent node with classification context."""

    llm_with_tools = llm.bind_tools(tools)

    def agent_node(state: QAState) -> dict:
        """Call the LLM to decide next action with classification context."""
        messages = list(state.messages)

        # Include classification in context if available
        if state.classification:
            classification_context = (
                f"\n\n## Classification Context\n"
                f"- Question type: {state.classification.question_type}\n"
                f"- Retrieval strategy: {state.classification.retrieval_strategy}\n"
                f"- Suggested tools: {', '.join(state.classification.tools_to_use)}\n"
                f"- Query alternatives: {', '.join(state.classification.query_rewrites)}\n"
            )
            # Append to system message if present
            if messages and isinstance(messages[0], SystemMessage):
                messages[0] = SystemMessage(
                    content=messages[0].content + classification_context
                )

        # Add context about what's been searched (helps avoid redundant searches)
        searches = state_holder.get("searches", [])
        if searches:
            search_summary = "\n".join(
                [
                    f"- {s['type']} search for '{s['query']}': {s['result_count']} results"
                    for s in searches[-10:]  # Last 10 searches
                ]
            )
            context_msg = f"\n\nSearches already performed:\n{search_summary}"

            # Append to system message if present
            if messages and isinstance(messages[0], SystemMessage):
                messages[0] = SystemMessage(
                    content=messages[0].content + context_msg
                )

        # Retry up to 3 times if we get an empty response
        max_retries = 3
        for attempt in range(max_retries):
            response = llm_with_tools.invoke(messages)

            # Check if response is valid
            has_content = bool(getattr(response, "content", None))
            has_tool_calls = bool(getattr(response, "tool_calls", None))

            if has_content or has_tool_calls:
                break

            if attempt < max_retries - 1:
                logger.warning(
                    "Empty response from LLM (attempt %d/%d), retrying...",
                    attempt + 1,
                    max_retries,
                )
            else:
                logger.warning(
                    "Empty response from LLM after %d attempts",
                    max_retries,
                )

        # Log tool calls for debugging
        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.debug(
                "Agent tool calls: %s",
                [tc["name"] for tc in response.tool_calls],
            )

        # Track iteration
        new_iteration = state.iteration_count + 1
        state_holder["iteration_count"] = new_iteration

        return {
            "messages": [response],
            "iteration_count": new_iteration,
        }

    return agent_node


# ==============================================================================
# Shape Node
# ==============================================================================


def _extract_line_items_from_structured(
    words_by_line: dict[int, list[dict]],
    amounts: list[dict],
) -> list[AmountItem]:
    """Extract product-price pairs from structured word/label data.

    Uses the words_by_line dict (line_idx -> list of {text, label, word_id, x})
    and amounts list ({label, text, amount, line_idx, word_id}) to find
    product names associated with prices.

    For each LINE_TOTAL or UNIT_PRICE, collects PRODUCT_NAME words on the same line.
    """
    line_items: list[AmountItem] = []

    # Process each amount that's a line item price
    for amt in amounts:
        label = amt.get("label", "")
        if label not in ("LINE_TOTAL", "UNIT_PRICE"):
            continue

        amount_value = amt.get("amount")
        if amount_value is None or amount_value < 0.50:
            continue

        line_idx = amt.get("line_idx")
        if line_idx is None:
            continue

        # Get all words on this line
        line_words = words_by_line.get(line_idx, [])
        if not line_words:
            continue

        # Collect PRODUCT_NAME words (sorted by x position, left to right)
        product_words = [
            w
            for w in sorted(line_words, key=lambda w: w.get("x", 0))
            if w.get("label") == "PRODUCT_NAME"
        ]

        if product_words:
            # Join product name words
            product_name = " ".join(w["text"] for w in product_words)
        else:
            # Fallback: collect unlabeled words before the price
            # (excluding the price word itself and common non-product labels)
            price_word_id = amt.get("word_id")
            non_product_labels = {
                "TAX",
                "SUBTOTAL",
                "GRAND_TOTAL",
                "LINE_TOTAL",
                "UNIT_PRICE",
                "QUANTITY",
                "MERCHANT_NAME",
            }
            unlabeled_words = [
                w
                for w in sorted(line_words, key=lambda w: w.get("x", 0))
                if w.get("word_id") != price_word_id
                and w.get("label") not in non_product_labels
            ]
            product_name = " ".join(w["text"] for w in unlabeled_words)

        # Clean up product name
        product_name = product_name.strip()
        # Remove leading numbers (SKU codes)
        import re

        product_name = re.sub(r"^\d+\s*", "", product_name).strip()

        if product_name and amount_value > 0:
            line_items.append(
                AmountItem(
                    label=label,
                    amount=amount_value,
                    item_text=product_name,
                )
            )

    return line_items


def create_shape_node(state_holder: dict) -> Callable:
    """Create the context shaping node.

    Transforms raw receipt data into structured summaries using
    words_by_line and amounts data directly (no text parsing).
    """

    def shape_node(state: QAState) -> dict:
        """Convert retrieved receipts to structured summaries.

        Uses two tiers:
        1. Detail-tier: auto-fetched receipts with line items (words_by_line)
        2. Summary-tier: lightweight summaries from get_receipt_summaries
           (has merchant, date, grand_total, tax, tip, item_count — no line items)

        Detail-tier receipts take priority; summary-tier fills in the rest.
        """
        retrieved_receipts = state_holder.get("retrieved_receipts", [])
        summary_receipts = state_holder.get("summary_receipts", [])

        logger.info(
            "Shaping %d detail + %d summary receipts",
            len(retrieved_receipts),
            len(summary_receipts),
        )

        seen_keys: set[tuple] = set()
        summaries: list[ReceiptSummary] = []

        # TIER 1: Detail-shaped receipts (have line items)
        for receipt in retrieved_receipts:
            key = (receipt.get("image_id"), receipt.get("receipt_id"))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            words_by_line = receipt.get("words_by_line", {})
            amounts = receipt.get("amounts", [])

            grand_total = None
            tax = None
            for amt in amounts:
                label = amt.get("label", "")
                amount_value = amt.get("amount")
                if label == "GRAND_TOTAL" and amount_value is not None:
                    grand_total = amount_value
                elif label == "TAX" and amount_value is not None:
                    tax = amount_value

            line_items = _extract_line_items_from_structured(
                words_by_line, amounts
            )

            labels_found = set()
            for line_words in words_by_line.values():
                for w in line_words:
                    if w.get("label"):
                        labels_found.add(w["label"])

            summaries.append(
                ReceiptSummary(
                    image_id=receipt.get("image_id", ""),
                    receipt_id=receipt.get("receipt_id", 0),
                    merchant=receipt.get("merchant", "Unknown"),
                    grand_total=grand_total,
                    tax=tax,
                    line_items=line_items,
                    labels_found=list(labels_found),
                )
            )

        # TIER 2: Summary-only receipts (no line items, but have totals/date/tip)
        for s in summary_receipts:
            key = (s.get("image_id"), s.get("receipt_id"))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            summaries.append(
                ReceiptSummary(
                    image_id=s.get("image_id", ""),
                    receipt_id=s.get("receipt_id", 0),
                    merchant=s.get("merchant_name") or "Unknown",
                    grand_total=s.get("grand_total"),
                    tax=s.get("tax"),
                    tip=s.get("tip"),
                    date=s.get("date"),
                    item_count=s.get("item_count"),
                    line_items=[],
                    labels_found=[],
                )
            )

        # Limit to reasonable number — summaries are ~100 chars each
        MAX_RECEIPTS = 200
        limited_summaries = summaries[:MAX_RECEIPTS]

        total_line_items = sum(len(s.line_items) for s in limited_summaries)
        detail_count = sum(1 for s in limited_summaries if s.line_items)
        summary_only_count = len(limited_summaries) - detail_count
        logger.info(
            "Shaped to %d receipts (%d detail, %d summary-only, %d line items)",
            len(limited_summaries),
            detail_count,
            summary_only_count,
            total_line_items,
        )

        if not limited_summaries and (retrieved_receipts or summary_receipts):
            logger.warning("No summaries after shaping, may need retry")

        return {
            "shaped_summaries": limited_summaries,
            "current_phase": "synthesize",
        }

    return shape_node


# ==============================================================================
# Synthesize Node
# ==============================================================================


def create_synthesize_node(llm: Any, state_holder: dict) -> Callable:
    """Create the answer synthesis node.

    Works with structured ReceiptSummary objects instead of raw text.
    """

    def synthesize_node(state: QAState) -> dict:
        """Generate final answer from structured receipt summaries."""
        summaries = state.shaped_summaries
        logger.info(
            "Synthesizing answer from %d receipt summaries", len(summaries)
        )

        # Extract agent's reasoning from the last AIMessage
        agent_reasoning = ""
        for msg in reversed(state.messages):
            if isinstance(msg, AIMessage) and msg.content:
                content = str(msg.content)
                # Truncate to avoid blowing up the context
                if len(content) > 3000:
                    content = content[:3000] + "\n[... truncated]"
                agent_reasoning = content
                break

        # Build structured context for the LLM
        context_parts = []
        for i, summary in enumerate(summaries):
            # Format line items compactly
            items_str = ""
            if summary.line_items:
                items = [
                    (
                        f"  - {item.item_text}: ${item.amount:.2f}"
                        if item.item_text
                        else f"  - ${item.amount:.2f}"
                    )
                    for item in summary.line_items
                ]
                items_str = "\n" + "\n".join(items)

            # Build receipt summary
            receipt_info = (
                f"Receipt {i + 1}: {summary.merchant}\n"
                f"  ID: {summary.image_id}:{summary.receipt_id}"
            )
            if summary.date:
                receipt_info += f"\n  Date: {summary.date}"
            if summary.grand_total is not None:
                receipt_info += f"\n  Total: ${summary.grand_total:.2f}"
            if summary.tax is not None:
                receipt_info += f"\n  Tax: ${summary.tax:.2f}"
            if summary.tip is not None and summary.tip > 0:
                receipt_info += f"\n  Tip: ${summary.tip:.2f}"
            if summary.item_count is not None and not summary.line_items:
                receipt_info += f"\n  Items: {summary.item_count}"
            if items_str:
                receipt_info += f"\n  Items:{items_str}"

            context_parts.append(receipt_info)

        context_str = (
            "\n\n".join(context_parts)
            if context_parts
            else "(No receipts found)"
        )

        # Check for pre-computed aggregation
        aggregated = state_holder.get("aggregated_amount")
        if aggregated:
            context_str += (
                f"\n\nPre-computed Total: ${aggregated.get('total', 0):.2f}"
            )

        # Append pre-computed aggregates from tool calls
        aggregates = state_holder.get("aggregates", [])
        if aggregates:
            agg_parts = ["\n\nPre-computed Aggregates:"]
            for agg in aggregates:
                avg_str = (
                    f", avg=${agg['average_receipt']:.2f}"
                    if agg.get("average_receipt")
                    else ""
                )
                agg_parts.append(
                    f"  {agg['source']}: {agg['count']} receipts, "
                    f"total=${agg['total_spending']:.2f}{avg_str}"
                )
            context_str += "\n".join(agg_parts)

        # Build synthesis prompt
        agent_section = ""
        if agent_reasoning:
            agent_section = f"Agent Analysis:\n{agent_reasoning}\n\n"

        messages = [
            SystemMessage(content=SYNTHESIZE_SYSTEM_PROMPT),
            HumanMessage(
                content=f"Question: {state.question}\n\n"
                f"{agent_section}"
                f"Receipt Data:\n{context_str}\n\n"
                f"Generate a clear answer with specific amounts and evidence."
            ),
        ]

        response = llm.invoke(messages)
        answer_text = (
            response.content if hasattr(response, "content") else str(response)
        )

        # Extract amount from aggregation if available
        total_amount = None
        if aggregated:
            total_amount = aggregated.get("total")

        # Build evidence from structured summaries
        evidence = []
        for summary in summaries:
            for item in summary.line_items:
                evidence.append(
                    {
                        "image_id": summary.image_id,
                        "receipt_id": summary.receipt_id,
                        "merchant": summary.merchant,
                        "item": item.item_text or "",
                        "amount": item.amount,
                    }
                )

        # Store in state_holder for extraction
        state_holder["answer"] = {
            "answer": answer_text,
            "total_amount": total_amount,
            "receipt_count": len(summaries),
            "evidence": evidence,
        }

        # Also store classification for the caller
        if state.classification:
            state_holder["classification"] = {
                "question_type": state.classification.question_type,
                "retrieval_strategy": state.classification.retrieval_strategy,
            }

        logger.info("Synthesized answer: %s", answer_text[:100])

        return {
            "final_answer": answer_text,
            "total_amount": total_amount,
            "receipt_count": len(summaries),
            "evidence": evidence,
            "current_phase": "complete",
        }

    return synthesize_node


# ==============================================================================
# Routing Functions
# ==============================================================================


def route_after_agent(state: QAState, state_holder: dict) -> str:
    """Route after agent node: tools, shape, or end."""
    # Check if answer was submitted via tool
    if state_holder.get("answer") is not None:
        logger.debug("Answer already submitted, going to end")
        return "end"

    # Check if retrieval complete signal was given
    if state_holder.get("retrieval_complete"):
        logger.debug("Retrieval complete, going to shape")
        return "shape"

    # Check last message for tool calls
    if state.messages:
        last_message = state.messages[-1]
        if isinstance(last_message, AIMessage):
            if last_message.tool_calls:
                # Has tool calls - execute them if under limit
                if state.iteration_count < 15:
                    return "tools"
                else:
                    logger.warning(
                        "Max iterations reached (%d), going to shape",
                        state.iteration_count,
                    )
                    return "shape"
            else:
                # No tool calls - LLM responded with text
                # Check if we have retrieved anything
                if state_holder.get("retrieved_receipts"):
                    return "shape"
                else:
                    # Extract answer from content
                    if last_message.content:
                        state_holder["answer"] = {
                            "answer": str(last_message.content),
                            "total_amount": None,
                            "receipt_count": 0,
                            "evidence": [],
                        }
                    return "end"

    return "end"


def route_after_tools(state: QAState, state_holder: dict) -> str:
    """Route after tools node."""
    # Check if answer was submitted
    if state_holder.get("answer") is not None:
        return "end"

    # Check if retrieval complete
    if state_holder.get("retrieval_complete"):
        return "shape"

    # Default: back to agent
    return "agent"


def route_after_shape(state: QAState, state_holder: dict) -> str:
    """Route after shape node: synthesize or retry."""
    # If context is empty but we retrieved something, might need to retry
    if state.should_retry_retrieval:
        logger.info("Empty context after shaping, retrying retrieval")
        # Reset retrieval complete flag
        state_holder["retrieval_complete"] = False
        return "agent"

    return "synthesize"


# ==============================================================================
# Graph Builder
# ==============================================================================


def create_qa_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the 5-node question-answering workflow.

    Flow: START -> plan -> agent <-> tools -> shape -> synthesize -> END

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Function to generate embeddings
        settings: Optional settings

    Returns:
        (compiled_graph, state_holder) - The graph and state dict
    """
    if settings is None:
        settings = get_settings()

    # Create tools with injected dependencies
    tools, state_holder = create_qa_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    # Create LLM (uses OpenRouter)
    llm = create_llm(
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        temperature=0.0,
        timeout=120,
    )

    # Initialize state holder
    state_holder["iteration_count"] = 0
    state_holder["max_iterations"] = 15

    # Create nodes
    plan_node = create_plan_node(llm)
    agent_node = create_agent_node(llm, tools, state_holder)
    tool_node = ToolNode(tools)
    shape_node = create_shape_node(state_holder)
    synthesize_node = create_synthesize_node(llm, state_holder)

    # Build graph
    workflow = StateGraph(QAState)

    # Add nodes
    workflow.add_node("plan", plan_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("shape", shape_node)
    workflow.add_node("synthesize", synthesize_node)

    # Set entry point
    workflow.set_entry_point("plan")

    # Add edges
    workflow.add_edge("plan", "agent")

    # Agent routing
    workflow.add_conditional_edges(
        "agent",
        lambda state: route_after_agent(state, state_holder),
        {
            "tools": "tools",
            "shape": "shape",
            "end": END,
        },
    )

    # Tools routing
    workflow.add_conditional_edges(
        "tools",
        lambda state: route_after_tools(state, state_holder),
        {
            "agent": "agent",
            "shape": "shape",
            "end": END,
        },
    )

    # Shape routing
    workflow.add_conditional_edges(
        "shape",
        lambda state: route_after_shape(state, state_holder),
        {
            "agent": "agent",
            "synthesize": "synthesize",
        },
    )

    # Synthesize always ends
    workflow.add_edge("synthesize", END)

    compiled = workflow.compile()
    return compiled, state_holder


# ==============================================================================
# Question Runner
# ==============================================================================


async def answer_question(
    graph: Any,
    state_holder: dict,
    question: str,
    callbacks: Optional[list] = None,
) -> dict:
    """
    Run the question-answering workflow for a question.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        question: The question to answer
        callbacks: Optional list of LangChain callbacks for cost tracking

    Returns:
        Answer dict with answer, amount, count, and evidence
    """
    # Reset state
    state_holder["answer"] = None
    state_holder["classification"] = None
    state_holder["iteration_count"] = 0
    state_holder["retrieval_complete"] = False
    state_holder["retrieved_receipts"] = []
    state_holder["summary_receipts"] = []
    state_holder["_summary_keys"] = set()
    state_holder["aggregates"] = []
    state_holder["searches"] = []
    state_holder["fetched_receipt_keys"] = set()

    # Create initial state
    initial_state = QAState(
        question=question,
        messages=[
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=question),
        ],
    )

    logger.info("Answering question: %s", question[:100])

    try:
        import os

        model_name = os.environ.get("OPENROUTER_MODEL") or os.environ.get(
            "RECEIPT_AGENT_OPENROUTER_MODEL", "x-ai/grok-4.1-fast"
        )
        provider = (
            model_name.split("/")[0] if "/" in model_name else "openrouter"
        )

        config = {
            "recursion_limit": 30,
            "metadata": {
                "ls_provider": provider,
                "ls_model_name": model_name,
            },
        }
        if callbacks:
            config["callbacks"] = callbacks

        await graph.ainvoke(initial_state, config=config)

        answer = state_holder.get("answer")

        if answer:
            logger.info("Answer: %s", answer["answer"][:100])
            return answer
        else:
            logger.warning("Agent ended without submitting answer")
            return {
                "answer": "I couldn't find enough information to answer that question.",
                "total_amount": None,
                "receipt_count": 0,
                "evidence": [],
            }

    except Exception as e:
        logger.error("Error answering question: %s", e)
        return {
            "answer": f"Error: {str(e)}",
            "total_amount": None,
            "receipt_count": 0,
            "evidence": [],
        }


def answer_question_sync(
    graph: Any,
    state_holder: dict,
    question: str,
    callbacks: Optional[list] = None,
) -> dict:
    """Synchronous wrapper for answer_question."""
    return asyncio.run(
        answer_question(
            graph=graph,
            state_holder=state_holder,
            question=question,
            callbacks=callbacks,
        )
    )


# Backwards compatibility alias
SYNTHESIZE_PROMPT = SYNTHESIZE_SYSTEM_PROMPT
