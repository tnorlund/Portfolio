"""
Financial math validation subagent for label evaluation.

This subagent validates mathematical relationships between financial labels:
- GRAND_TOTAL = SUBTOTAL + TAX
- SUBTOTAL = sum(LINE_TOTAL)
- QTY × UNIT_PRICE = LINE_TOTAL (per line)

When math doesn't match, the LLM determines which value is likely wrong based on:
- OCR patterns (common misreads)
- Receipt context (surrounding text)
- Typical receipt structure

Input:
    - visual_lines: Words grouped by y-coordinate with current labels
    - image_id, receipt_id: For output format

Output:
    List of decisions ready for apply_llm_decisions():
    [
        {
            "image_id": "...",
            "receipt_id": 1,
            "issue": {"line_id": 5, "word_id": 3, "current_label": "GRAND_TOTAL"},
            "llm_review": {
                "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                "reasoning": "...",
                "suggested_label": "SUBTOTAL" or None,
                "confidence": "high" | "medium" | "low",
            }
        },
        ...
    ]
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from receipt_agent.constants import FINANCIAL_MATH_LABELS
from receipt_agent.prompts.structured_outputs import (
    FinancialEvaluationResponse,
    extract_json_from_response,
)

from .state import VisualLine, WordContext

logger = logging.getLogger(__name__)

# Tolerance for floating point comparison (cents)
MATH_TOLERANCE = 0.02


@dataclass
class FinancialValue:
    """A word with a financial label and its numeric value."""

    word_context: WordContext
    label: str
    numeric_value: float
    line_index: int
    word_text: str


@dataclass
class MathIssue:
    """A detected math discrepancy."""

    issue_type: str  # "GRAND_TOTAL_MISMATCH", "SUBTOTAL_MISMATCH", "LINE_ITEM_MISMATCH"
    expected_value: float
    actual_value: float
    difference: float
    involved_values: list[FinancialValue]
    description: str


# =============================================================================
# Value Extraction Helpers
# =============================================================================


def extract_number(text: str) -> Optional[float]:
    """
    Extract numeric value from text, handling currency symbols and formatting.

    Handles: $1,234.56, (1.23), -1.23, 1.23-, etc.
    """
    if not text:
        return None

    # Remove currency symbols and whitespace
    clean = text.strip()
    clean = re.sub(r"[$€£¥]", "", clean)
    clean = clean.replace(",", "")

    # Handle parentheses for negative (accounting format)
    is_negative = False
    if clean.startswith("(") and clean.endswith(")"):
        is_negative = True
        clean = clean[1:-1]

    # Handle trailing minus
    if clean.endswith("-"):
        is_negative = True
        clean = clean[:-1]

    # Handle leading minus
    if clean.startswith("-"):
        is_negative = True
        clean = clean[1:]

    try:
        value = float(clean)
        return -value if is_negative else value
    except ValueError:
        return None


def extract_financial_values(
    visual_lines: list[VisualLine],
) -> dict[str, list[FinancialValue]]:
    """
    Extract all financial values from visual lines, grouped by label.

    Returns:
        Dict mapping label -> list of FinancialValue
    """
    values_by_label: dict[str, list[FinancialValue]] = {}

    for line in visual_lines:
        for wc in line.words:
            if wc.current_label is None:
                continue

            label = wc.current_label.label
            if label not in FINANCIAL_MATH_LABELS:
                continue

            numeric = extract_number(wc.word.text)
            if numeric is None:
                # QUANTITY might be a whole number without decimal
                if label == "QUANTITY":
                    try:
                        numeric = float(int(wc.word.text.strip()))
                    except ValueError:
                        continue
                else:
                    continue

            fv = FinancialValue(
                word_context=wc,
                label=label,
                numeric_value=numeric,
                line_index=line.line_index,
                word_text=wc.word.text,
            )

            if label not in values_by_label:
                values_by_label[label] = []
            values_by_label[label].append(fv)

    return values_by_label


# =============================================================================
# Math Validation
# =============================================================================


def check_grand_total_math(
    values: dict[str, list[FinancialValue]],
) -> Optional[MathIssue]:
    """
    Check: GRAND_TOTAL = SUBTOTAL + TAX

    Returns MathIssue if math doesn't match, None otherwise.
    """
    grand_totals = values.get("GRAND_TOTAL", [])
    subtotals = values.get("SUBTOTAL", [])
    taxes = values.get("TAX", [])

    if not grand_totals or not subtotals:
        return None

    # Use the first of each (most receipts have one)
    grand_total = grand_totals[0]
    subtotal = subtotals[0]
    tax = taxes[0] if taxes else None

    tax_value = tax.numeric_value if tax else 0.0
    expected = subtotal.numeric_value + tax_value
    actual = grand_total.numeric_value
    difference = actual - expected

    if abs(difference) <= MATH_TOLERANCE:
        return None

    involved = [grand_total, subtotal]
    if tax:
        involved.append(tax)

    return MathIssue(
        issue_type="GRAND_TOTAL_MISMATCH",
        expected_value=expected,
        actual_value=actual,
        difference=difference,
        involved_values=involved,
        description=(
            f"GRAND_TOTAL ({actual:.2f}) != SUBTOTAL ({subtotal.numeric_value:.2f}) "
            f"+ TAX ({tax_value:.2f}) = {expected:.2f}. Difference: {difference:.2f}"
        ),
    )


def check_subtotal_math(
    values: dict[str, list[FinancialValue]],
) -> Optional[MathIssue]:
    """
    Check: SUBTOTAL = sum(LINE_TOTAL)

    Returns MathIssue if math doesn't match, None otherwise.
    """
    subtotals = values.get("SUBTOTAL", [])
    line_totals = values.get("LINE_TOTAL", [])
    discounts = values.get("DISCOUNT", [])

    if not subtotals or not line_totals:
        return None

    subtotal = subtotals[0]
    line_total_sum = sum(lt.numeric_value for lt in line_totals)

    # Discounts are typically negative or should be subtracted
    discount_sum = sum(abs(d.numeric_value) for d in discounts)

    expected = line_total_sum - discount_sum
    actual = subtotal.numeric_value
    difference = actual - expected

    if abs(difference) <= MATH_TOLERANCE:
        return None

    involved = [subtotal] + line_totals + discounts

    return MathIssue(
        issue_type="SUBTOTAL_MISMATCH",
        expected_value=expected,
        actual_value=actual,
        difference=difference,
        involved_values=involved,
        description=(
            f"SUBTOTAL ({actual:.2f}) != sum(LINE_TOTAL) ({line_total_sum:.2f}) "
            f"- DISCOUNT ({discount_sum:.2f}) = {expected:.2f}. Difference: {difference:.2f}"
        ),
    )


def check_line_item_math(
    values: dict[str, list[FinancialValue]],
) -> list[MathIssue]:
    """
    Check: QTY × UNIT_PRICE = LINE_TOTAL for each line item row.

    Returns list of MathIssue for each mismatched line.
    """
    issues = []

    # Group values by line_index for per-line validation
    line_values: dict[int, dict[str, FinancialValue]] = {}

    for label in ["QUANTITY", "UNIT_PRICE", "LINE_TOTAL"]:
        for fv in values.get(label, []):
            if fv.line_index not in line_values:
                line_values[fv.line_index] = {}
            line_values[fv.line_index][label] = fv

    # Check each line that has all three components
    for line_idx, line_vals in line_values.items():
        qty = line_vals.get("QUANTITY")
        unit_price = line_vals.get("UNIT_PRICE")
        line_total = line_vals.get("LINE_TOTAL")

        if not all([qty, unit_price, line_total]):
            continue

        expected = qty.numeric_value * unit_price.numeric_value
        actual = line_total.numeric_value
        difference = actual - expected

        if abs(difference) <= MATH_TOLERANCE:
            continue

        issues.append(
            MathIssue(
                issue_type="LINE_ITEM_MISMATCH",
                expected_value=expected,
                actual_value=actual,
                difference=difference,
                involved_values=[qty, unit_price, line_total],
                description=(
                    f"Line {line_idx}: LINE_TOTAL ({actual:.2f}) != "
                    f"QTY ({qty.numeric_value}) × UNIT_PRICE ({unit_price.numeric_value:.2f}) "
                    f"= {expected:.2f}. Difference: {difference:.2f}"
                ),
            )
        )

    return issues


def detect_math_issues(
    visual_lines: list[VisualLine],
) -> list[MathIssue]:
    """
    Detect all math issues in the receipt.

    Returns list of MathIssue objects.
    """
    values = extract_financial_values(visual_lines)
    issues = []

    # Check GRAND_TOTAL = SUBTOTAL + TAX
    grand_total_issue = check_grand_total_math(values)
    if grand_total_issue:
        issues.append(grand_total_issue)

    # Check SUBTOTAL = sum(LINE_TOTAL)
    subtotal_issue = check_subtotal_math(values)
    if subtotal_issue:
        issues.append(subtotal_issue)

    # Check QTY × UNIT_PRICE = LINE_TOTAL per line
    line_issues = check_line_item_math(values)
    issues.extend(line_issues)

    return issues


# =============================================================================
# LLM Prompt Building
# =============================================================================


def build_financial_validation_prompt(
    visual_lines: list[VisualLine],
    math_issues: list[MathIssue],
    merchant_name: str = "Unknown",
) -> str:
    """
    Build LLM prompt for financial math validation.

    The LLM determines which value is likely wrong when math doesn't match.
    """
    # Build receipt text representation
    receipt_lines = []
    for line in visual_lines:
        line_text = []
        for wc in line.words:
            label = wc.current_label.label if wc.current_label else "unlabeled"
            line_text.append(f"{wc.word.text}[{label}]")
        receipt_lines.append(f"  Line {line.line_index}: " + " | ".join(line_text))

    receipt_text = "\n".join(receipt_lines[:50])
    if len(visual_lines) > 50:
        receipt_text += f"\n  ... ({len(visual_lines) - 50} more lines)"

    # Build math issues table
    issues_text = []
    for i, issue in enumerate(math_issues):
        values_desc = []
        for fv in issue.involved_values:
            values_desc.append(
                f'    - {fv.label}: "{fv.word_text}" = {fv.numeric_value:.2f} '
                f"(line {fv.line_index})"
            )
        values_str = "\n".join(values_desc)

        issues_text.append(
            f"[{i}] {issue.issue_type}\n"
            f"  Description: {issue.description}\n"
            f"  Values involved:\n{values_str}"
        )

    issues_str = "\n\n".join(issues_text)

    prompt = f"""# Financial Math Validation for {merchant_name}

You are validating mathematical relationships between financial values on a receipt.
For each math issue detected, determine which value is most likely WRONG.

## Receipt Structure
{receipt_text}

## Math Issues Detected
{issues_str}

## Your Task
For each issue above, analyze the receipt context and determine which value is incorrect.

Consider:
1. OCR errors (common misreads: 8↔6, 1↔7, 0↔O)
2. Receipt structure (totals usually at bottom, line items in middle)
3. Which value "looks wrong" based on surrounding text

Respond with a JSON array:

```json
[
  {{
    "index": 0,
    "issue_type": "GRAND_TOTAL_MISMATCH",
    "decision": "INVALID",
    "reasoning": "The GRAND_TOTAL appears to have an OCR error...",
    "suggested_label": null,
    "confidence": "medium"
  }},
  ...
]
```

## Decision Guide
- VALID: The labeled value is correct, another value in the relationship is wrong
- INVALID: This specific value's label is wrong (suggest correction if applicable)
- NEEDS_REVIEW: Cannot determine which value is wrong

For each issue, evaluate ALL involved values. Return one decision per involved value.

Respond ONLY with the JSON array, no other text.
"""
    return prompt


def parse_financial_evaluation_response(
    response_text: str,
    num_issues: int,
) -> list[dict]:
    """Parse the LLM response into a list of decisions."""
    response_text = extract_json_from_response(response_text)

    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "Failed to parse LLM response",
        "suggested_label": None,
        "confidence": "low",
        "issue_type": "UNKNOWN",
    }

    # Parse JSON once
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return [fallback.copy() for _ in range(num_issues)]

    # Try structured parsing with Pydantic
    try:
        if isinstance(parsed, list):
            parsed = {"evaluations": parsed}
        structured_response = FinancialEvaluationResponse.model_validate(parsed)
        return structured_response.to_ordered_list(num_issues)
    except ValidationError as e:
        logger.debug("Structured parsing failed, falling back to manual parsing: %s", e)

    # Fallback to manual parsing (reuse already-parsed JSON)
    try:
        decisions = parsed  # Reuse already-parsed JSON
        if isinstance(decisions, dict):
            decisions = decisions.get("evaluations", [])

        if not isinstance(decisions, list):
            logger.warning("Decisions is not a list: %s", type(decisions).__name__)
            return [fallback.copy() for _ in range(num_issues)]

        result = []
        for i in range(num_issues):
            decision = next((d for d in decisions if d.get("index") == i), None)
            if decision:
                result.append(
                    {
                        "decision": decision.get("decision", "NEEDS_REVIEW"),
                        "reasoning": decision.get("reasoning", ""),
                        "suggested_label": decision.get("suggested_label"),
                        "confidence": decision.get("confidence", "medium"),
                        "issue_type": decision.get("issue_type", "UNKNOWN"),
                    }
                )
            else:
                result.append(fallback.copy())

        return result

    except TypeError as e:
        logger.warning("Failed to process parsed response: %s", e)
        return [fallback.copy() for _ in range(num_issues)]


# =============================================================================
# Main Evaluation Function
# =============================================================================


def evaluate_financial_math(
    visual_lines: list[VisualLine],
    llm: BaseChatModel,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
) -> list[dict]:
    """
    Validate financial math on a receipt.

    This is the main entry point for financial validation.

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        llm: Language model for determining which value is wrong
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context

    Returns:
        List of decisions ready for apply_llm_decisions()
    """
    # Step 1: Detect math issues
    math_issues = detect_math_issues(visual_lines)
    logger.info("Detected %d math issues", len(math_issues))

    if not math_issues:
        logger.info("No math issues found, skipping financial validation")
        return []

    # Log issue summary
    for issue in math_issues:
        logger.info("  %s: %s", issue.issue_type, issue.description)

    # Step 2: Build prompt and call LLM
    prompt = build_financial_validation_prompt(
        visual_lines=visual_lines,
        math_issues=math_issues,
        merchant_name=merchant_name,
    )

    try:
        response = llm.invoke(prompt)
        response_text = (
            response.content if hasattr(response, "content") else str(response)
        )

        # Step 3: Parse response
        # Note: LLM returns one decision per issue, but each issue has multiple values
        decisions = parse_financial_evaluation_response(response_text, len(math_issues))

        # Step 4: Format output - create one result per involved value
        results = []
        for issue, decision in zip(math_issues, decisions, strict=True):
            for fv in issue.involved_values:
                wc = fv.word_context
                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue": {
                            "line_id": wc.word.line_id,
                            "word_id": wc.word.word_id,
                            "current_label": fv.label,
                            "word_text": fv.word_text,
                            "issue_type": issue.issue_type,
                        },
                        "llm_review": {
                            "decision": decision.get("decision", "NEEDS_REVIEW"),
                            "reasoning": decision.get("reasoning", ""),
                            "suggested_label": decision.get("suggested_label"),
                            "confidence": decision.get("confidence", "medium"),
                        },
                    }
                )

        # Log summary
        decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
        for r in results:
            dec = r["llm_review"]["decision"]
            if dec in decision_counts:
                decision_counts[dec] += 1
        logger.info("Financial validation results: %s", decision_counts)

        return results

    except Exception as e:
        # Check for rate limit errors
        from receipt_agent.utils import (
            BothProvidersFailedError,
            OllamaRateLimitError,
        )

        if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
            logger.error("Financial LLM rate limited, propagating for retry: %s", e)
            raise

        logger.error("Financial LLM call failed: %s", e)

        # Return NEEDS_REVIEW for all values in all issues
        results = []
        for issue in math_issues:
            for fv in issue.involved_values:
                wc = fv.word_context
                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue": {
                            "line_id": wc.word.line_id,
                            "word_id": wc.word.word_id,
                            "current_label": fv.label,
                            "word_text": fv.word_text,
                            "issue_type": issue.issue_type,
                        },
                        "llm_review": {
                            "decision": "NEEDS_REVIEW",
                            "reasoning": f"LLM call failed: {e}",
                            "suggested_label": None,
                            "confidence": "low",
                        },
                    }
                )
        return results


# Alias for API consistency with other subagents
evaluate_financial_math_sync = evaluate_financial_math
