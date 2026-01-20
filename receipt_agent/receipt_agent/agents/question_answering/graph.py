"""
LangGraph workflow for answering questions about receipts.

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

from receipt_agent.agents.question_answering.state import QuestionAnsweringState
from receipt_agent.agents.question_answering.tools import (
    QuestionContext,
    create_qa_tools,
)
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)


# ==============================================================================
# System Prompt
# ==============================================================================

SYSTEM_PROMPT = """You are a receipt analysis assistant. Your job is to answer questions about the user's receipts by searching through their receipt data.

## Available Tools

### Search Tools
- `search_lines_by_text`: Search receipt lines for specific text (e.g., "COFFEE", "MILK", "ORGANIC")
  - Use `exclude_terms` to filter out unwanted matches (e.g., exclude "CREAMER" when searching for coffee)

- `search_words_by_label`: Search for words with specific labels
  - Labels include: TAX, SUBTOTAL, GRAND_TOTAL, LINE_TOTAL, UNIT_PRICE, MERCHANT_NAME, etc.

### Detail Tools
- `get_full_receipt`: Get complete receipt with formatted text and all labeled words
  - Uses efficient GSI4 query to get all data in one call
  - Returns receipt text as visual lines with word labels (e.g., "MILK[PRODUCT_NAME] 5.99[LINE_TOTAL]")
  - Includes all labeled amounts (TAX, SUBTOTAL, GRAND_TOTAL, etc.)
  - **Use this first** when you need to understand a receipt's contents

- `get_receipt_with_price`: Get a receipt and find the price for a specific line item
  - Use this after finding matching lines to get the actual price/amount

- `get_labeled_amounts`: Get all amounts with specific labels from a receipt
  - Use this for questions about TAX, totals, etc.

### Answer Tool (REQUIRED)
- `submit_answer`: Submit your final answer
  - ALWAYS call this at the end with your answer
  - Include total_amount if the question asks for a sum
  - Include receipt_count for "how many" questions
  - Include evidence showing which receipts support your answer

## Strategy

1. **Search**: Use `search_lines_by_text` to find matching receipts
2. **Get details**: Use `get_full_receipt` to see receipt with labeled amounts
3. **Submit**: ALWAYS call `submit_answer` at the end

## CRITICAL: You MUST call submit_answer

- Every response MUST end with calling `submit_answer`
- If you can't find data, call submit_answer("I couldn't find that information")
- Never end without calling submit_answer

## Example Questions and Approaches

**"How much did I spend on coffee this year?"**
1. search_lines_by_text("COFFEE", exclude_terms=["CREAMER"])
2. For each result, get_receipt_with_price to find the price
3. Sum all amounts
4. submit_answer with total

**"Show me all receipts with dairy products"**
1. search_lines_by_text("MILK")
2. search_lines_by_text("CHEESE")
3. search_lines_by_text("YOGURT")
4. Combine results, list merchants and items
5. submit_answer with list

**"How much tax did I pay last quarter?"**
1. search_words_by_label("TAX")
2. For each result, get_labeled_amounts to confirm the amount
3. Sum all TAX amounts
4. submit_answer with total

## Important Rules

1. ALWAYS search first, then get details for matching results
2. Use exclude_terms to filter out irrelevant matches
3. ALWAYS end with submit_answer
4. If no results found, say so in the answer
5. Be efficient - don't fetch details for all results if you only need a few
"""


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_qa_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the question-answering workflow.

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

    # Create LLM with tools bound (uses OpenRouter)
    llm = create_llm(
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        temperature=0.0,
        timeout=120,
    ).bind_tools(tools)

    # Define the agent node (calls LLM)
    def agent_node(state: QuestionAnsweringState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages

        # Retry up to 3 times if we get an empty response
        max_retries = 3
        for attempt in range(max_retries):
            response = llm.invoke(messages)

            # Check if response is valid (has content or tool_calls)
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

        return {"messages": [response]}

    # Define tool node
    tool_node = ToolNode(tools)

    # Track iterations to prevent infinite loops (stored in state_holder for reset)
    state_holder["iteration_count"] = 0
    state_holder["max_iterations"] = 15

    # Define routing function
    def should_continue(state: QuestionAnsweringState) -> str:
        """Check if we should continue or end."""
        state_holder["iteration_count"] += 1
        logger.debug(
            "should_continue: iteration=%d, messages=%d",
            state_holder["iteration_count"],
            len(state.messages) if state.messages else 0,
        )

        # Check if answer was submitted
        if state_holder.get("answer") is not None:
            logger.debug("Answer already submitted, ending")
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            logger.debug(
                "Last message type: %s, has tool_calls: %s, has content: %s",
                type(last_message).__name__,
                bool(getattr(last_message, "tool_calls", None)),
                bool(getattr(last_message, "content", None)),
            )

            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    # Check for submit_answer in tool calls
                    for tc in last_message.tool_calls:
                        if tc.get("name") == "submit_answer":
                            logger.debug("Found submit_answer in tool calls")
                            return "tools"
                    # Other tool calls - continue if under limit
                    if state_holder["iteration_count"] < state_holder["max_iterations"]:
                        return "tools"
                    else:
                        logger.warning(
                            "Max iterations reached (%d), ending without answer",
                            state_holder["max_iterations"],
                        )
                        return "end"
                else:
                    # No tool calls - LLM responded with text
                    # Extract answer from content if present
                    if last_message.content:
                        logger.info(
                            "Extracting answer from LLM response (no submit_answer call): %s",
                            str(last_message.content)[:100],
                        )
                        state_holder["answer"] = {
                            "answer": str(last_message.content),
                            "total_amount": None,
                            "receipt_count": 0,
                            "evidence": [],
                        }
                    else:
                        logger.warning("AIMessage has no content and no tool_calls")

        return "end"

    # Build the graph
    workflow = StateGraph(QuestionAnsweringState)

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
# Question Runner
# ==============================================================================


async def answer_question(
    graph: Any,
    state_holder: dict,
    question: str,
) -> dict:
    """
    Run the question-answering workflow for a question.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        question: The question to answer

    Returns:
        Answer dict with answer, amount, count, and evidence
    """
    # Reset state
    state_holder["context"] = QuestionContext(question=question)
    state_holder["answer"] = None
    state_holder["iteration_count"] = 0

    # Create initial state
    initial_state = QuestionAnsweringState(
        question=question,
        messages=[
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=question),
        ],
    )

    logger.info("Answering question: %s", question[:100])

    # Run the workflow
    try:
        config = {"recursion_limit": 30}
        await graph.ainvoke(initial_state, config=config)

        # Get answer from state holder
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
) -> dict:
    """Synchronous wrapper for answer_question."""
    return asyncio.run(
        answer_question(
            graph=graph,
            state_holder=state_holder,
            question=question,
        )
    )
