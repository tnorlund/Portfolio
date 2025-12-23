"""
Financial Validation Sub-Agent Graph

Graph creation and execution functions for the financial validation sub-agent.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.subagents.financial_validation.state import (
    FinancialValidationState,
)
from receipt_agent.utils.agent_common import (
    create_agent_node_with_retry,
    create_ollama_llm,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


# ================================================================
# System Prompt
# ================================================================

FINANCIAL_VALIDATION_PROMPT = """\
You are a financial validation agent for receipts.
Your job is to validate that all financial labels are consistent and correct.

## Your Task

You're given a receipt with text and labels. Your job is to:
1. Detect currency from receipt text
2. Extract financial values (grand total, subtotal, tax, line items)
3. Validate financial math:
   - Grand Total ≈ Subtotal + Tax (with tolerance for rounding)
   - Subtotal ≈ Sum of all LINE_TOTAL values
   - Line Items: QUANTITY × UNIT_PRICE ≈ LINE_TOTAL
4. Identify which labels are incorrect or missing
5. Propose corrections with reasoning

## Available Tools

- `get_financial_labels`: Get all financial labels
  (GRAND_TOTAL, SUBTOTAL, TAX, LINE_TOTAL, etc.)
- `detect_currency`: Detect currency from receipt text
- `extract_amounts`: Extract numeric amounts from labels
- `validate_math`: Validate that financial math checks out
- `propose_corrections`: Propose label corrections

## Validation Rules

- Grand Total = Subtotal + Tax (tolerance: 0.01 for rounding)
- Subtotal = Sum of all LINE_TOTAL values (tolerance: 0.01)
- Line Items: QUANTITY × UNIT_PRICE ≈ LINE_TOTAL (tolerance: 0.01)
- All amounts must use same currency
- Tax should be non-negative
- No duplicate charges

## Output Format

When you find issues, propose corrections with:
- Which label is wrong (line_id, word_id, current_label)
- What it should be (correct_label)
- Reasoning (why it's wrong, what the correct value should be)
- Confidence (0.0 to 1.0)

Begin by detecting currency and extracting financial labels."""


# ================================================================
# Tool Factory
# ================================================================


def create_financial_validation_tools(
    receipt_data: dict,
) -> tuple[list[Any], dict]:
    """Create tools for financial validation sub-agent."""
    state = {"receipt": receipt_data}

    @tool
    def get_financial_labels() -> dict:
        """Get all financial labels (GRAND_TOTAL, SUBTOTAL, TAX,
        LINE_TOTAL, etc.)."""
        receipt = state["receipt"]
        labels = receipt.get("labels", [])
        words = receipt.get("words", [])

        financial_labels = [
            "GRAND_TOTAL",
            "SUBTOTAL",
            "TAX",
            "LINE_TOTAL",
            "DISCOUNT",
            "COUPON",
        ]

        word_lookup = {(w.get("line_id"), w.get("word_id")): w for w in words}

        result = {}
        for label_type in financial_labels:
            matching = [
                {
                    "line_id": l.get("line_id"),
                    "word_id": l.get("word_id"),
                    "text": word_lookup.get(
                        (l.get("line_id"), l.get("word_id")), {}
                    ).get("text", ""),
                    "validation_status": l.get("validation_status"),
                }
                for l in labels
                if l.get("label") == label_type
            ]
            if matching:
                result[label_type] = matching

        return {"financial_labels": result}

    @tool
    def detect_currency() -> dict:
        """Detect currency from receipt text."""
        receipt = state["receipt"]
        receipt_text = receipt.get("receipt_text", "")

        currency_symbols = {
            "$": "USD",
            "€": "EUR",
            "£": "GBP",
            "¥": "JPY",
            "₹": "INR",
        }

        currency_keywords = {
            "USD": "USD",
            "US Dollar": "USD",
            "EUR": "EUR",
            "Euro": "EUR",
            "GBP": "GBP",
            "Pound": "GBP",
        }

        evidence = []
        detected_currency = None

        for symbol, code in currency_symbols.items():
            if symbol in receipt_text:
                evidence.append(f"Found symbol: {symbol}")
                detected_currency = code
                break

        if not detected_currency:
            for keyword, code in currency_keywords.items():
                if keyword.lower() in receipt_text.lower():
                    evidence.append(f"Found keyword: {keyword}")
                    detected_currency = code
                    break

        if not detected_currency and "$" in receipt_text:
            detected_currency = "USD"
            evidence.append("Defaulting to USD (dollar sign found)")

        return {
            "currency": detected_currency or "USD",
            "confidence": 0.9 if evidence else 0.5,
            "evidence": evidence,
        }

    @tool
    def extract_amounts() -> dict:
        """Extract numeric amounts from financial labels."""
        receipt = state["receipt"]
        labels = receipt.get("labels", [])
        words = receipt.get("words", [])

        word_lookup = {(w.get("line_id"), w.get("word_id")): w for w in words}

        def extract_value(label_type: str) -> Optional[float]:
            matching_labels = [
                l for l in labels if l.get("label") == label_type
            ]
            if not matching_labels:
                return None

            label = matching_labels[0]
            key = (label.get("line_id"), label.get("word_id"))
            word = word_lookup.get(key, {})
            text = word.get("text", "")

            # Extract number
            text_clean = re.sub(r"[^\d.,-]", "", text)
            text_clean = text_clean.replace(",", "")
            try:
                return float(text_clean)
            except ValueError:
                return None

        grand_total = extract_value("GRAND_TOTAL")
        subtotal = extract_value("SUBTOTAL")
        tax = extract_value("TAX")

        # Sum line totals
        line_totals = []
        for label in labels:
            if label.get("label") == "LINE_TOTAL":
                key = (label.get("line_id"), label.get("word_id"))
                word = word_lookup.get(key, {})
                text = word.get("text", "")
                text_clean = re.sub(r"[^\d.,-]", "", text)
                text_clean = text_clean.replace(",", "")
                try:
                    line_totals.append(float(text_clean))
                except ValueError:
                    pass

        return {
            "grand_total": grand_total,
            "subtotal": subtotal,
            "tax": tax,
            "line_totals": line_totals,
            "sum_line_totals": sum(line_totals) if line_totals else None,
        }

    @tool
    def validate_math() -> dict:
        """Validate that financial math checks out."""
        amounts = extract_amounts()

        issues = []
        is_valid = True

        grand_total = amounts.get("grand_total")
        subtotal = amounts.get("subtotal")
        tax = amounts.get("tax")
        sum_line_totals = amounts.get("sum_line_totals")

        # Check grand total
        if (
            grand_total is not None
            and subtotal is not None
            and tax is not None
        ):
            calculated = subtotal + tax
            tolerance = 0.01
            if abs(grand_total - calculated) > tolerance:
                issues.append(
                    {
                        "type": "grand_total_mismatch",
                        "message": (
                            f"Grand total ({grand_total}) doesn't match "
                            f"subtotal ({subtotal}) + tax ({tax}) = "
                            f"{calculated}"
                        ),
                        "grand_total": grand_total,
                        "calculated": calculated,
                        "difference": grand_total - calculated,
                    }
                )
                is_valid = False

        # Check subtotal
        if subtotal is not None and sum_line_totals is not None:
            tolerance = 0.01
            if abs(subtotal - sum_line_totals) > tolerance:
                issues.append(
                    {
                        "type": "subtotal_mismatch",
                        "message": (
                            f"Subtotal ({subtotal}) doesn't match sum of "
                            f"line totals ({sum_line_totals})"
                        ),
                        "subtotal": subtotal,
                        "sum_line_totals": sum_line_totals,
                        "difference": subtotal - sum_line_totals,
                    }
                )
                is_valid = False

        return {
            "is_valid": is_valid,
            "issues": issues,
            "amounts": amounts,
        }

    @tool
    def propose_corrections(
        corrections: list[dict],
        currency: Optional[str] = None,
    ) -> dict:
        """
        Propose label corrections.

        Args:
            corrections: List of corrections, each with:
                - line_id: Line ID
                - word_id: Word ID
                - current_label: Current label type
                - correct_label: Correct label type
                - reasoning: Why correction is needed
                - confidence: Confidence score (0.0 to 1.0)
            currency: Detected currency

        Returns:
            Success status
        """
        state["result"] = {
            "corrections": corrections,
            "currency": currency,
        }
        return {
            "success": True,
            "corrections_count": len(corrections),
            "currency": currency,
        }

    tools = [
        get_financial_labels,
        detect_currency,
        extract_amounts,
        validate_math,
        propose_corrections,
    ]

    return tools, state


# ================================================================
# Graph Creation
# ================================================================


def create_financial_validation_graph(
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """Create the financial validation sub-agent graph."""
    if settings is None:
        settings = get_settings()

    llm = create_ollama_llm(settings)

    state_holder = {"receipt": {}}

    tools, _ = create_financial_validation_tools(state_holder["receipt"])

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # Create agent node with retry logic
    agent_node = create_agent_node_with_retry(
        llm=llm_with_tools,
        agent_name="financial-validation",
    )

    # Create tool node
    tool_node = ToolNode(tools)

    workflow = StateGraph(FinancialValidationState)

    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    workflow.set_entry_point("agent")

    def should_continue(state: FinancialValidationState) -> str:
        if not state.messages:
            return END
        last_message = state.messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")

    graph = workflow.compile()

    return graph, state_holder


# ================================================================
# Run Sub-Agent
# ================================================================


async def run_financial_validation(
    graph: Any,
    state_holder: dict,
    receipt_text: str,
    labels: list[dict],
    words: list[dict],
) -> dict:
    """Run financial validation sub-agent."""
    # Update state
    state_holder["receipt"] = {
        "receipt_text": receipt_text,
        "labels": labels,
        "words": words,
    }

    # Recreate tools with updated state
    tools, _ = create_financial_validation_tools(state_holder["receipt"])

    # Create initial state with system prompt
    initial_state = FinancialValidationState(
        receipt_text=receipt_text,
        labels=labels,
        words=words,
        messages=[
            SystemMessage(content=FINANCIAL_VALIDATION_PROMPT),
            HumanMessage(
                content=(
                    "Please validate financial consistency for this receipt. "
                    "Start by detecting currency and "
                    "extracting financial labels."
                )
            ),
        ],
    )

    # Run agent
    try:
        await graph.ainvoke(initial_state)
    except Exception as e:
        logger.exception("Financial validation failed: %s", e)
        return {
            "currency": None,
            "is_valid": False,
            "issues": [{"type": "error", "message": str(e)}],
            "corrections": [],
        }

    # Extract result
    result_data = state_holder.get("result", {})
    return {
        "currency": result_data.get("currency"),
        "is_valid": len(result_data.get("corrections", [])) == 0,
        "issues": [],
        "corrections": result_data.get("corrections", []),
    }
