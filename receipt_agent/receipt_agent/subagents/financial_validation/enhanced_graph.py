"""
Enhanced Financial Validation Sub-Agent

This enhanced version leverages table structure information
from the table sub-agent to perform comprehensive financial validation
including:
- Line-item field validation (QUANTITY × UNIT_PRICE = LINE_TOTAL)
- Grand total math proof (SUBTOTAL + TAX = GRAND_TOTAL)
- Missing field detection for line items
- Currency consistency validation

Uses the latest CORE_LABELS definitions for validation.
"""

import asyncio
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
from receipt_agent.subagents.financial_validation.utils import extract_number
from receipt_agent.utils.agent_common import (
    create_agent_node_with_retry,
    create_ollama_llm,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)

# Updated CORE_LABELS from the user's specification
CORE_LABELS = {
    # Merchant & Store Info (6 labels)
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": ("Telephone number printed on the receipt " "(store's main line)."),
    "WEBSITE": ("Web or email address printed on the receipt " "(e.g., sprouts.com)."),
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    # Location / Address (1 label)
    "ADDRESS_LINE": ("Full address line (street + city etc.) printed on the receipt."),
    # Transaction Info (5 labels)
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": ("Payment instrument summary (e.g., VISA ••••1234, CASH)."),
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": ("Any non-coupon discount line item " "(e.g., 10% member discount)."),
    # Line-Item Fields (4 labels) - NOTE: LINE_TOTAL added here
    "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
    "QUANTITY": "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
    "UNIT_PRICE": "Price per single unit / weight before tax.",
    "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
    # Totals & Taxes (3 labels)
    "SUBTOTAL": "Sum of all line totals before tax and discounts.",
    "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
}

# Financial label types for validation
FINANCIAL_LABELS = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "QUANTITY",
    "DISCOUNT",
    "COUPON",
}

LINE_ITEM_LABELS = {"PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"}

ENHANCED_FINANCIAL_VALIDATION_PROMPT = """\
You are an enhanced financial validation agent for receipts.

Your job is to prove that the financial math on a receipt is correct by:

## Primary Validations

1. **Grand Total Math**: Prove GRAND_TOTAL = SUBTOTAL + TAX + fees - discounts
   (±0.01 tolerance)
2. **Subtotal Math**: Prove SUBTOTAL = sum of all LINE_TOTAL values
   (±0.01 tolerance)
3. **Line Item Math**: For each line item, prove QUANTITY × UNIT_PRICE
   = LINE_TOTAL
   (±0.01 tolerance)
4. **Line Item Completeness**: Each LINE_TOTAL must have related PRODUCT_NAME,
   and ideally QUANTITY + UNIT_PRICE

## Table Structure Integration

You have access to table structure information that shows:
- Which words are in the same row (line items)
- Which words are in the same column (e.g., all prices, all quantities)
- The overall financial section boundaries

Use this structure to:
- Group line-item fields that belong together
- Identify missing required fields for line items
- Validate that financial fields appear in expected column positions

## Available Tools

- `get_table_structure`: Get table/column analysis from table sub-agent
  (if available)
- `get_financial_labels`: Get all financial labels by type
- `detect_currency`: Detect currency from receipt text
- `validate_grand_total_math`: Check GRAND_TOTAL = SUBTOTAL + TAX
  math
- `validate_subtotal_math`: Check SUBTOTAL = sum(LINE_TOTAL)
  math
- `validate_line_item_math`: Check each QUANTITY × UNIT_PRICE
  = LINE_TOTAL
- `find_missing_line_item_fields`: Find LINE_TOTALs missing PRODUCT_NAME,
  QUANTITY, or UNIT_PRICE
- `propose_corrections`: Submit label corrections with detailed reasoning

## Validation Process

1. Start by getting table structure (if available) and financial labels
2. Detect currency and validate consistency
3. Validate grand total math: GRAND_TOTAL = SUBTOTAL + TAX + fees -
   discounts
4. Validate subtotal math: SUBTOTAL = sum of all LINE_TOTAL values
5. For each line item, validate: QUANTITY × UNIT_PRICE = LINE_TOTAL
6. Check that each LINE_TOTAL has complete line-item information
7. Propose corrections for any inconsistencies found

## Output Requirements

For each issue found, propose corrections with:
- `line_id`, `word_id`: Location of the incorrect label
- `current_label`: What label it currently has (or "UNLABELED")
- `correct_label`: What label it should have
- `reasoning`: Detailed explanation of why this correction is needed
- `confidence`: 0.0-1.0 confidence score

Be thorough and precise. Financial validation is critical for receipt accuracy.

Begin by getting table structure and financial labels."""


def create_enhanced_financial_validation_tools(
    receipt_data: dict,
    table_structure: Optional[dict] = None,
    shared_state: Optional[dict] = None,
) -> tuple[list[Any], dict]:
    """Create tools for enhanced financial validation sub-agent."""
    if shared_state is not None:
        # Use the shared state dict so tools can access updated data
        state = shared_state
        # Ensure receipt data is in shared state
        state["receipt"] = receipt_data
        state["table_structure"] = table_structure
    else:
        # Create local state for standalone usage
        state = {
            "receipt": receipt_data,
            "table_structure": table_structure,
            "currency": None,
            "corrections": [],
        }

    @tool
    def get_table_structure() -> dict:
        """Get table structure analysis from table sub-agent (if available)."""
        structure = state.get("table_structure")
        if structure:
            return {
                "has_structure": True,
                "table_analysis": structure,
                "financial_rows": structure.get("rows", []),
                "columns": structure.get("columns", []),
            }
        else:
            return {
                "has_structure": False,
                "message": (
                    "No table structure available - proceeding with "
                    "label-based analysis"
                ),
            }

    @tool
    def get_financial_labels() -> dict:
        """Get all financial labels organized by type."""
        receipt = state["receipt"]
        labels = receipt.get("labels", [])
        words = receipt.get("words", [])

        # Create word lookup
        word_lookup = {(w.get("line_id"), w.get("word_id")): w for w in words}

        # Group labels by type
        financial_groups = {}
        for label_type in FINANCIAL_LABELS:
            matching = []
            for label in labels:
                if label.get("label") == label_type:
                    key = (label.get("line_id"), label.get("word_id"))
                    word = word_lookup.get(key, {})
                    text = word.get("text", "")
                    value = extract_number(text)

                    matching.append(
                        {
                            "line_id": label.get("line_id"),
                            "word_id": label.get("word_id"),
                            "text": text,
                            "numeric_value": value,
                            "validation_status": label.get("validation_status"),
                        }
                    )

            if matching:
                financial_groups[label_type] = matching

        return {
            "financial_labels": financial_groups,
            "label_counts": {k: len(v) for k, v in financial_groups.items()},
        }

    @tool
    def detect_currency() -> dict:
        """Detect currency from receipt text and validate consistency."""
        receipt = state["receipt"]
        receipt_text = receipt.get("receipt_text", "")

        currency_patterns = {
            r"[$]": "USD",
            r"[€]": "EUR",
            r"[£]": "GBP",
            r"[¥]": "JPY",
            r"[₹]": "INR",
        }

        detected = None
        evidence = []

        for pattern, currency in currency_patterns.items():
            if re.search(pattern, receipt_text):
                detected = currency
                evidence.append(f"Found {currency} symbol in text")
                break

        if not detected:
            # Check for currency words
            currency_words = {
                r"\bUSD\b": "USD",
                r"\bDollars?\b": "USD",
                r"\bEUR\b": "EUR",
                r"\bEuros?\b": "EUR",
            }

            for pattern, currency in currency_words.items():
                if re.search(pattern, receipt_text, re.IGNORECASE):
                    detected = currency
                    evidence.append(f"Found {currency} keyword in text")
                    break

        if not detected:
            detected = "USD"  # Default assumption
            evidence.append("Defaulting to USD (no clear currency indicators)")

        state["currency"] = detected

        return {
            "currency": detected,
            "evidence": evidence,
            "confidence": (
                0.9 if len(evidence) > 0 and "Defaulting" not in evidence[0] else 0.5
            ),
        }

    @tool
    def validate_grand_total_math() -> dict:
        """Validate that GRAND_TOTAL = SUBTOTAL + TAX + fees - discounts."""
        financial_labels = get_financial_labels.func()["financial_labels"]

        # Get values
        grand_total = None
        if "GRAND_TOTAL" in financial_labels and financial_labels["GRAND_TOTAL"]:
            grand_total = financial_labels["GRAND_TOTAL"][0]["numeric_value"]

        subtotal = None
        if "SUBTOTAL" in financial_labels and financial_labels["SUBTOTAL"]:
            subtotal = financial_labels["SUBTOTAL"][0]["numeric_value"]

        tax = 0.0  # Default to 0 if no tax
        if "TAX" in financial_labels and financial_labels["TAX"]:
            tax = sum(item["numeric_value"] or 0 for item in financial_labels["TAX"])

        # Sum discounts and coupons (these reduce the total)
        discounts = 0.0
        for discount_type in ["DISCOUNT", "COUPON"]:
            if discount_type in financial_labels:
                discounts += sum(
                    abs(item["numeric_value"] or 0)
                    for item in financial_labels[discount_type]
                )

        issues = []
        is_valid = True

        if grand_total is not None and subtotal is not None:
            expected_total = subtotal + tax - discounts
            tolerance = 0.01

            if abs(grand_total - expected_total) > tolerance:
                issues.append(
                    {
                        "type": "grand_total_mismatch",
                        "message": (
                            f"GRAND_TOTAL ({grand_total}) ≠ "
                            f"SUBTOTAL ({subtotal}) + TAX ({tax}) - "
                            f"DISCOUNTS ({discounts}) = {expected_total}"
                        ),
                        "grand_total": grand_total,
                        "expected": expected_total,
                        "difference": grand_total - expected_total,
                    }
                )
                is_valid = False
        elif grand_total is None:
            issues.append(
                {
                    "type": "missing_grand_total",
                    "message": "No GRAND_TOTAL label found",
                }
            )
            is_valid = False
        elif subtotal is None:
            issues.append(
                {
                    "type": "missing_subtotal",
                    "message": "No SUBTOTAL label found",
                }
            )
            is_valid = False

        return {
            "is_valid": is_valid,
            "issues": issues,
            "values": {
                "grand_total": grand_total,
                "subtotal": subtotal,
                "tax": tax,
                "discounts": discounts,
                "expected_total": (
                    subtotal + tax - discounts if subtotal is not None else None
                ),
            },
        }

    @tool
    def validate_subtotal_math() -> dict:
        """Validate that SUBTOTAL = sum of all LINE_TOTAL values."""
        financial_labels = get_financial_labels.func()["financial_labels"]

        subtotal = None
        if "SUBTOTAL" in financial_labels and financial_labels["SUBTOTAL"]:
            subtotal = financial_labels["SUBTOTAL"][0]["numeric_value"]

        line_totals = []
        if "LINE_TOTAL" in financial_labels:
            line_totals = [
                item["numeric_value"]
                for item in financial_labels["LINE_TOTAL"]
                if item["numeric_value"] is not None
            ]

        sum_line_totals = sum(line_totals) if line_totals else 0.0

        issues = []
        is_valid = True

        if subtotal is not None and line_totals:
            tolerance = 0.01
            if abs(subtotal - sum_line_totals) > tolerance:
                issues.append(
                    {
                        "type": "subtotal_mismatch",
                        "message": (
                            f"SUBTOTAL ({subtotal}) ≠ sum of "
                            f"LINE_TOTAL values ({sum_line_totals})"
                        ),
                        "subtotal": subtotal,
                        "sum_line_totals": sum_line_totals,
                        "line_total_count": len(line_totals),
                        "difference": subtotal - sum_line_totals,
                    }
                )
                is_valid = False
        elif subtotal is None and line_totals:
            issues.append(
                {
                    "type": "missing_subtotal",
                    "message": (
                        f"No SUBTOTAL found but {len(line_totals)} "
                        "LINE_TOTAL values exist"
                    ),
                }
            )
            is_valid = False
        elif subtotal is not None and not line_totals:
            issues.append(
                {
                    "type": "missing_line_totals",
                    "message": (
                        f"SUBTOTAL exists ({subtotal}) but no LINE_TOTAL "
                        "values found"
                    ),
                }
            )
            is_valid = False

        return {
            "is_valid": is_valid,
            "issues": issues,
            "values": {
                "subtotal": subtotal,
                "line_totals": line_totals,
                "sum_line_totals": sum_line_totals,
                "line_total_count": len(line_totals),
            },
        }

    @tool
    def validate_line_item_math() -> dict:
        """Validate that QUANTITY × UNIT_PRICE = LINE_TOTAL for each
        line item."""
        receipt = state["receipt"]
        labels = receipt.get("labels", [])
        words = receipt.get("words", [])

        # Group labels by line_id to find complete line items
        line_groups = {}
        word_lookup = {(w.get("line_id"), w.get("word_id")): w for w in words}

        for label in labels:
            line_id = label.get("line_id")
            if line_id not in line_groups:
                line_groups[line_id] = []

            key = (label.get("line_id"), label.get("word_id"))
            word = word_lookup.get(key, {})
            text = word.get("text", "")

            line_groups[line_id].append(
                {
                    "word_id": label.get("word_id"),
                    "label": label.get("label"),
                    "text": text,
                    "numeric_value": (
                        extract_number(text)
                        if label.get("label")
                        in ["QUANTITY", "UNIT_PRICE", "LINE_TOTAL"]
                        else None
                    ),
                }
            )

        # Find lines with LINE_TOTAL and validate math
        issues = []
        validated_lines = []

        for line_id, line_labels in line_groups.items():
            # Check if this line has a LINE_TOTAL
            line_total_items = [
                item for item in line_labels if item["label"] == "LINE_TOTAL"
            ]
            if not line_total_items:
                continue

            line_total = line_total_items[0]["numeric_value"]
            if line_total is None:
                continue

            # Find QUANTITY and UNIT_PRICE on same line
            quantity_items = [
                item for item in line_labels if item["label"] == "QUANTITY"
            ]
            unit_price_items = [
                item for item in line_labels if item["label"] == "UNIT_PRICE"
            ]

            quantity = quantity_items[0]["numeric_value"] if quantity_items else None
            unit_price = (
                unit_price_items[0]["numeric_value"] if unit_price_items else None
            )

            line_info = {
                "line_id": line_id,
                "line_total": line_total,
                "quantity": quantity,
                "unit_price": unit_price,
                "has_product_name": any(
                    item["label"] == "PRODUCT_NAME" for item in line_labels
                ),
            }

            if quantity is not None and unit_price is not None:
                calculated_total = quantity * unit_price
                tolerance = 0.01

                if abs(line_total - calculated_total) > tolerance:
                    issues.append(
                        {
                            "type": "line_item_math_error",
                            "line_id": line_id,
                            "message": (
                                f"Line {line_id}: LINE_TOTAL ({line_total}) ≠ "
                                f"QUANTITY ({quantity}) × "
                                f"UNIT_PRICE ({unit_price}) = "
                                f"{calculated_total}"
                            ),
                            "line_total": line_total,
                            "quantity": quantity,
                            "unit_price": unit_price,
                            "calculated_total": calculated_total,
                            "difference": line_total - calculated_total,
                        }
                    )

                line_info["math_valid"] = (
                    abs(line_total - calculated_total) <= tolerance
                )
                line_info["calculated_total"] = calculated_total
            else:
                line_info["math_valid"] = None  # Cannot validate without both values

            validated_lines.append(line_info)

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "validated_lines": validated_lines,
            "total_line_items": len(validated_lines),
        }

    @tool
    def find_missing_line_item_fields() -> dict:
        """Find LINE_TOTALs that are missing required line-item fields."""
        receipt = state["receipt"]
        labels = receipt.get("labels", [])

        # Group by line_id
        line_groups = {}
        for label in labels:
            line_id = label.get("line_id")
            if line_id not in line_groups:
                line_groups[line_id] = set()
            line_groups[line_id].add(label.get("label"))

        missing_fields = []

        for line_id, label_set in line_groups.items():
            if "LINE_TOTAL" in label_set:
                # This line has a LINE_TOTAL, check for required fields
                missing = []

                if "PRODUCT_NAME" not in label_set:
                    missing.append("PRODUCT_NAME")
                if "QUANTITY" not in label_set:
                    missing.append("QUANTITY")
                if "UNIT_PRICE" not in label_set:
                    missing.append("UNIT_PRICE")

                if missing:
                    missing_fields.append(
                        {
                            "line_id": line_id,
                            "has_labels": list(label_set),
                            "missing_labels": missing,
                            "severity": (
                                "high" if "PRODUCT_NAME" in missing else "medium"
                            ),
                        }
                    )

        return {
            "missing_fields": missing_fields,
            "lines_with_issues": len(missing_fields),
            "total_line_items": len(
                [
                    line_id
                    for line_id, labels in line_groups.items()
                    if "LINE_TOTAL" in labels
                ]
            ),
        }

    @tool
    def propose_corrections(corrections: list[dict]) -> dict:
        """Submit label corrections with detailed reasoning."""
        state["corrections"] = corrections

        return {
            "success": True,
            "corrections_submitted": len(corrections),
            "corrections": corrections,
        }

    tools = [
        get_table_structure,
        get_financial_labels,
        detect_currency,
        validate_grand_total_math,
        validate_subtotal_math,
        validate_line_item_math,
        find_missing_line_item_fields,
        propose_corrections,
    ]

    return tools, state


def create_enhanced_financial_validation_graph(
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """Create the enhanced financial validation sub-agent graph."""
    if settings is None:
        settings = get_settings()

    llm = create_ollama_llm(settings)

    # Create shared tool state that can be updated between runs
    shared_tool_state = {
        "receipt": {},
        "table_structure": None,
        "currency": None,
        "corrections": [],
    }

    # Create state holder for external interface
    state_holder = {
        "receipt": {},
        "table_structure": None,
        "tool_state": shared_tool_state,
    }

    tools, _ = create_enhanced_financial_validation_tools(
        state_holder["receipt"],
        state_holder["table_structure"],
        shared_state=shared_tool_state,
    )

    llm_with_tools = llm.bind_tools(tools)
    agent_node = create_agent_node_with_retry(
        llm=llm_with_tools,
        agent_name="enhanced-financial-validation",
    )

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

    return workflow.compile(), state_holder


async def run_enhanced_financial_validation(
    graph: Any,
    state_holder: dict,
    receipt_text: str,
    labels: list[dict],
    words: list[dict],
    table_structure: Optional[dict] = None,
) -> dict:
    """Run enhanced financial validation sub-agent."""
    # Update shared state with all data (tools will see these updates)
    tool_state = state_holder.get("tool_state")
    if tool_state:
        tool_state["receipt"] = {
            "receipt_text": receipt_text,
            "labels": labels,
            "words": words,
        }
        tool_state["table_structure"] = table_structure
    else:
        # Fallback: update state_holder directly
        state_holder["receipt"] = {
            "receipt_text": receipt_text,
            "labels": labels,
            "words": words,
        }
        state_holder["table_structure"] = table_structure

    # Create initial state
    initial_state = FinancialValidationState(
        receipt_text=receipt_text,
        labels=labels,
        words=words,
        messages=[
            SystemMessage(content=ENHANCED_FINANCIAL_VALIDATION_PROMPT),
            HumanMessage(
                content=(
                    "Please perform comprehensive financial validation "
                    "for this receipt. Start by checking table structure and "
                    "financial labels, then validate all math."
                )
            ),
        ],
    )

    try:
        await graph.ainvoke(initial_state)
        corrections = tool_state.get("corrections", [])
        currency = tool_state.get("currency", "USD")

        return {
            "currency": currency,
            "is_valid": len(corrections) == 0,
            "corrections": corrections,
            "table_structure_used": table_structure is not None,
        }
    except Exception as e:
        logger.exception(f"Enhanced financial validation failed: {e}")
        return {
            "currency": None,
            "is_valid": False,
            "issues": [{"type": "validation_error", "message": str(e)}],
            "corrections": [],
        }
