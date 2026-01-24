"""
LangGraph workflow for answering questions about receipts.

Enhanced ReAct RAG workflow with 5 nodes:
- plan: Classify question, determine retrieval strategy
- agent: ReAct tool loop with retry logic
- tools: Standard ToolNode for tool execution
- shape: Post-retrieval context processing
- synthesize: Dedicated answer generation

Flow: START → plan → agent ⟷ tools → shape → synthesize → END

This agent uses ChromaDB for semantic search and DynamoDB for receipt data
to answer questions like:
- "How much did I spend on coffee this year?"
- "Show me all receipts with dairy products"
- "How much tax did I pay last quarter?"
"""

import asyncio
import logging
from typing import Any, Callable, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from receipt_agent.agents.question_answering.state import (
    EnhancedQAState,
    QuestionAnsweringState,
    QuestionClassification,
    RetrievedContext,
)
from receipt_agent.agents.question_answering.tools_simplified import (
    SIMPLIFIED_SYSTEM_PROMPT,
    create_simplified_qa_tools,
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
- specific_product: Questions about a specific named product (e.g., "How much was the Kirkland Olive Oil?")
- category_query: Questions about a product category or concept (e.g., "How much on coffee?", "dairy spending")
- aggregation: Questions requiring summing across receipts (e.g., "Total spending this month?")
- time_based: Questions filtered by date/time (e.g., "Receipts from last week")
- comparison: Questions comparing categories (e.g., "Did I spend more on groceries or dining?")
- list_query: Questions asking to list/enumerate (e.g., "Show all dairy receipts")
- metadata_query: Questions about merchants/places (e.g., "Which stores have I visited?")

## Retrieval Strategies

- text_only: Use when searching for a specific, unique product name
  - Example: "Kirkland Signature Olive Oil" - exact text match needed

- semantic_first: Use when the query is a broad category or concept
  - Example: "coffee" - could be grocery coffee, café drinks, espresso, cold brew
  - Example: "dairy" - could be milk, cheese, yogurt, butter, cream
  - Example: "snacks" - chips, crackers, cookies, etc.
  - The semantic search finds conceptually related items that don't contain the literal word

- hybrid_comprehensive: Use BOTH text AND semantic search for complete coverage
  - Best for spending questions on categories: "How much did I spend on coffee?"
  - Text search catches exact matches (COFFEE, FRENCH ROAST COFFEE)
  - Semantic search catches related items (cappuccino, americano, latte, espresso)
  - Deduplicate results by (image_id, receipt_id, line_text) before aggregating

- aggregation_direct: Use get_receipt_summaries for merchant/category/date aggregation
  - Example: "Total at Costco?" → merchant_filter
  - Example: "Grocery spending?" → category_filter
  - Example: "Last month's spending?" → date filters

## Semantic Search Indicators
Use semantic search (semantic_first or hybrid_comprehensive) when the query involves:
- Food/drink categories: coffee, dairy, produce, meat, snacks, beverages, alcohol
- General concepts: organic, healthy, junk food, breakfast items, cleaning supplies
- Activity-based: dining out, groceries, gas, pharmacy
- Ambiguous terms that could have many variants

## Query Rewrites
For hybrid_comprehensive, suggest BOTH:
1. Text search terms: exact product words likely on receipts
2. Semantic queries: natural language descriptions

Example for "coffee":
- text_terms: ["COFFEE", "ESPRESSO", "COLD BREW", "ROAST"]
- semantic_queries: ["coffee drinks", "café beverages", "espresso drinks"]

Example for "dairy":
- text_terms: ["MILK", "CHEESE", "YOGURT", "BUTTER", "CREAM"]
- semantic_queries: ["dairy products", "milk and cheese"]

## Tools to Use
- specific_product: search_product_lines (text), get_receipt
- category_query: search_product_lines (text AND semantic), dedupe, aggregate
- aggregation: get_receipt_summaries (fastest for totals)
- time_based: get_receipt_summaries with date filters
- comparison: get_receipt_summaries (multiple calls), compare
- list_query: search_receipts (multiple), get_receipt
- metadata_query: list_merchants, list_categories, get_receipts_by_merchant

## Classification Output Examples

### Question: "How much did I spend on coffee?"
```json
{
  "question_type": "category_query",
  "retrieval_strategy": "hybrid_comprehensive",
  "text_search_terms": ["COFFEE", "ESPRESSO", "COLD BREW", "ROAST"],
  "semantic_queries": ["coffee drinks", "café beverages", "espresso latte cappuccino americano"],
  "tools_to_use": ["search_product_lines"]
}
```

### Question: "How much did I spend on dairy?"
```json
{
  "question_type": "category_query",
  "retrieval_strategy": "hybrid_comprehensive",
  "text_search_terms": ["MILK", "CHEESE", "YOGURT", "BUTTER", "CREAM"],
  "semantic_queries": ["dairy products", "milk cheese yogurt butter"],
  "tools_to_use": ["search_product_lines"]
}
```

### Question: "Total spending at Costco?"
```json
{
  "question_type": "aggregation",
  "retrieval_strategy": "aggregation_direct",
  "text_search_terms": [],
  "semantic_queries": [],
  "tools_to_use": ["get_receipt_summaries"]
}
```

### Question: "How much was the Kirkland Olive Oil?"
```json
{
  "question_type": "specific_product",
  "retrieval_strategy": "text_only",
  "text_search_terms": ["KIRKLAND OLIVE OIL"],
  "semantic_queries": [],
  "tools_to_use": ["search_product_lines", "get_receipt"]
}
```
"""

SYNTHESIZE_SYSTEM_PROMPT = """You are synthesizing an answer about receipts based on retrieved context.

You have been given shaped context containing relevant receipt information.
Generate a clear, accurate answer with citations to specific receipts.

## Guidelines
1. Be precise with amounts - use exact numbers from the receipts
2. Cite specific receipts by merchant name when possible
3. For "how much" questions, always state the total
4. If context is insufficient, acknowledge what's missing
5. Structure list responses clearly

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


def create_plan_node(llm: Any, state_holder: dict) -> Callable:
    """Create the plan node for question classification."""

    # Use structured output for classification
    classification_llm = llm.with_structured_output(QuestionClassification)

    def plan_node(state: EnhancedQAState) -> dict:
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

            # Store in state_holder for external access
            state_holder["classification"] = classification

            return {
                "classification": classification,
                "current_phase": "retrieve",
            }

        except Exception as e:
            logger.warning("Classification failed: %s, using defaults", e)
            # Default classification on failure - use hybrid for robustness
            default_classification = QuestionClassification(
                question_type="category_query",
                retrieval_strategy="hybrid_comprehensive",
                text_search_terms=[],
                semantic_queries=[],
                tools_to_use=["search_product_lines", "get_receipt"],
            )
            state_holder["classification"] = default_classification
            return {
                "classification": default_classification,
                "current_phase": "retrieve",
            }

    return plan_node


# ==============================================================================
# Agent Node (Enhanced)
# ==============================================================================


def create_agent_node(
    llm: Any,
    tools: list,
    state_holder: dict,
) -> Callable:
    """Create the enhanced agent node with classification context."""

    llm_with_tools = llm.bind_tools(tools)

    def agent_node(state: EnhancedQAState) -> dict:
        """Call the LLM to decide next action with classification context."""
        messages = list(state.messages)

        # Include classification in context if available
        if state.classification:
            classification_context = (
                f"\n\n## Classification Context\n"
                f"- Question type: {state.classification.question_type}\n"
                f"- Retrieval strategy: {state.classification.retrieval_strategy}\n"
                f"- Suggested tools: {', '.join(state.classification.tools_to_use)}\n"
            )
            if state.classification.text_search_terms:
                classification_context += (
                    f"- Text search terms: {', '.join(state.classification.text_search_terms)}\n"
                )
            if state.classification.semantic_queries:
                classification_context += (
                    f"- Semantic queries: {', '.join(state.classification.semantic_queries)}\n"
                )
            # Append to system message if present
            if messages and isinstance(messages[0], SystemMessage):
                messages[0] = SystemMessage(
                    content=messages[0].content + classification_context
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


def create_shape_node(state_holder: dict) -> Callable:
    """Create the context shaping node."""

    def shape_node(state: EnhancedQAState) -> dict:
        """Process retrieved contexts: dedupe, rerank, filter."""
        logger.info("Shaping context from %d retrieved receipts", len(state.retrieved_contexts))

        # Get retrieved receipts from state_holder
        retrieved_receipts = state_holder.get("retrieved_receipts", [])

        # Convert to RetrievedContext objects
        contexts: list[RetrievedContext] = []
        seen_keys: set[tuple] = set()

        for receipt in retrieved_receipts:
            key = (receipt.get("image_id"), receipt.get("receipt_id"))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Extract labels from formatted receipt
            labels_found = []
            formatted = receipt.get("formatted_receipt", "")
            for label in ["TAX", "SUBTOTAL", "GRAND_TOTAL", "PRODUCT_NAME", "LINE_TOTAL"]:
                if f"[{label}]" in formatted:
                    labels_found.append(label)

            contexts.append(RetrievedContext(
                image_id=receipt.get("image_id", ""),
                receipt_id=receipt.get("receipt_id", 0),
                text=formatted[:2000],  # Limit text length
                relevance_score=1.0,  # Default score
                labels_found=labels_found,
                amounts=receipt.get("amounts", []),
            ))

        # Filter low-confidence contexts (< 0.3)
        filtered_contexts = [
            ctx for ctx in contexts
            if ctx.relevance_score >= 0.3
        ]

        # Limit total tokens (~4000 chars as proxy)
        total_chars = 0
        limited_contexts = []
        for ctx in filtered_contexts:
            if total_chars + len(ctx.text) > 4000:
                break
            limited_contexts.append(ctx)
            total_chars += len(ctx.text)

        logger.info(
            "Shaped context: %d receipts, %d chars",
            len(limited_contexts),
            total_chars,
        )

        # Check if we need to retry (empty after shaping)
        if not limited_contexts and retrieved_receipts:
            logger.warning("Context empty after shaping, may need retry")

        return {
            "shaped_context": limited_contexts,
            "retrieved_contexts": contexts,  # Store full list too
            "current_phase": "synthesize",
        }

    return shape_node


# ==============================================================================
# Synthesize Node
# ==============================================================================


def create_synthesize_node(llm: Any, state_holder: dict) -> Callable:
    """Create the answer synthesis node."""

    def synthesize_node(state: EnhancedQAState) -> dict:
        """Generate final answer from shaped context."""
        logger.info("Synthesizing answer from %d contexts", len(state.shaped_context))

        # Build context string
        context_parts = []
        for i, ctx in enumerate(state.shaped_context):
            context_parts.append(
                f"Receipt {i + 1} (image_id={ctx.image_id}, receipt_id={ctx.receipt_id}):\n"
                f"{ctx.text}\n"
                f"Amounts: {ctx.amounts}\n"
            )

        context_str = "\n---\n".join(context_parts) if context_parts else "(No receipts found)"

        # Check for aggregated amount
        aggregated = state_holder.get("aggregated_amount")
        if aggregated:
            context_str += f"\n\nAggregated Total: ${aggregated.get('total', 0):.2f}"

        # Build synthesis prompt
        messages = [
            SystemMessage(content=SYNTHESIZE_SYSTEM_PROMPT),
            HumanMessage(
                content=f"Question: {state.question}\n\n"
                f"Retrieved Context:\n{context_str}\n\n"
                f"Generate a clear answer with evidence."
            ),
        ]

        response = llm.invoke(messages)
        answer_text = response.content if hasattr(response, "content") else str(response)

        # Extract amount from aggregation if available
        total_amount = None
        if aggregated:
            total_amount = aggregated.get("total")

        # Build evidence from shaped contexts
        evidence = []
        for ctx in state.shaped_context:
            for amt in ctx.amounts:
                evidence.append({
                    "image_id": ctx.image_id,
                    "receipt_id": ctx.receipt_id,
                    "item": amt.get("text", ""),
                    "amount": amt.get("amount"),
                })

        # Store in state_holder for extraction
        state_holder["answer"] = {
            "answer": answer_text,
            "total_amount": total_amount,
            "receipt_count": len(state.shaped_context),
            "evidence": evidence,
        }

        logger.info("Synthesized answer: %s", answer_text[:100])

        return {
            "final_answer": answer_text,
            "answer": answer_text,  # Backwards compatibility
            "total_amount": total_amount,
            "receipt_count": len(state.shaped_context),
            "evidence": evidence,
            "current_phase": "complete",
        }

    return synthesize_node


# ==============================================================================
# Routing Functions
# ==============================================================================


def route_after_agent(state: EnhancedQAState, state_holder: dict) -> str:
    """Route after agent node: tools, shape, or end."""
    # Check if answer was submitted via tool
    if state_holder.get("answer") is not None:
        logger.debug("Answer submitted via tool, going to end")
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
                # Check for specific routing tools
                for tc in last_message.tool_calls:
                    if tc.get("name") == "submit_answer":
                        return "tools"
                    if tc.get("name") == "signal_retrieval_complete":
                        return "tools"

                # Other tool calls - continue if under limit
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


def route_after_tools(state: EnhancedQAState, state_holder: dict) -> str:
    """Route after tools node."""
    # Check if answer was submitted
    if state_holder.get("answer") is not None:
        return "end"

    # Check if retrieval complete
    if state_holder.get("retrieval_complete"):
        return "shape"

    # Default: back to agent
    return "agent"


def route_after_shape(state: EnhancedQAState, state_holder: dict) -> str:
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
    use_enhanced: bool = False,
) -> tuple[Any, dict]:
    """
    Create the question-answering workflow.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Function to generate embeddings
        settings: Optional settings
        use_enhanced: Use 5-node enhanced graph (default: False for backwards compat)

    Returns:
        (compiled_graph, state_holder) - The graph and state dict
    """
    if settings is None:
        settings = get_settings()

    # Create tools with injected dependencies
    tools, state_holder = create_simplified_qa_tools(
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

    if use_enhanced:
        return _create_enhanced_graph(llm, tools, state_holder)
    else:
        return _create_simple_graph(llm, tools, state_holder)


def _create_simple_graph(
    llm: Any,
    tools: list,
    state_holder: dict,
) -> tuple[Any, dict]:
    """Create the simple 2-node graph (backwards compatible)."""
    llm_with_tools = llm.bind_tools(tools)

    def agent_node(state: QuestionAnsweringState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages

        max_retries = 3
        for attempt in range(max_retries):
            response = llm_with_tools.invoke(messages)

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

        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.debug(
                "Agent tool calls: %s",
                [tc["name"] for tc in response.tool_calls],
            )

        return {"messages": [response]}

    tool_node = ToolNode(tools)

    state_holder["iteration_count"] = 0

    def should_continue(state: QuestionAnsweringState) -> str:
        """Check if we should continue or end."""
        state_holder["iteration_count"] += 1
        logger.debug(
            "should_continue: iteration=%d, messages=%d",
            state_holder["iteration_count"],
            len(state.messages) if state.messages else 0,
        )

        if state_holder.get("answer") is not None:
            logger.debug("Answer already submitted, ending")
            return "end"

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
                    for tc in last_message.tool_calls:
                        if tc.get("name") == "submit_answer":
                            logger.debug("Found submit_answer in tool calls")
                            return "tools"
                    if state_holder["iteration_count"] < state_holder["max_iterations"]:
                        return "tools"
                    else:
                        logger.warning(
                            "Max iterations reached (%d), ending without answer",
                            state_holder["max_iterations"],
                        )
                        return "end"
                else:
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

    workflow = StateGraph(QuestionAnsweringState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )
    workflow.add_edge("tools", "agent")

    compiled = workflow.compile()
    return compiled, state_holder


def _create_enhanced_graph(
    llm: Any,
    tools: list,
    state_holder: dict,
) -> tuple[Any, dict]:
    """Create the enhanced 5-node ReAct RAG graph."""

    # Create nodes
    plan_node = create_plan_node(llm, state_holder)
    agent_node = create_agent_node(llm, tools, state_holder)
    tool_node = ToolNode(tools)
    shape_node = create_shape_node(state_holder)
    synthesize_node = create_synthesize_node(llm, state_holder)

    # Build graph
    workflow = StateGraph(EnhancedQAState)

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
    use_enhanced: bool = False,
    callbacks: Optional[list] = None,
) -> dict:
    """
    Run the question-answering workflow for a question.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        question: The question to answer
        use_enhanced: Whether using enhanced graph
        callbacks: Optional list of LangChain callbacks for cost tracking etc.

    Returns:
        Answer dict with answer, amount, count, and evidence
    """
    # Reset state
    state_holder["answer"] = None
    state_holder["iteration_count"] = 0
    state_holder["retrieval_complete"] = False
    state_holder["retrieved_receipts"] = []
    state_holder["searches"] = []

    # Create initial state
    if use_enhanced:
        from receipt_agent.agents.question_answering.tools_simplified import (
            QuestionContext,
        )
        state_holder["context"] = QuestionContext(question=question)

        initial_state = EnhancedQAState(
            question=question,
            messages=[
                SystemMessage(content=SIMPLIFIED_SYSTEM_PROMPT),
                HumanMessage(content=question),
            ],
        )
    else:
        from receipt_agent.agents.question_answering.tools_simplified import (
            QuestionContext,
        )
        state_holder["context"] = QuestionContext(question=question)

        initial_state = QuestionAnsweringState(
            question=question,
            messages=[
                SystemMessage(content=SIMPLIFIED_SYSTEM_PROMPT),
                HumanMessage(content=question),
            ],
        )

    logger.info("Answering question: %s", question[:100])

    try:
        # Get model name for LangSmith cost tracking metadata
        import os
        model_name = os.environ.get("OPENROUTER_MODEL") or os.environ.get(
            "RECEIPT_AGENT_OPENROUTER_MODEL", "google/gemini-2.5-flash"
        )

        # Parse provider from model name (e.g., "google/gemini-2.5-flash" -> "google")
        provider = model_name.split("/")[0] if "/" in model_name else "openrouter"

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
    use_enhanced: bool = False,
    callbacks: Optional[list] = None,
) -> dict:
    """Synchronous wrapper for answer_question."""
    return asyncio.run(
        answer_question(
            graph=graph,
            state_holder=state_holder,
            question=question,
            use_enhanced=use_enhanced,
            callbacks=callbacks,
        )
    )


# ==============================================================================
# Legacy Exports (backwards compatibility)
# ==============================================================================

# Re-export original SYSTEM_PROMPT for backwards compatibility
SYSTEM_PROMPT = SIMPLIFIED_SYSTEM_PROMPT
