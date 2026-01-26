"""
LangGraph workflow for answering questions about receipts.

Simple ReAct workflow with 3 nodes:
- agent: LLM decides to call tools or respond with answer
- tools: Execute tool calls
- synthesize: Format final answer with evidence (structured output)

Flow: START → agent ⟷ tools → synthesize → END

The agent loops calling tools until it responds without tool calls,
then synthesize formats the answer with supporting receipt details.
"""

import asyncio
import logging
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.agents.question_answering.state import (
    AnswerWithEvidence,
    QAState,
)
from receipt_agent.agents.question_answering.tools import (
    SYSTEM_PROMPT,
    create_qa_tools,
)
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)


# Prompt for the synthesize node
SYNTHESIZE_PROMPT = """Based on the conversation above, create a structured answer.

The agent has gathered information about receipts. Now format a clear response.

Instructions:
1. Extract the answer from the agent's final response
2. Include any total amounts mentioned
3. Count the receipts involved
4. Build evidence from the retrieved receipts (provided below)

Retrieved Receipts:
{receipts}

Format your response as:
- answer: Natural language answer to the question
- total_amount: Dollar amount if this was a "how much" question (null otherwise)
- receipt_count: Number of receipts supporting the answer
- evidence: List of supporting items [{{"image_id": "...", "receipt_id": N, "item": "...", "amount": N.NN}}, ...]
"""


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

    # Create tools
    tools, state_holder = create_qa_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    # Create LLM
    llm = create_llm(
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        temperature=0.0,
        timeout=120,
    )

    # LLM with tools bound for the agent node
    llm_with_tools = llm.bind_tools(tools)

    # LLM with structured output for synthesize node
    synthesize_llm = llm.with_structured_output(AnswerWithEvidence)

    # Initialize state holder
    state_holder["iteration_count"] = 0
    state_holder["max_iterations"] = 10

    def agent_node(state: QAState) -> dict:
        """Call the LLM to decide next action or provide answer."""
        messages = list(state.messages)

        # Add context about what's been searched (helps avoid redundant searches)
        searches = state_holder.get("searches", [])
        if searches:
            search_summary = "\n".join([
                f"- {s['type']} search for '{s['query']}': {s['result_count']} results"
                for s in searches[-10:]  # Last 10 searches
            ])
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

        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.debug(
                "Agent tool calls: %s",
                [tc["name"] for tc in response.tool_calls],
            )

        return {"messages": [response]}

    def synthesize_node(state: QAState) -> dict:
        """Format the final answer with structured output."""
        # Get retrieved receipts for evidence
        retrieved = state_holder.get("retrieved_receipts", [])

        # Format receipts for the prompt
        receipt_text = ""
        if retrieved:
            receipt_parts = []
            for r in retrieved[:10]:  # Limit to 10 receipts
                amounts = r.get("amounts", [])
                amount_str = ", ".join([
                    f"{a['label']}: ${a['amount']}"
                    for a in amounts[:5]  # Limit amounts shown
                ])
                receipt_parts.append(
                    f"- {r.get('merchant', 'Unknown')} "
                    f"(image_id={r.get('image_id')}, receipt_id={r.get('receipt_id')}): "
                    f"{amount_str or 'no amounts'}"
                )
            receipt_text = "\n".join(receipt_parts)
        else:
            receipt_text = "(No receipts retrieved)"

        # Build synthesize prompt
        synthesize_prompt = SYNTHESIZE_PROMPT.format(receipts=receipt_text)

        # Get conversation history plus synthesize instruction
        messages = list(state.messages) + [
            HumanMessage(content=synthesize_prompt)
        ]

        try:
            result = synthesize_llm.invoke(messages)

            # Store in state_holder for extraction
            state_holder["answer"] = {
                "answer": result.answer,
                "total_amount": result.total_amount,
                "receipt_count": result.receipt_count,
                "evidence": result.evidence,
            }

            logger.info("Synthesized answer: %s", result.answer[:100])

        except Exception as e:
            logger.error("Synthesize failed: %s", e)
            # Fallback: extract from last agent message
            last_content = ""
            for msg in reversed(state.messages):
                if isinstance(msg, AIMessage) and msg.content:
                    last_content = str(msg.content)
                    break

            state_holder["answer"] = {
                "answer": last_content or "Unable to generate answer.",
                "total_amount": None,
                "receipt_count": len(retrieved),
                "evidence": [],
            }

        return {}

    # Create tool node
    tool_node = ToolNode(tools)

    def should_continue(state: QAState) -> str:
        """Route after agent: tools, synthesize, or end."""
        state_holder["iteration_count"] = state_holder.get("iteration_count", 0) + 1

        # Check iteration limit
        if state_holder["iteration_count"] >= state_holder["max_iterations"]:
            logger.warning(
                "Max iterations reached (%d), going to synthesize",
                state_holder["max_iterations"],
            )
            return "synthesize"

        # Check last message
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    # Has tool calls - execute them
                    return "tools"
                else:
                    # No tool calls - agent is done, go to synthesize
                    logger.info("Agent done (no tool calls), going to synthesize")
                    return "synthesize"

        return "synthesize"

    # Build graph
    workflow = StateGraph(QAState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("synthesize", synthesize_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add edges
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "synthesize": "synthesize",
        },
    )
    workflow.add_edge("tools", "agent")
    workflow.add_edge("synthesize", END)

    compiled = workflow.compile()
    return compiled, state_holder


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
    state_holder["iteration_count"] = 0
    state_holder["searches"] = []
    state_holder["retrieved_receipts"] = []
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
            "RECEIPT_AGENT_OPENROUTER_MODEL", "google/gemini-2.5-flash"
        )
        provider = model_name.split("/")[0] if "/" in model_name else "openrouter"

        config = {
            "recursion_limit": 25,
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
            logger.warning("Agent ended without answer")
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
