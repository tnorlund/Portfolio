"""Receipt amount parsing helpers shared across receipt services."""

from __future__ import annotations

import re
from typing import Any

_CURRENCY_SYMBOLS = r"$€£¥₹"

# ---------------------------------------------------------------------------
# Shared summary-line keyword patterns.
#
# These are the canonical copies of the keyword patterns used to recognize
# receipt summary rows ("Total:", "BALANCE DUE", "Subtotal", ...). They are
# defined here (rather than in receipt_upload) because receipt_dynamo sits at
# the bottom of the dependency graph, so both receipt_upload's amount
# classifier and receipt_dynamo's ReceiptSummary printed-total fallback can
# import them without forking the regexes.
# ---------------------------------------------------------------------------

TOTAL_KEYWORD_RE = re.compile(
    r"\b(total|amount\s+due|balance|authorized)\b", re.I
)
SUBTOTAL_KEYWORD_RE = re.compile(r"\bsub[-\s]?total\b", re.I)
TAX_KEYWORD_RE = re.compile(r"\b(tax|vat)\b", re.I)
NON_PAYMENT_SUMMARY_RE = re.compile(
    r"\b("
    r"savings?|discounts?|refunds?|returns?|coupons?|promos?|"
    r"promotion|rewards?|loyalty|cash\s+back|cashback|store\s+credit"
    r")\b",
    re.I,
)
# Tokens that turn a "total" row into a non-monetary count/summary row
# (e.g. "TOTAL NUMBER OF ITEMS SOLD"), which must NOT be read as a
# grand-total row.
GRAND_TOTAL_DISQUALIFIER_TOKENS = frozenset(
    {
        "number",
        "items",
        "item",
        "qty",
        "quantity",
        "count",
        "sold",
        "transactions",
        "transaction",
        "pieces",
        "units",
        "lines",
        "savings",
        "saved",
    }
)


def is_grand_total_line(line_text: str) -> bool:
    """Return whether joined line text reads as a grand-total row.

    A grand-total row carries a total/balance/amount-due keyword, is not a
    subtotal or tax row, is not a non-payment summary row (savings,
    discounts, ...), and is not an item-count row ("TOTAL NUMBER OF ITEMS
    SOLD").
    """
    if not line_text:
        return False
    if not TOTAL_KEYWORD_RE.search(line_text):
        return False
    if SUBTOTAL_KEYWORD_RE.search(line_text):
        return False
    if TAX_KEYWORD_RE.search(line_text):
        return False
    if NON_PAYMENT_SUMMARY_RE.search(line_text):
        return False
    tokens = {
        token for token in re.split(r"[^a-z]+", line_text.lower()) if token
    }
    return not (tokens & GRAND_TOTAL_DISQUALIFIER_TOKENS)


def parse_receipt_amount(text: Any) -> float | None:
    """Parse receipt currency/amount text without changing the OCR text.

    Handles US-style amounts (``1,234.56``), decimal-comma amounts
    (``8,82``), European grouped amounts (``1.234,56``), and negative
    accounting forms.
    """
    if text is None:
        return None

    raw = str(text).strip()
    if not raw:
        return None

    is_negative = raw.startswith("(") and raw.endswith(")")
    cleaned = re.sub(f"[{re.escape(_CURRENCY_SYMBOLS)}\\s]", "", raw)

    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]

    if cleaned.endswith("-"):
        is_negative = True
        cleaned = cleaned[:-1]

    if cleaned.startswith("-"):
        is_negative = True
        cleaned = cleaned[1:]

    cleaned = re.sub(r"[^0-9,.-]", "", cleaned)
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    normalized = _normalize_decimal_separators(cleaned)
    if normalized is None:
        return None

    try:
        value = float(normalized)
    except ValueError:
        return None

    return -value if is_negative else value


def looks_like_receipt_amount(text: Any) -> bool:
    """Return whether text has receipt amount punctuation/symbol context."""
    if text is None:
        return False
    raw = str(text).strip()
    if not raw:
        return False
    return bool(
        re.search(f"[{re.escape(_CURRENCY_SYMBOLS)}]", raw)
        or re.fullmatch(r"-?\(?\d{1,3}(,\d{3})+(\.\d{2})?\)?-?", raw)
        or re.fullmatch(r"-?\(?\d+([.,]\d{2})\)?-?", raw)
    )


def _normalize_decimal_separators(cleaned: str) -> str | None:
    """Normalize supported comma/dot conventions to Python float syntax."""
    if "," not in cleaned:
        return cleaned

    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")

    if last_dot >= 0 and last_comma > last_dot:
        # European grouping: 1.234,56 -> 1234.56
        if re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{2}", cleaned):
            return cleaned.replace(".", "").replace(",", ".")
        return cleaned.replace(".", "").replace(",", ".")

    if last_dot >= 0:
        # US grouping: 1,234.56 -> 1234.56
        return cleaned.replace(",", "")

    if re.fullmatch(r"\d{1,3}(,\d{3})+", cleaned):
        # Thousands-only grouping: 1,234 -> 1234
        return cleaned.replace(",", "")

    if re.fullmatch(r"\d+,\d{2}", cleaned):
        # Decimal comma: 8,82 -> 8.82
        return cleaned.replace(",", ".")

    # Ambiguous comma usage; preserve historical behavior as thousands.
    return cleaned.replace(",", "")
