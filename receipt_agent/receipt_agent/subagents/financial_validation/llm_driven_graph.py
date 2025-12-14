"""
LLM-Driven Financial Discovery Sub-Agent

This sub-agent uses LLM reasoning rather than hard-coded rules to identify
financial values (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL) on receipts.

## Approach

1. **Structure Analysis**: Analyze receipt text, words, and table structure
2. **Numeric Discovery**: Find all numeric values with positional context
3. **LLM Reasoning**: Use LLM to reason about which values represent which financial types
4. **Mathematical Verification**: Test that proposed assignments follow receipt math
5. **Context Generation**: Provide structured financial context for label assignment

## Key Features

- **Reasoning-First**: LLM analyzes patterns and context before making assignments
- **Self-Verification**: Agent tests its own mathematical conclusions
- **Explainable**: Detailed reasoning provided for each financial assignment
- **Flexible**: Adapts to different receipt formats without rigid rules
- **Mathematical Validation**: Ensures GRAND_TOTAL = SUBTOTAL + TAX, etc.

## Output

Provides rich financial context that downstream label sub-agents can use:
```python
{
    "financial_candidates": {
        "GRAND_TOTAL": [{"line_id": 25, "word_id": 3, "value": 45.67, "confidence": 0.95}],
        "SUBTOTAL": [{"line_id": 23, "word_id": 2, "value": 42.18, "confidence": 0.90}],
        "TAX": [{"line_id": 24, "word_id": 2, "value": 3.49, "confidence": 0.85}],
        "LINE_TOTAL": [
            {"line_id": 10, "word_id": 4, "value": 12.99, "confidence": 0.80},
            {"line_id": 12, "word_id": 3, "value": 8.50, "confidence": 0.75}
        ]
    },
    "mathematical_validation": {
        "verified": 2,
        "total_tests": 2,
        "all_valid": True
    },
    "currency": "USD",
    "llm_reasoning": {
        "structure_analysis": "...",
        "final_assessment": "...",
        "confidence": "high"
    }
}
```
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

# Financial label types for reference
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

LLM_DRIVEN_FINANCIAL_DISCOVERY_PROMPT = """You are a financial discovery agent. Your task is to analyze a receipt and identify financial values through a 5-step process.

## MANDATORY WORKFLOW - Execute in this EXACT order:

**STEP 1**: Call `analyze_receipt_structure()`
**STEP 2**: Call `identify_numeric_candidates()`
**STEP 3**: Call `reason_about_financial_layout(reasoning="your analysis", candidate_assignments=[your assignments])`
**STEP 4**: Call `test_mathematical_relationships()`
**STEP 5**: Call `finalize_financial_context(final_reasoning="summary", confidence_assessment="high/medium/low")`

## Rules:
- Call each tool EXACTLY ONCE in the specified order
- Do NOT repeat tools or skip steps
- After calling finalize_financial_context, you are DONE - do not continue

## Step 3 Format Example:
```
reason_about_financial_layout(
    reasoning="This receipt shows a simple transaction with a balance due of $8.59...",
    candidate_assignments=[
        {"line_id": 24, "word_id": 1, "value": 8.59, "proposed_type": "GRAND_TOTAL", "confidence": 0.95, "reasoning": "This is the balance due amount"}
    ]
)
```

Start with Step 1 now.

## Financial Types to Identify

- **GRAND_TOTAL**: The final amount due (usually largest, at bottom)
- **SUBTOTAL**: Sum of all line items before tax/fees (usually before tax line)
- **TAX**: Sales tax, VAT, or other taxes (usually between subtotal and grand total)
- **LINE_TOTAL**: Individual line item totals (extended price for each product)
- **UNIT_PRICE**: Price per unit of a product
- **QUANTITY**: Number or weight of items purchased

## Mathematical Relationships to Verify

- GRAND_TOTAL = SUBTOTAL + TAX + fees - discounts (±$0.01 tolerance)
- SUBTOTAL = sum of all LINE_TOTAL values (±$0.01 tolerance)
- For line items: QUANTITY × UNIT_PRICE = LINE_TOTAL (±$0.01 tolerance)

## Approach Guidelines

- **Think like a human** reading the receipt - what story does the financial flow tell?
- **Use context clues** from surrounding text, positioning, and table structure
- **Consider business logic** - receipts follow predictable patterns but vary in format
- **Reason about relationships** - which numbers should mathematically relate to each other?
- **Verify your reasoning** - test that your assignments follow correct math
- **Be transparent** - explain your reasoning clearly for each assignment

## Quality Standards

- Assign financial types based on logical reasoning, not just position or keywords
- Ensure mathematical relationships validate correctly
- Provide confidence scores based on strength of evidence
- Include detailed reasoning for each assignment
- Acknowledge uncertainty when evidence is ambiguous

Start by analyzing the receipt structure to understand the layout and financial flow."""


def create_llm_driven_financial_tools(
    receipt_data: dict,
    table_structure: Optional[dict] = None,
    shared_state: Optional[dict] = None,
) -> tuple[list[Any], dict]:
    """Create tools for LLM-driven financial discovery sub-agent."""
    # Constants for output limits to prevent context overflow
    MAX_LINE_PREVIEW_LINES = 20
    MAX_NUMERIC_CANDIDATES = 50

    if shared_state is not None:
        # Use the shared state dict so tools can access updated data
        state = shared_state
    else:
        # Create local state for standalone usage
        state = {
            "receipt": receipt_data,
            "table_structure": table_structure,
            "currency": None,
            "financial_reasoning": "",
            "proposed_assignments": [],
            "verification_results": [],
        }

    @tool
    def analyze_receipt_structure() -> dict:
        """Get comprehensive view of receipt structure for financial analysis."""
        try:
            logger.debug(
                "TOOL: analyze_receipt_structure called, state keys=%s",
                list(state.keys()),
            )
            receipt = state["receipt"]
            logger.debug("TOOL: receipt type=%s", type(receipt))
            if isinstance(receipt, dict):
                logger.debug("TOOL: receipt keys=%s", list(receipt.keys()))

            receipt_text = receipt.get("receipt_text", "")
            words = receipt.get("words", [])
            table_structure = state.get("table_structure")

            logger.debug(
                "TOOL: receipt_text length=%d, words count=%d",
                len(receipt_text),
                len(words),
            )
            if words:
                logger.debug("TOOL: first word=%s", words[0])
            else:
                logger.debug("TOOL: words list is empty")

            # Extract basic structure info
            lines_by_id = {}
            for word in words:
                line_id = word.get("line_id")
                if line_id not in lines_by_id:
                    lines_by_id[line_id] = []
                lines_by_id[line_id].append(word.get("text", ""))

            # Build line text for context
            line_texts = {}
            for line_id, word_texts in lines_by_id.items():
                line_texts[line_id] = " ".join(word_texts)

            # Sort lines deterministically and truncate to prevent context overflow
            sorted_line_items = sorted(line_texts.items())
            total_lines = len(sorted_line_items)
            lines_truncated = total_lines > MAX_LINE_PREVIEW_LINES
            preview_lines = sorted_line_items[:MAX_LINE_PREVIEW_LINES]

            logger.debug(
                "TOOL: Returning result with %d words, %d lines (showing %d)",
                len(words),
                total_lines,
                len(preview_lines),
            )

            return {
                "receipt_text": receipt_text,
                "total_words": len(words),
                "total_lines": total_lines,
                "lines_truncated": lines_truncated,
                "has_table_structure": table_structure is not None,
                "table_summary": (
                    table_structure.get("summary", "")
                    if table_structure
                    else "No table structure available"
                ),
                "line_preview": {
                    str(line_id): text for line_id, text in preview_lines
                },
                "structure_notes": {
                    "words_per_line_avg": len(words) / max(total_lines, 1),
                    "table_columns": (
                        len(table_structure.get("columns", []))
                        if table_structure
                        else 0
                    ),
                    "table_rows": (
                        len(table_structure.get("rows", []))
                        if table_structure
                        else 0
                    ),
                },
            }
        except Exception as e:
            logger.exception(
                "TOOL: Exception in analyze_receipt_structure: %s", e
            )
            return {"error": f"Tool failed: {e}"}

    @tool
    def identify_numeric_candidates() -> dict:
        """Find all numeric values in the receipt with their positions and surrounding context."""
        receipt = state["receipt"]
        words = receipt.get("words", [])

        # Group words by line for context
        lines_by_id = {}
        for word in words:
            line_id = word.get("line_id")
            if line_id not in lines_by_id:
                lines_by_id[line_id] = []
            lines_by_id[line_id].append(word)

        numeric_candidates = []

        for word in words:
            text = word.get("text", "")
            numeric_value = extract_number(text)

            if numeric_value is not None:
                line_id = word.get("line_id")

                # Get full line context
                line_words = lines_by_id.get(line_id, [])
                line_context = " ".join(
                    [w.get("text", "") for w in line_words]
                )

                # Find position within line
                word_position = next(
                    (
                        i
                        for i, w in enumerate(line_words)
                        if w.get("word_id") == word.get("word_id")
                    ),
                    -1,
                )

                numeric_candidates.append(
                    {
                        "line_id": line_id,
                        "word_id": word.get("word_id"),
                        "text": text,
                        "numeric_value": numeric_value,
                        "line_context": line_context,
                        "position_in_line": word_position,
                        "is_rightmost": word_position == len(line_words) - 1,
                        "confidence": word.get("confidence", 1.0),
                    }
                )

        # Sort by value for easier analysis (deterministic: highest values first)
        numeric_candidates.sort(key=lambda x: x["numeric_value"], reverse=True)

        # Compute statistics from full list before truncation
        total_candidates = len(numeric_candidates)
        candidates_truncated = total_candidates > MAX_NUMERIC_CANDIDATES
        truncated_candidates = numeric_candidates[:MAX_NUMERIC_CANDIDATES]

        # Calculate statistics from full list
        values = [c["numeric_value"] for c in numeric_candidates]
        sorted_values = sorted(values) if values else []
        median_value = (
            sorted_values[len(sorted_values) // 2] if sorted_values else 0
        )

        return {
            "numeric_candidates": truncated_candidates,
            "total_numeric_values": total_candidates,
            "candidates_truncated": candidates_truncated,
            "value_statistics": {
                "min_value": (min(values) if values else 0),
                "max_value": (max(values) if values else 0),
                "median_value": median_value,
                "value_count_by_range": {
                    "over_100": len(
                        [
                            c
                            for c in numeric_candidates
                            if c["numeric_value"] > 100
                        ]
                    ),
                    "10_to_100": len(
                        [
                            c
                            for c in numeric_candidates
                            if 10 <= c["numeric_value"] <= 100
                        ]
                    ),
                    "under_10": len(
                        [
                            c
                            for c in numeric_candidates
                            if c["numeric_value"] < 10
                        ]
                    ),
                },
            },
            "context_patterns": [
                {
                    "value": c["numeric_value"],
                    "context": c["line_context"],
                    "position": (
                        "rightmost"
                        if c["is_rightmost"]
                        else f"position_{c['position_in_line']}"
                    ),
                }
                for c in truncated_candidates[
                    :10
                ]  # First 10 for pattern analysis
            ],
        }

    @tool
    def reason_about_financial_layout(
        reasoning: str, candidate_assignments: list[dict]
    ) -> dict:
        """Apply reasoning to identify which numeric values represent which financial types.

        Args:
            reasoning: Your detailed reasoning about the receipt's financial structure and how you identified each type
            candidate_assignments: List of assignments in this format:
                [
                    {
                        "line_id": 23,
                        "word_id": 2,
                        "value": 45.67,
                        "proposed_type": "GRAND_TOTAL",
                        "confidence": 0.95,
                        "reasoning": "This is the largest value at the bottom of the receipt, appearing after 'TOTAL'"
                    },
                    {
                        "line_id": 21,
                        "word_id": 3,
                        "value": 42.18,
                        "proposed_type": "SUBTOTAL",
                        "confidence": 0.90,
                        "reasoning": "This appears before tax with 'SUBTOTAL' text, and is close to sum of line items"
                    }
                ]

        Returns:
            Summary of your assignments organized by financial type
        """
        if not candidate_assignments:
            return {"error": "No candidate assignments provided"}

        # Allowed financial types
        ALLOWED_FINANCIAL_TYPES = {
            "GRAND_TOTAL",
            "SUBTOTAL",
            "TAX",
            "LINE_TOTAL",
        }

        # Validate assignment format and proposed_type
        for assignment in candidate_assignments:
            required_fields = [
                "line_id",
                "word_id",
                "value",
                "proposed_type",
                "confidence",
                "reasoning",
            ]
            missing_fields = [
                field for field in required_fields if field not in assignment
            ]
            if missing_fields:
                return {
                    "error": f"Assignment missing required fields: {missing_fields}"
                }

            # Validate proposed_type is one of the allowed types
            proposed_type = assignment.get("proposed_type")
            if proposed_type not in ALLOWED_FINANCIAL_TYPES:
                return {
                    "error": f"Invalid proposed_type '{proposed_type}'. "
                    f"Must be one of: {', '.join(sorted(ALLOWED_FINANCIAL_TYPES))}"
                }

        # Prevent duplicate (line_id, word_id) assignments
        # Keep highest confidence assignment when duplicates exist
        seen_words = {}
        duplicates_removed = []
        for assignment in candidate_assignments:
            word_key = (assignment.get("line_id"), assignment.get("word_id"))
            existing = seen_words.get(word_key)

            if existing is None:
                # First assignment for this word - keep it
                seen_words[word_key] = assignment
            else:
                # Duplicate detected - keep the one with higher confidence
                existing_confidence = existing.get("confidence", 0)
                new_confidence = assignment.get("confidence", 0)

                if new_confidence > existing_confidence:
                    # New assignment has higher confidence - replace
                    duplicates_removed.append(
                        {
                            "removed": existing,
                            "kept": assignment,
                            "reason": "higher_confidence",
                        }
                    )
                    seen_words[word_key] = assignment
                else:
                    # Existing assignment has higher or equal confidence - keep it
                    duplicates_removed.append(
                        {
                            "removed": assignment,
                            "kept": existing,
                            "reason": "lower_confidence",
                        }
                    )

        # Log warnings about removed duplicates
        if duplicates_removed:
            dup_details = []
            for dup_info in duplicates_removed:
                removed = dup_info["removed"]
                kept = dup_info["kept"]
                dup_details.append(
                    f"line {removed.get('line_id')} word {removed.get('word_id')} "
                    f"(removed: {removed.get('proposed_type')} conf={removed.get('confidence', 0):.2f}, "
                    f"kept: {kept.get('proposed_type')} conf={kept.get('confidence', 0):.2f})"
                )
            logger.warning(
                f"⚠️  Removed {len(duplicates_removed)} duplicate assignments, "
                f"kept highest confidence: {', '.join(dup_details[:5])}"
                + (
                    f" (and {len(duplicates_removed) - 5} more)"
                    if len(duplicates_removed) > 5
                    else ""
                )
            )

        # Store the deduplicated assignments
        deduplicated_assignments = list(seen_words.values())
        state["financial_reasoning"] = reasoning
        state["proposed_assignments"] = deduplicated_assignments

        # Group by type for easier analysis (using deduplicated assignments)
        assignments_by_type = {}
        for assignment in deduplicated_assignments:
            financial_type = assignment.get("proposed_type")
            if financial_type not in assignments_by_type:
                assignments_by_type[financial_type] = []
            assignments_by_type[financial_type].append(assignment)

        # Calculate summary statistics (using deduplicated assignments)
        confidence_scores = [
            a.get("confidence", 0) for a in deduplicated_assignments
        ]
        avg_confidence = (
            sum(confidence_scores) / len(confidence_scores)
            if confidence_scores
            else 0
        )

        return {
            "reasoning_recorded": True,
            "total_assignments": len(deduplicated_assignments),
            "duplicates_removed": len(duplicates_removed),
            "assignments_by_type": assignments_by_type,
            "assignment_summary": {
                financial_type: {
                    "count": len(assignments),
                    "avg_confidence": sum(
                        a.get("confidence", 0) for a in assignments
                    )
                    / len(assignments),
                    "values": [a.get("value") for a in assignments],
                }
                for financial_type, assignments in assignments_by_type.items()
            },
            "overall_confidence": avg_confidence,
            "ready_for_verification": True,
        }

    def _coerce_numeric_value(value: Any) -> Optional[float]:
        """Coerce a value to float, handling strings and various numeric types.

        Args:
            value: Value that may be int, float, string, or other type

        Returns:
            float value if conversion succeeds, None otherwise
        """
        if value is None:
            return None

        # If already a numeric type, convert to float
        if isinstance(value, (int, float)):
            return float(value)

        # If string, try direct float conversion first
        if isinstance(value, str):
            # Try direct conversion (handles "25.79", "123", etc.)
            try:
                return float(value.strip())
            except (ValueError, AttributeError):
                # If that fails, try extract_number (handles "$25.79", "(25.79)", etc.)
                return extract_number(value)

        # For other types, try to convert via string
        try:
            return float(str(value))
        except (ValueError, TypeError):
            logger.warning(
                f"Could not coerce value to float: {value} (type: {type(value)})"
            )
            return None

    @tool
    def test_mathematical_relationships() -> dict:
        """Test whether your proposed financial assignments follow correct receipt mathematics."""
        proposed = state.get("proposed_assignments", [])
        if not proposed:
            return {
                "error": "No proposed assignments to test. Call reason_about_financial_layout first."
            }

        # Extract values by type, coercing to numeric
        values_by_type = {}
        invalid_values = []
        for assignment in proposed:
            financial_type = assignment.get("proposed_type")
            raw_value = assignment.get("value")
            coerced_value = _coerce_numeric_value(raw_value)

            if coerced_value is None:
                invalid_values.append(
                    {
                        "line_id": assignment.get("line_id"),
                        "word_id": assignment.get("word_id"),
                        "financial_type": financial_type,
                        "raw_value": raw_value,
                    }
                )
                logger.warning(
                    f"Invalid numeric value for {financial_type} at line {assignment.get('line_id')} "
                    f"word {assignment.get('word_id')}: {raw_value} (type: {type(raw_value)})"
                )
                continue  # Skip invalid values

            if financial_type not in values_by_type:
                values_by_type[financial_type] = []
            values_by_type[financial_type].append(
                {
                    "value": coerced_value,  # Use coerced numeric value
                    "line_id": assignment.get("line_id"),
                    "word_id": assignment.get("word_id"),
                    "confidence": assignment.get("confidence", 0.5),
                }
            )

        # If we have invalid values, include them in the response
        if invalid_values:
            logger.warning(
                f"Skipped {len(invalid_values)} assignments with invalid numeric values"
            )

        verification_results = []
        tolerance = 0.01

        # Test 1: GRAND_TOTAL = SUBTOTAL + TAX
        if "GRAND_TOTAL" in values_by_type and "SUBTOTAL" in values_by_type:
            grand_total_info = values_by_type["GRAND_TOTAL"][
                0
            ]  # Use highest confidence or first
            subtotal_info = values_by_type["SUBTOTAL"][0]

            grand_total = grand_total_info["value"]
            subtotal = subtotal_info["value"]

            # Handle optional TAX
            tax = 0
            tax_info = None
            if "TAX" in values_by_type:
                tax_info = values_by_type["TAX"][0]
                tax = tax_info["value"]

            calculated_total = subtotal + tax
            difference = grand_total - calculated_total
            math_correct = abs(difference) <= tolerance

            verification_results.append(
                {
                    "test_name": "GRAND_TOTAL = SUBTOTAL + TAX",
                    "description": "Verify that grand total equals subtotal plus tax",
                    "grand_total": grand_total,
                    "subtotal": subtotal,
                    "tax": tax,
                    "calculated_total": calculated_total,
                    "difference": difference,
                    "tolerance": tolerance,
                    "passes": math_correct,
                    "confidence_factors": {
                        "grand_total_confidence": grand_total_info[
                            "confidence"
                        ],
                        "subtotal_confidence": subtotal_info["confidence"],
                        "tax_confidence": (
                            tax_info["confidence"] if tax_info else 1.0
                        ),
                    },
                }
            )

        # Test 2: SUBTOTAL = sum(LINE_TOTAL)
        if "SUBTOTAL" in values_by_type and "LINE_TOTAL" in values_by_type:
            subtotal_info = values_by_type["SUBTOTAL"][0]
            line_total_infos = values_by_type["LINE_TOTAL"]

            subtotal = subtotal_info["value"]
            line_totals = [info["value"] for info in line_total_infos]

            calculated_subtotal = sum(line_totals)
            difference = subtotal - calculated_subtotal
            math_correct = abs(difference) <= tolerance

            verification_results.append(
                {
                    "test_name": "SUBTOTAL = sum(LINE_TOTAL)",
                    "description": "Verify that subtotal equals sum of all line totals",
                    "subtotal": subtotal,
                    "line_totals": line_totals,
                    "line_total_count": len(line_totals),
                    "calculated_subtotal": calculated_subtotal,
                    "difference": difference,
                    "tolerance": tolerance,
                    "passes": math_correct,
                    "confidence_factors": {
                        "subtotal_confidence": subtotal_info["confidence"],
                        "line_totals_avg_confidence": sum(
                            info["confidence"] for info in line_total_infos
                        )
                        / len(line_total_infos),
                    },
                }
            )

        # Test 3: LINE_TOTAL math (if QUANTITY and UNIT_PRICE available)
        line_item_tests = []
        if "LINE_TOTAL" in values_by_type:
            # Group by line_id to find complete line items
            line_groups = {}
            for assignment in proposed:
                line_id = assignment.get("line_id")
                if line_id not in line_groups:
                    line_groups[line_id] = {}
                line_groups[line_id][
                    assignment.get("proposed_type")
                ] = assignment

            for line_id, line_assignments in line_groups.items():
                if "LINE_TOTAL" in line_assignments:
                    line_total_raw = line_assignments["LINE_TOTAL"]["value"]
                    line_total = _coerce_numeric_value(line_total_raw)

                    if line_total is None:
                        continue  # Skip if can't coerce LINE_TOTAL

                    if (
                        "QUANTITY" in line_assignments
                        and "UNIT_PRICE" in line_assignments
                    ):
                        quantity_raw = line_assignments["QUANTITY"]["value"]
                        unit_price_raw = line_assignments["UNIT_PRICE"][
                            "value"
                        ]
                        quantity = _coerce_numeric_value(quantity_raw)
                        unit_price = _coerce_numeric_value(unit_price_raw)

                        if quantity is None or unit_price is None:
                            continue  # Skip if can't coerce QUANTITY or UNIT_PRICE

                        calculated_line_total = quantity * unit_price
                        difference = line_total - calculated_line_total
                        math_correct = abs(difference) <= tolerance

                        line_item_tests.append(
                            {
                                "line_id": line_id,
                                "quantity": quantity,
                                "unit_price": unit_price,
                                "line_total": line_total,
                                "calculated_line_total": calculated_line_total,
                                "difference": difference,
                                "passes": math_correct,
                            }
                        )

        if line_item_tests:
            verification_results.append(
                {
                    "test_name": "LINE_TOTAL = QUANTITY × UNIT_PRICE",
                    "description": "Verify line item mathematics for individual products",
                    "line_item_tests": line_item_tests,
                    "total_line_items_tested": len(line_item_tests),
                    "line_items_passed": len(
                        [test for test in line_item_tests if test["passes"]]
                    ),
                    "passes": all(test["passes"] for test in line_item_tests),
                }
            )

        # Store results and calculate overall status
        state["verification_results"] = verification_results

        tests_passed = sum(
            1 for result in verification_results if result.get("passes", False)
        )
        total_tests = len(verification_results)
        all_tests_pass = tests_passed == total_tests

        return {
            "verification_summary": {
                "total_tests": total_tests,
                "tests_passed": tests_passed,
                "tests_failed": total_tests - tests_passed,
                "all_tests_pass": all_tests_pass,
                "success_rate": (
                    tests_passed / total_tests if total_tests > 0 else 0
                ),
                "invalid_values_count": len(invalid_values),
            },
            "verification_results": verification_results,
            "invalid_values": invalid_values if invalid_values else None,
            "mathematical_validity": "valid" if all_tests_pass else "invalid",
            "recommendations": (
                "All mathematical relationships verified successfully."
                if all_tests_pass
                else "Some mathematical relationships failed - consider revising financial assignments."
            ),
        }

    @tool
    def finalize_financial_context(
        final_reasoning: str, confidence_assessment: str, currency: str = "USD"
    ) -> dict:
        """Submit your final financial analysis as context for downstream label assignment.

        Args:
            final_reasoning: Your final reasoning about the financial structure and assignments
            confidence_assessment: Overall assessment of confidence (e.g., "high", "medium", "low")
            currency: Detected currency (default: USD)

        Returns:
            Structured financial context for label assignment
        """
        proposed = state.get("proposed_assignments", [])
        verification_results = state.get("verification_results", [])

        if not proposed:
            return {
                "error": "No proposed assignments available. Call reason_about_financial_layout first."
            }

        # Get receipt data to look up word text
        receipt = state.get("receipt", {})
        words = receipt.get("words", [])

        # Create a lookup dict for fast word access by (line_id, word_id)
        word_lookup = {}
        for word in words:
            key = (word.get("line_id"), word.get("word_id"))
            word_lookup[key] = word.get("text", "")

        # Build the financial context output
        financial_candidates = {}
        for assignment in proposed:
            financial_type = assignment.get("proposed_type")
            if financial_type not in financial_candidates:
                financial_candidates[financial_type] = []

            # Look up the word text
            word_key = (assignment.get("line_id"), assignment.get("word_id"))
            word_text = word_lookup.get(word_key, "")

            financial_candidates[financial_type].append(
                {
                    "line_id": assignment.get("line_id"),
                    "word_id": assignment.get("word_id"),
                    "text": word_text,
                    "value": assignment.get("value"),
                    "confidence": assignment.get("confidence", 0.5),
                    "reasoning": assignment.get("reasoning", ""),
                }
            )

        # Calculate mathematical validation summary
        total_tests = len(verification_results)
        passed_tests = len(
            [r for r in verification_results if r.get("passes", False)]
        )

        # Store final result in state for retrieval
        final_result = {
            "financial_candidates": financial_candidates,
            "mathematical_validation": {
                "verified": passed_tests,
                "total_tests": total_tests,
                "all_valid": passed_tests == total_tests,
                "success_rate": (
                    passed_tests / total_tests if total_tests > 0 else 0
                ),
                "test_details": verification_results,
            },
            "currency": currency,
            "llm_reasoning": {
                "structure_analysis": state.get("financial_reasoning", ""),
                "final_assessment": final_reasoning,
                "confidence": confidence_assessment,
            },
            "summary": {
                "total_financial_values": len(proposed),
                "financial_types_identified": list(
                    financial_candidates.keys()
                ),
                "avg_confidence": (
                    sum(a.get("confidence", 0) for a in proposed)
                    / len(proposed)
                    if proposed
                    else 0
                ),
                "mathematical_validity": (
                    "valid"
                    if passed_tests == total_tests
                    else "partial" if passed_tests > 0 else "invalid"
                ),
            },
        }

        state["final_result"] = final_result

        return {
            "success": True,
            "context_generated": True,
            "financial_types_count": len(financial_candidates),
            "mathematical_tests_passed": f"{passed_tests}/{total_tests}",
            "overall_confidence": confidence_assessment,
            "ready_for_label_assignment": True,
            "preview": {
                "financial_types": list(financial_candidates.keys()),
                "currency": currency,
                "math_valid": passed_tests == total_tests,
            },
        }

    tools = [
        analyze_receipt_structure,
        identify_numeric_candidates,
        reason_about_financial_layout,
        test_mathematical_relationships,
        finalize_financial_context,
    ]

    return tools, state


def create_llm_driven_financial_graph(
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """Create the LLM-driven financial discovery sub-agent graph."""
    if settings is None:
        settings = get_settings()

    # Use the same LLM creation as main harmonizer (inherits proper auth)
    llm = create_ollama_llm(settings)

    # Create shared tool state that can be updated between runs
    shared_tool_state = {
        "receipt": {},
        "table_structure": None,
        "currency": None,
        "financial_reasoning": "",
        "proposed_assignments": [],
        "verification_results": [],
    }

    # Create state holder for external interface
    state_holder = {
        "receipt": {},
        "table_structure": None,
        "tool_state": shared_tool_state,
    }

    # Create tools with shared state reference
    tools, _ = create_llm_driven_financial_tools(
        state_holder["receipt"],
        state_holder["table_structure"],
        shared_state=shared_tool_state,
    )

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # Create agent node with retry logic
    agent_node = create_agent_node_with_retry(
        llm=llm_with_tools,
        agent_name="llm-driven-financial-discovery",
    )

    # Create tool node
    tool_node = ToolNode(tools)

    # Create graph
    workflow = StateGraph(FinancialValidationState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add edges
    def should_continue(state: FinancialValidationState) -> str:
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


async def run_llm_driven_financial_discovery(
    graph: Any,
    state_holder: dict,
    receipt_text: str,
    labels: list[dict],
    words: list[dict],
    table_structure: Optional[dict] = None,
) -> dict:
    """
    Run the LLM-driven financial discovery sub-agent.

    Args:
        graph: Compiled LangGraph graph
        state_holder: State holder dict
        receipt_text: Receipt text content
        labels: Existing word labels (may be empty)
        words: Word-level data with positions
        table_structure: Optional table structure from table sub-agent

    Returns:
        Financial context dict with candidates, validation, and reasoning
    """
    # Update state holder with current data
    receipt_data = {
        "receipt_text": receipt_text,
        "labels": labels,
        "words": words,
    }
    state_holder["receipt"] = receipt_data
    state_holder["table_structure"] = table_structure

    # Update the existing tool state instead of recreating tools
    tool_state = state_holder.get("tool_state")
    if tool_state is not None:
        # Update the tool state with new receipt data
        tool_state["receipt"] = receipt_data
        tool_state["table_structure"] = table_structure
        # Reset other state for fresh analysis
        tool_state["currency"] = None
        tool_state["financial_reasoning"] = ""
        tool_state["proposed_assignments"] = []
        tool_state["verification_results"] = []

    # Create initial state
    initial_state = FinancialValidationState(
        receipt_text=receipt_text,
        labels=labels,
        words=words,
        messages=[
            SystemMessage(content=LLM_DRIVEN_FINANCIAL_DISCOVERY_PROMPT),
            HumanMessage(
                content="Please analyze this receipt to identify financial values (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL). "
                "Use reasoning to understand the receipt structure, identify numeric candidates, apply your reasoning to assign financial types, "
                "verify the mathematical relationships, and provide structured context for label assignment."
            ),
        ],
    )

    try:
        logger.info(
            f"LLM Financial Discovery: Starting graph execution with {len(words)} words"
        )
        logger.info(
            f"LLM Financial Discovery: Initial state has {len(initial_state.messages)} messages"
        )

        # Run the agent
        await graph.ainvoke(initial_state)

        logger.info("LLM Financial Discovery: Graph execution completed")
        logger.info(
            f"LLM Financial Discovery: Tool state keys: {list(tool_state.keys())}"
        )

        # Extract final result from tool state
        tool_state = state_holder.get("tool_state")
        final_result = tool_state.get("final_result") if tool_state else None

        if final_result:
            logger.info(
                f"LLM Financial Discovery: SUCCESS - final_result found with keys: {list(final_result.keys())}"
            )
            return final_result
        else:
            logger.warning(
                "LLM Financial Discovery: FAILED - no final_result in tool_state"
            )
            if tool_state:
                logger.warning(
                    f"LLM Financial Discovery: Available tool_state keys: {list(tool_state.keys())}"
                )

            # Fallback - extract what we can from tool state
            return {
                "financial_candidates": {},
                "mathematical_validation": {
                    "verified": 0,
                    "total_tests": 0,
                    "all_valid": False,
                },
                "currency": (
                    tool_state.get("currency", "USD") if tool_state else "USD"
                ),
                "llm_reasoning": {
                    "structure_analysis": (
                        tool_state.get("financial_reasoning", "")
                        if tool_state
                        else ""
                    ),
                    "final_assessment": "Agent completed but did not finalize results",
                    "confidence": "unknown",
                },
                "error": "Agent did not call finalize_financial_context",
            }

    except Exception as e:
        logger.exception(f"LLM-driven financial discovery failed: {e}")
        return {
            "financial_candidates": {},
            "mathematical_validation": {
                "verified": 0,
                "total_tests": 0,
                "all_valid": False,
            },
            "currency": None,
            "llm_reasoning": {
                "structure_analysis": "",
                "final_assessment": f"Agent failed with error: {str(e)}",
                "confidence": "none",
            },
            "error": str(e),
        }
