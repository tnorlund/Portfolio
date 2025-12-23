"""
Label Validation Agent Workflow

A LangGraph agent that validates label suggestions for receipt words using
all available context (ChromaDB, DynamoDB, Google Places metadata).
"""

import logging
import os
from typing import Annotated, Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from receipt_dynamo.data._pulumi import load_secrets as load_pulumi_secrets

from receipt_agent.agents.label_validation.state import LabelValidationState
from receipt_agent.agents.label_validation.tools import (
    WordContext,
    create_label_validation_tools,
)
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.constants import CORE_LABELS

logger = logging.getLogger(__name__)


# ==============================================================================
# System Prompt
# ==============================================================================

LABEL_VALIDATION_PROMPT = """You are a label validation agent for receipt processing.

Your task is to validate whether a suggested label type is correct for a word on a receipt.

## Context

You are validating:
- **Word**: `{word_text}`
- **Suggested Label**: `{suggested_label_type}`
- **Merchant**: {merchant_name}
- **Original Reasoning**: {original_reasoning}

## Available Tools

- `search_similar_words`: Search for semantically similar words with their labels and full context (audit trail, surrounding text)
- `get_all_labels_for_word`: Get all labels for this word (audit trail, consolidation chain, reasoning)
- `get_labels_on_receipt`: Get all labels on the same receipt (for context)
- `submit_decision`: Submit your final validation decision (REQUIRED at the end)

**Note**: Word context and merchant metadata are provided above. Use tools for additional evidence if needed.

## CORE_LABELS Definitions

All valid label types and their definitions:

{core_labels_definitions}

## Decision Criteria (priority order)

1. **Word Context (PRIMARY)**: Where does the word appear? What surrounds it? Does context match the label definition?
   - **CRITICAL**: If word context clearly shows the label is correct, mark VALID even if similar words suggest otherwise
   - Example: If "STAND" appears in line "[THE STAND]" and merchant is "The Stand", it's VALID for MERCHANT_NAME
2. **Merchant Metadata (SECONDARY)**: Does word match merchant name/address? (Use Google Places data as supporting evidence)
3. **Similar Words (SUPPORTING)**: Do similar words have this label as VALID/INVALID? (Supporting evidence only)
   - **WARNING**: Don't let similar word examples override clear primary context
   - If context clearly supports the label, similar words are just additional confirmation
4. **Label History (CONTEXT)**: Has this word been labeled before? Any cross-label confusion patterns?

## Decision Making

- **VALID**: High confidence (>80%) - word clearly matches label type definition
  - **Be confident**: If word appears in the correct context (e.g., merchant name line for MERCHANT_NAME), mark VALID
  - **Partial tokens are OK**: If "STAND" is part of "THE STAND" merchant name and appears in merchant name context, it's VALID
- **INVALID**: High confidence (>80%) - word clearly does NOT match label type definition
- **NEEDS_REVIEW**: Low confidence (<80%) - ambiguous, needs human review
  - **Only use when**: Context is truly ambiguous or conflicting
  - **Don't use when**: Context clearly supports the label but you're being overly cautious

## Confidence Guidelines

- **High (0.8-1.0)**: Context clearly supports/contradicts the label
  - Use when: Word appears in correct context (e.g., merchant name line for MERCHANT_NAME)
  - Use when: Word matches merchant metadata and appears in appropriate location
- **Medium (0.5-0.8)**: Some supporting evidence, minor conflicts
- **Low (0.0-0.5)**: Ambiguous, conflicting signals → NEEDS_REVIEW
  - Only use when context is genuinely unclear

## Special Cases

### MERCHANT_NAME
- **VALID if**: Word appears in merchant name line/header AND (matches merchant metadata OR is part of merchant name)
- **Partial tokens**: If merchant is "The Stand" and word "STAND" appears in merchant name line, it's VALID
- **Don't be conservative**: If context clearly shows it's the merchant name, mark VALID with high confidence

### UNIT_PRICE vs LINE_TOTAL (disambiguation)
- Treat amounts as a pair with quantity context:
  - If two amounts appear on the same line: the trailing/right-aligned one is usually **LINE_TOTAL**; the inline/left one near product name or unit marker is **UNIT_PRICE**.
  - If a quantity exists and amount ≈ quantity × other_amount (within $0.02), the product is **LINE_TOTAL** and the factor is **UNIT_PRICE**.
  - Weight/unit markers (`/lb`, `per lb`, `ea`, `each`, `kg`, `oz`) strongly indicate **UNIT_PRICE** for the adjacent amount.
  - If only one amount is present and quantity == 1, default to **LINE_TOTAL** unless there is a unit marker immediately next to it.
  - If the same number appears twice, use position/alignment: the column-aligned/trailing instance is **LINE_TOTAL**; the inline instance is **UNIT_PRICE**.

## Important Rules

1. **Word context (provided above) is PRIMARY** - use it first and trust it when clear
2. **Similar words are SUPPORTING evidence only** - don't let them override clear primary context
3. **Be confident when context is clear** - don't default to NEEDS_REVIEW when evidence supports VALID/INVALID
4. **Partial merchant name tokens can be VALID** - if they appear in merchant name context
5. **ALWAYS end with `submit_decision`** - never end without calling it
6. **Call `submit_decision` exactly ONCE** - it can only be called once per validation
6. **Only use NEEDS_REVIEW for genuine ambiguity** - not when you're being overly cautious

Use the provided context and tools to make a confident decision."""


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_label_validation_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the label validation workflow graph.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Function to generate embeddings
        settings: Optional settings

    Returns:
        (compiled_graph, state_holder) - The graph and state dict to inject context
    """
    if settings is None:
        settings = get_settings()

    # Create tools with injected dependencies
    tools, state_holder = create_label_validation_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    # Store clients in state holder for use in run_label_validation
    state_holder["dynamo_client"] = dynamo_client
    state_holder["chroma_client"] = chroma_client
    state_holder["embed_fn"] = embed_fn

    # Initialize tool tracking
    state_holder["tools_used"] = []

    # Create LLM with tools bound
    # Try to get API key from Pulumi secrets if not in settings
    api_key = settings.ollama_api_key.get_secret_value()
    if not api_key:
        try:
            pulumi_secrets = load_pulumi_secrets("dev") or load_pulumi_secrets(
                "prod"
            )
            if pulumi_secrets:
                api_key = (
                    pulumi_secrets.get("portfolio:OLLAMA_API_KEY")
                    or pulumi_secrets.get("OLLAMA_API_KEY")
                    or pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_API_KEY")
                )
                if api_key:
                    logger.info("Loaded Ollama API key from Pulumi secrets")
        except Exception as e:
            logger.debug("Could not load Ollama API key from Pulumi: %s", e)

    if not api_key:
        logger.warning(
            "Ollama API key not set - LLM calls will fail. "
            "Set RECEIPT_AGENT_OLLAMA_API_KEY or configure in Pulumi secrets"
        )

    # For GPT-OSS models, use 'reasoning' parameter to control reasoning effort
    # According to Ollama docs: https://docs.ollama.com/capabilities/thinking
    # GPT-OSS requires 'reasoning' to be 'low', 'medium', or 'high'
    # LangChain ChatOllama supports 'reasoning' parameter (not 'think')
    # Default to 'medium' for balanced performance
    reasoning_level = (
        "medium" if "gpt-oss" in settings.ollama_model.lower() else None
    )

    llm_kwargs = {
        "base_url": settings.ollama_base_url,
        "model": settings.ollama_model,
        "client_kwargs": {
            "headers": (
                {"Authorization": f"Bearer {api_key}"} if api_key else {}
            ),
            "timeout": 120,
        },
        "temperature": 0.0,
    }

    # Add reasoning parameter for GPT-OSS models
    if reasoning_level:
        llm_kwargs["reasoning"] = reasoning_level

    llm = ChatOllama(**llm_kwargs).bind_tools(tools)

    # Define the agent node (calls LLM)
    def agent_node(state: LabelValidationState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages

        # Check message size to prevent 400 errors
        # Estimate token count (rough: 1 token ≈ 4 characters)
        total_chars = sum(
            len(str(msg.content))
            for msg in messages
            if hasattr(msg, "content")
        )
        estimated_tokens = total_chars // 4

        # Ollama Cloud typically has ~128k token context, but we'll be conservative
        # If messages are too long, truncate tool outputs (keep system prompt and recent messages)
        MAX_TOKENS = 80000  # Conservative limit (80k tokens)
        if estimated_tokens > MAX_TOKENS:
            logger.warning(
                "Message size too large (%s estimated tokens, limit: %s). "
                "Truncating tool outputs to prevent 400 error.",
                estimated_tokens,
                MAX_TOKENS,
            )
            # Keep system message (first) and last 2 messages (human + last tool output)
            # This preserves the prompt and most recent context
            original_count = len(messages)
            if original_count > 3:
                truncated_messages = [messages[0]] + messages[-2:]
                messages = truncated_messages
                logger.info(
                    "Truncated messages from %s to %s",
                    original_count,
                    len(messages),
                )

        try:
            response = llm.invoke(messages)
        except Exception as e:
            error_str = str(e)
            error_type = type(e).__name__

            # Check for 400 Bad Request errors
            is_bad_request = (
                "400" in error_str
                or "Bad Request" in error_str
                or error_type == "ResponseError"
                and "400" in error_str
            )

            if is_bad_request:
                logger.error(
                    "Bad Request (400) error from Ollama. "
                    "Message size: ~%s tokens, Message count: %s. Error: %s",
                    estimated_tokens,
                    len(messages),
                    error_str[:500],
                )
                # For 400 errors, we can't retry (it's a malformed request)
                # Return a default NEEDS_REVIEW decision
                error_message = AIMessage(
                    content=(
                        "I encountered an error processing this request. "
                        "The context may be too large or malformed. "
                        "Marking as NEEDS_REVIEW for manual review."
                    )
                )
                return {"messages": [error_message]}

            # For other errors, re-raise to let LangGraph handle retries
            logger.error(
                "LLM invocation error: %s: %s",
                error_type,
                error_str[:500],
            )
            raise

        # Log and track tool calls
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_names = [
                tc.get("name", "unknown") for tc in response.tool_calls
            ]
            logger.info("Agent tool calls: %s", tool_names)
            # Track unique tools used
            for tool_name in tool_names:
                if tool_name not in state_holder.get("tools_used", []):
                    state_holder.setdefault("tools_used", []).append(tool_name)

        # Only return new message - add_messages reducer handles accumulation
        return {"messages": [response]}

    # Define tool node
    tool_node = ToolNode(tools)

    # Define routing function
    def should_continue(state: LabelValidationState) -> str:
        """Check if we should continue or end."""
        # BEST PRACTICE: Check decision FIRST (before checking tool calls)
        # This ensures we stop immediately after submit_decision is called
        if state_holder.get("decision") is not None:
            logger.debug("Decision already submitted, ending workflow")
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    # Check if submit_decision is in the tool calls
                    tool_names = [
                        tc.get("name")
                        for tc in (last_message.tool_calls or [])
                    ]
                    if "submit_decision" in tool_names:
                        # submit_decision was called - will be processed by tool node
                        # After tool node, should_continue will see decision and end
                        return "tools"
                    return "tools"

        # No tool calls and no decision - end (shouldn't happen with good prompts)
        logger.warning(
            "No decision submitted and no tool calls - ending workflow"
        )
        return "end"

    # Build the graph
    workflow = StateGraph(LabelValidationState)

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
# Runner
# ==============================================================================


async def run_label_validation(
    graph: Any,
    state_holder: dict,
    word_text: str,
    suggested_label_type: str,
    merchant_name: Optional[str],
    original_reasoning: str,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
) -> dict:
    """
    Run the label validation workflow for a word.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict (to inject context)
        word_text: Word text being validated
        suggested_label_type: Suggested label type
        merchant_name: Merchant name
        original_reasoning: Original reasoning from suggestion LLM
        image_id: Image ID
        receipt_id: Receipt ID
        line_id: Line ID
        word_id: Word ID

    Returns:
        Validation decision dict
    """
    # Set up context for this validation
    state_holder["context"] = WordContext(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        word_text=word_text,
        suggested_label_type=suggested_label_type,
        merchant_name=merchant_name,
        original_reasoning=original_reasoning,
    )
    state_holder["decision"] = None
    state_holder["tools_used"] = []  # Reset for this run

    # Fetch word context and merchant metadata upfront (always needed)
    # This avoids redundant tool calls and gives agent immediate context
    dynamo_client = state_holder.get("dynamo_client")
    word_context_data = {}
    merchant_metadata_data = {}

    if dynamo_client:
        try:
            # Get word context (always needed)
            line = dynamo_client.get_receipt_line(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=line_id,
            )
            line_text = line.text if line else None

            # Get surrounding words (3 before, 3 after)
            surrounding_words = None
            if line:
                words_in_line = dynamo_client.list_receipt_words_from_line(
                    receipt_id=receipt_id,
                    image_id=image_id,
                    line_id=line_id,
                )
                if words_in_line:
                    words_in_line.sort(key=lambda w: w.word_id)
                    word_texts = [w.text for w in words_in_line]
                    try:
                        word_index = next(
                            i
                            for i, w in enumerate(words_in_line)
                            if w.word_id == word_id
                        )
                        start_idx = max(0, word_index - 3)
                        end_idx = min(len(word_texts), word_index + 4)
                        surrounding_words_list = word_texts[start_idx:end_idx]
                        # Mark the target word
                        target_idx = word_index - start_idx
                        if surrounding_words_list:
                            surrounding_words_list[target_idx] = (
                                f"[{surrounding_words_list[target_idx]}]"
                            )
                        surrounding_words = " ".join(surrounding_words_list)
                    except StopIteration:
                        surrounding_words = " ".join(word_texts)

            # Get surrounding lines (all lines from receipt, formatted)
            # Mark the specific word instance within the line using word_id (not regex)
            surrounding_lines = None
            all_receipt_lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id=image_id,
                receipt_id=receipt_id,
            )
            if all_receipt_lines and line:
                # Get words in the SPECIFIC line (line_id) to mark the specific word instance by word_id
                # Note: Each line has multiple words, and the same word text can appear on different lines
                # with different word_ids. We use line_id to identify the line and word_id to identify
                # the specific word within that line.
                words_in_target_line = None
                target_word_obj = None
                target_word_occurrence = None
                if line_id:
                    # Get words ONLY from the target line (line_id)
                    words_in_target_line = dynamo_client.list_receipt_words_from_line(
                        receipt_id=receipt_id,
                        image_id=image_id,
                        line_id=line_id,  # Specific line_id identifies which line
                    )
                    if words_in_target_line:
                        words_in_target_line.sort(key=lambda w: w.word_id)
                        try:
                            # Find the specific word by word_id within this line
                            target_word_index = next(
                                i
                                for i, w in enumerate(words_in_target_line)
                                if w.word_id
                                == word_id  # Specific word_id identifies which word on this line
                            )
                            target_word_obj = words_in_target_line[
                                target_word_index
                            ]
                            # Count how many times this word text appears before our target word ON THIS LINE
                            # This ensures we mark the correct instance if the word appears multiple times on the same line
                            target_word_occurrence = sum(
                                1
                                for w in words_in_target_line[
                                    :target_word_index
                                ]
                                if w.text == target_word_obj.text
                            )
                        except StopIteration:
                            target_word_obj = None

                sorted_lines = sorted(
                    all_receipt_lines, key=lambda l: l.calculate_centroid()[1]
                )
                formatted_lines = []
                for i, receipt_line in enumerate(sorted_lines):
                    line_text = receipt_line.text

                    # If this is the target line (line_id matches), mark the specific word by word_id
                    # This ensures we only mark the word on the correct line, even if the same word
                    # appears on other lines with different word_ids
                    if receipt_line.line_id == line_id and target_word_obj:
                        # Find and replace the Nth occurrence of this word text (where N = target_word_occurrence + 1)
                        word_text = target_word_obj.text
                        occurrence_count = 0
                        word_start = 0
                        while True:
                            word_start = line_text.find(word_text, word_start)
                            if word_start == -1:
                                break
                            # Check if it's a whole word (not part of another word)
                            if (
                                word_start == 0
                                or not line_text[word_start - 1].isalnum()
                            ) and (
                                word_start + len(word_text) >= len(line_text)
                                or not line_text[
                                    word_start + len(word_text)
                                ].isalnum()
                            ):
                                if occurrence_count == target_word_occurrence:
                                    # This is our target word instance - mark it
                                    line_text = (
                                        line_text[:word_start]
                                        + f"[{word_text}]"
                                        + line_text[
                                            word_start + len(word_text) :
                                        ]
                                    )
                                    break
                                occurrence_count += 1
                            word_start += 1

                    if i > 0:
                        prev_line = sorted_lines[i - 1]
                        curr_centroid = receipt_line.calculate_centroid()
                        if (
                            prev_line.bottom_left["y"]
                            < curr_centroid[1]
                            < prev_line.top_left["y"]
                        ):
                            formatted_lines[-1] += f" {line_text}"
                            continue
                    formatted_lines.append(line_text)
                surrounding_lines = formatted_lines

            # Get receipt place data (includes merchant metadata from Google Places)
            receipt_place = None
            merchant_metadata_data = {}
            try:
                place = dynamo_client.get_receipt_place(
                    receipt_id=receipt_id,
                    image_id=image_id,
                )
                if place:
                    receipt_place = {
                        "merchant_name": place.merchant_name,
                        "place_id": place.place_id,
                        "formatted_address": place.formatted_address,
                    }
                    # Merchant metadata from Google Places
                    merchant_metadata_data = {
                        "merchant_name": place.merchant_name,
                        "formatted_address": place.formatted_address,
                        "place_id": place.place_id,
                        "phone_number": getattr(place, "phone_number", None),
                        "website": getattr(place, "website", None),
                    }
            except Exception:
                pass

            word_context_data = {
                "word_text": word_text,
                "line_text": line_text,
                "surrounding_words": surrounding_words,
                "surrounding_lines": surrounding_lines,
                "receipt_place": receipt_place,
            }
        except Exception as e:
            logger.warning("Could not fetch initial context: %s", e)

    # Format word context for prompt
    word_context_text = ""
    if word_context_data:
        word_context_text = "\n### Word Context (Already Fetched)\n\n"
        if word_context_data.get("line_text"):
            word_context_text += (
                f"- **Line text**: `{word_context_data['line_text']}`\n"
            )
        if word_context_data.get("surrounding_words"):
            word_context_text += f"- **Surrounding words**: `{word_context_data['surrounding_words']}`\n"
        if word_context_data.get("surrounding_lines"):
            word_context_text += f"- **Full receipt context** (target line marked with brackets):\n  ```\n"
            word_context_text += "\n  ".join(
                word_context_data["surrounding_lines"]
            )
            word_context_text += "\n  ```\n"
        if word_context_data.get("receipt_place"):
            meta = word_context_data["receipt_place"]
            word_context_text += f"- **Receipt place**: merchant={meta.get('merchant_name')}, place_id={meta.get('place_id')}\n"

    # Format merchant metadata for prompt
    merchant_metadata_text = ""
    if merchant_metadata_data:
        merchant_metadata_text = (
            "\n### Merchant Metadata (Already Fetched)\n\n"
        )
        if merchant_metadata_data.get("merchant_name"):
            merchant_metadata_text += f"- **Merchant name**: {merchant_metadata_data['merchant_name']}\n"
        if merchant_metadata_data.get("formatted_address"):
            merchant_metadata_text += f"- **Address**: {merchant_metadata_data['formatted_address']}\n"
        if merchant_metadata_data.get("phone_number"):
            merchant_metadata_text += (
                f"- **Phone**: {merchant_metadata_data['phone_number']}\n"
            )
        if merchant_metadata_data.get("place_id"):
            merchant_metadata_text += (
                f"- **Place ID**: {merchant_metadata_data['place_id']}\n"
            )

    # Build CORE_LABELS definitions text
    core_labels_text = "\n".join(
        f"- **{label}**: {definition}"
        for label, definition in CORE_LABELS.items()
    )

    # Highlight the specific label being validated
    suggested_label_definition = CORE_LABELS.get(
        suggested_label_type, "Unknown label type"
    )
    core_labels_section = f"""**Validating**: `{suggested_label_type}` - {suggested_label_definition}

All valid label types:

{core_labels_text}
"""

    # Format system prompt with context
    system_prompt = LABEL_VALIDATION_PROMPT.format(
        word_text=word_text,
        suggested_label_type=suggested_label_type,
        merchant_name=merchant_name or "Unknown",
        original_reasoning=original_reasoning or "No reasoning provided",
        core_labels_definitions=core_labels_section,
    )

    # Add fetched context to system prompt
    if word_context_text or merchant_metadata_text:
        system_prompt += "\n\n" + word_context_text + merchant_metadata_text

    # Create initial state
    initial_state = LabelValidationState(
        word_text=word_text,
        suggested_label_type=suggested_label_type,
        merchant_name=merchant_name,
        original_reasoning=original_reasoning,
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        messages=[
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=f"Please validate whether '{word_text}' should have label '{suggested_label_type}'. "
                f"Use the provided context and tools to make a confident decision."
            ),
        ],
    )

    logger.info(
        "Starting label validation for '%s' -> %s (%s#%s, line %s, word %s)",
        word_text,
        suggested_label_type,
        image_id,
        receipt_id,
        line_id,
        word_id,
    )

    try:
        config = {
            "recursion_limit": 50,
            "configurable": {
                "thread_id": f"{image_id}#{receipt_id}#{line_id}#{word_id}"
            },
        }

        # Add LangSmith metadata if tracing is enabled
        if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
            config["metadata"] = {
                "word_text": word_text,
                "suggested_label_type": suggested_label_type,
                "merchant": merchant_name,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "workflow": "label_validation",
            }

        final_state = await graph.ainvoke(initial_state, config=config)

        # Get decision and tools used from state holder
        decision = state_holder.get("decision")
        tools_used = state_holder.get("tools_used", [])

        if decision:
            # Add tools used to decision
            decision["tools_used"] = tools_used

            # Add conversation messages for debugging (optional - can be large)
            # Only include if we want to see the full conversation
            if os.environ.get("LABEL_VALIDATION_DEBUG") == "true":
                decision["conversation"] = [
                    {
                        "type": msg.__class__.__name__,
                        "content": (
                            msg.content
                            if hasattr(msg, "content")
                            else str(msg)
                        ),
                        "tool_calls": (
                            [tc.get("name") for tc in (msg.tool_calls or [])]
                            if hasattr(msg, "tool_calls") and msg.tool_calls
                            else None
                        ),
                    }
                    for msg in final_state.messages
                ]

            logger.info(
                "Validation complete: %s (confidence=%.2f%%, tools=%s)",
                decision["decision"],
                decision["confidence"] * 100.0,
                tools_used,
            )
            return decision
        else:
            # Agent ended without submitting decision
            logger.warning(
                "Agent ended without submitting decision for '%s' -> %s",
                word_text,
                suggested_label_type,
            )
            return {
                "decision": "NEEDS_REVIEW",
                "confidence": 0.0,
                "reasoning": "Agent did not submit a decision",
                "evidence": [],
            }

    except Exception as e:
        logger.error("Error in label validation: %s", e)
        return {
            "decision": "NEEDS_REVIEW",
            "confidence": 0.0,
            "reasoning": f"Error during validation: {str(e)}",
            "evidence": [],
        }
