"""
Financial Validation Stage

Validates financial consistency of receipt labels:
- Grand Total = Subtotal + Tax
- Subtotal = Sum of line totals
- Currency detection and consistency

Uses deterministic validation (no LLM) for speed.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .types import (
    FinancialResult,
    LabelCorrection,
    PipelineContext,
    WordLabel,
)

logger = logging.getLogger(__name__)

# Tolerance for floating point comparison
MATH_TOLERANCE = 0.02  # 2 cents


async def run_financial_validation(
    ctx: PipelineContext,
    word_labels: List[WordLabel],
) -> FinancialResult:
    """
    Validate financial consistency of labels.

    Checks:
    1. Currency detection from receipt text
    2. Grand Total = Subtotal + Tax (within tolerance)
    3. Subtotal = Sum of LINE_TOTAL values (within tolerance)

    Args:
        ctx: Pipeline context
        word_labels: Labels from previous stages

    Returns:
        FinancialResult with validation status and issues
    """
    start_time = time.perf_counter()

    try:
        # Run validation in executor
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _validate_financials(ctx.receipt_text, word_labels),
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        result.validation_time_ms = elapsed_ms
        return result

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("Financial validation failed: %s", e)
        return FinancialResult(
            is_valid=False,
            issues=[{"type": "error", "message": str(e)}],
            validation_time_ms=elapsed_ms,
        )


def _validate_financials(
    receipt_text: str,
    word_labels: List[WordLabel],
) -> FinancialResult:
    """Perform financial validation checks."""
    issues: List[Dict[str, Any]] = []
    corrections: List[LabelCorrection] = []

    # Detect currency
    currency = _detect_currency(receipt_text)

    # Extract financial values
    grand_total = _extract_amount(word_labels, "GRAND_TOTAL")
    subtotal = _extract_amount(word_labels, "SUBTOTAL")
    tax = _extract_amount(word_labels, "TAX")
    line_totals = _extract_all_amounts(word_labels, "LINE_TOTAL")

    # Validate grand total equation
    if grand_total is not None and subtotal is not None and tax is not None:
        calculated = subtotal + tax
        diff = abs(grand_total - calculated)
        if diff > MATH_TOLERANCE:
            issues.append(
                {
                    "type": "grand_total_mismatch",
                    "message": f"Grand total ({grand_total:.2f}) != subtotal ({subtotal:.2f}) + tax ({tax:.2f}) = {calculated:.2f}",
                    "grand_total": grand_total,
                    "subtotal": subtotal,
                    "tax": tax,
                    "calculated": calculated,
                    "difference": diff,
                }
            )

    # Validate subtotal equation
    if subtotal is not None and line_totals:
        sum_lines = sum(line_totals)
        diff = abs(subtotal - sum_lines)
        if diff > MATH_TOLERANCE:
            issues.append(
                {
                    "type": "subtotal_mismatch",
                    "message": f"Subtotal ({subtotal:.2f}) != sum of line totals ({sum_lines:.2f})",
                    "subtotal": subtotal,
                    "sum_line_totals": sum_lines,
                    "line_totals": line_totals,
                    "difference": diff,
                }
            )

    # Check for missing labels
    if grand_total is None:
        # Look for unlabeled amounts that could be grand total
        potential_totals = _find_potential_totals(word_labels, receipt_text)
        if potential_totals:
            wl, amount = potential_totals[0]
            if wl.label != "GRAND_TOTAL":
                corrections.append(
                    LabelCorrection(
                        line_id=wl.line_id,
                        word_id=wl.word_id,
                        original_label=wl.label,
                        corrected_label="GRAND_TOTAL",
                        confidence=0.7,
                        reason=f"Amount {amount:.2f} near 'total' keyword, likely grand total",
                        source="financial",
                    )
                )

    is_valid = len(issues) == 0

    return FinancialResult(
        currency=currency,
        is_valid=is_valid,
        grand_total=grand_total,
        subtotal=subtotal,
        tax=tax,
        label_corrections=corrections,
        issues=issues,
    )


def _detect_currency(text: str) -> str:
    """Detect currency from receipt text."""
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
        "British Pound": "GBP",
    }

    # Check symbols
    for symbol, code in currency_symbols.items():
        if symbol in text:
            return code

    # Check keywords
    text_lower = text.lower()
    for keyword, code in currency_keywords.items():
        if keyword.lower() in text_lower:
            return code

    # Default to USD if dollar sign found
    if "$" in text:
        return "USD"

    return "USD"  # Default


def _extract_amount(
    word_labels: List[WordLabel],
    label_type: str,
) -> Optional[float]:
    """Extract numeric amount for a given label type."""
    matching = [wl for wl in word_labels if wl.label == label_type]
    if not matching:
        return None

    # Get the first match (usually there's only one GRAND_TOTAL, etc.)
    wl = matching[0]
    return _parse_amount(wl.text)


def _extract_all_amounts(
    word_labels: List[WordLabel],
    label_type: str,
) -> List[float]:
    """Extract all amounts for a given label type (e.g., LINE_TOTAL)."""
    amounts: List[float] = []
    for wl in word_labels:
        if wl.label == label_type:
            amount = _parse_amount(wl.text)
            if amount is not None:
                amounts.append(amount)
    return amounts


def _parse_amount(text: str) -> Optional[float]:
    """Parse numeric amount from text."""
    # Remove currency symbols and whitespace
    cleaned = re.sub(r"[^\d.,-]", "", text)
    # Handle comma as thousands separator
    cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_potential_totals(
    word_labels: List[WordLabel],
    receipt_text: str,
) -> List[Tuple[WordLabel, float]]:
    """
    Find words that could be grand totals based on context.

    Look for numeric amounts near keywords like 'total', 'amount due', etc.
    """
    potentials: List[Tuple[WordLabel, float]] = []

    # Keywords that suggest a total
    total_keywords = {"total", "amount", "due", "balance", "pay", "charge"}

    # Group labels by line
    lines: Dict[int, List[WordLabel]] = {}
    for wl in word_labels:
        if wl.line_id not in lines:
            lines[wl.line_id] = []
        lines[wl.line_id].append(wl)

    # Check each line for total keywords + amounts
    for line_id, line_words in lines.items():
        has_total_keyword = any(
            any(kw in wl.text.lower() for kw in total_keywords) for wl in line_words
        )
        if not has_total_keyword:
            continue

        # Find amounts on this line
        for wl in line_words:
            if wl.label in ("O", "MERCHANT_NAME", "ADDRESS", "PHONE", "DATE"):
                amount = _parse_amount(wl.text)
                if amount is not None and amount > 0:
                    potentials.append((wl, amount))

    # Sort by amount descending (largest is likely grand total)
    potentials.sort(key=lambda x: -x[1])
    return potentials


def validate_line_item(
    quantity_text: Optional[str],
    unit_price_text: Optional[str],
    line_total_text: Optional[str],
) -> Dict[str, Any]:
    """
    Validate a single line item: quantity * unit_price ≈ line_total.

    Returns validation result with is_valid and any issues.
    """
    result: Dict[str, Any] = {
        "is_valid": True,
        "issues": [],
    }

    if not all([quantity_text, unit_price_text, line_total_text]):
        return result  # Can't validate incomplete line items

    quantity = _parse_amount(quantity_text) if quantity_text else None
    unit_price = _parse_amount(unit_price_text) if unit_price_text else None
    line_total = _parse_amount(line_total_text) if line_total_text else None

    if quantity is None or unit_price is None or line_total is None:
        return result  # Can't parse values

    calculated = quantity * unit_price
    diff = abs(line_total - calculated)

    if diff > MATH_TOLERANCE:
        result["is_valid"] = False
        result["issues"].append(
            {
                "type": "line_item_mismatch",
                "message": f"qty ({quantity}) × price ({unit_price:.2f}) = {calculated:.2f}, but line total is {line_total:.2f}",
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "calculated": calculated,
                "difference": diff,
            }
        )

    return result
