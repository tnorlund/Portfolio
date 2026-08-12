"""Computed summary of receipt data for efficient querying.

This module provides a dataclass that aggregates derived fields from
ReceiptWordLabel records, enabling efficient answers to questions like:
- "How much did I spend at Costco?"
- "What was my total tax last month?"
- "What's my average grocery bill?"

The summary is computed from existing LayoutLM labels (GRAND_TOTAL, TAX, etc.)
without requiring an LLM.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING

from receipt_dynamo.amounts import (
    NON_PAYMENT_SUMMARY_RE,
    SUBTOTAL_KEYWORD_RE,
    TAX_KEYWORD_RE,
    TENDER_KEYWORD_RE,
    TOTAL_KEYWORD_RE,
    is_grand_total_line,
    looks_like_receipt_amount,
    parse_receipt_amount,
)
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.identifier_mixins import ReceiptIdentifierMixin
from receipt_dynamo.entities.util import validate_non_negative_int

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt import Receipt
    from receipt_dynamo.entities.receipt_place import ReceiptPlace
    from receipt_dynamo.entities.receipt_word import ReceiptWord
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logger = logging.getLogger(__name__)

# Allowed values for the optional tender / bank-match fields.
VALID_TENDER_CLASSES = frozenset({"cash", "card", "unknown"})
VALID_LEDGERS = frozenset({"chase", "apple", "none"})

_LAST4_RE = re.compile(r"^\d{4}$")


@dataclass
class MonetaryTotals:
    """Grouped monetary fields from a receipt.

    Attributes:
        grand_total: Total amount (from GRAND_TOTAL label).
        subtotal: Subtotal before tax (from SUBTOTAL label).
        tax: Tax amount (from TAX label).
        tip: Tip amount (from TIP label, if present).
    """

    grand_total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    tip: float | None = None

    def __post_init__(self) -> None:
        """Reject values that DynamoDB cannot safely store as numbers."""
        for field_name in ("grand_total", "subtotal", "tax", "tip"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be a finite number or None")
            if not isfinite(value):
                raise ValueError(f"{field_name} must be a finite number or None")
            setattr(self, field_name, float(value))

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "grand_total": self.grand_total,
            "subtotal": self.subtotal,
            "tax": self.tax,
            "tip": self.tip,
        }


# Regex pattern to extract monetary values from text
# Matches: $12.99, 12.99, $1,234.56, 1234.56, $1234, 1234
# Order matters: try comma-grouped first, then ungrouped amounts
# A trailing "-" is the accounting negative some registers print on
# refunds (Target returns print "$16.25-"); it is captured so
# extract_amount can negate, and stays optional so ordinary amounts,
# dates ("01-15-2024") and phone numbers parse exactly as before.
MONEY_PATTERN = re.compile(
    r"\$?\d{1,3}(?:,\d{3})+(?:\.\d{2})?-?"  # Comma-grouped: $1,234.56
    r"|\$?\d+(?:\.\d{2})?-?"  # Ungrouped: $1234.56, 1234, $50, 16.25-
)

# Regex patterns for date parsing
DATE_PATTERNS = [
    # MM/DD/YYYY or MM-DD-YYYY
    re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"),
    # MM/DD/YY or MM-DD-YY
    re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2})"),
    # YYYY-MM-DD (ISO format)
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
]

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Month-name dates ("JUL 25, 2026", "25 Jul '26", "July 25 2026").
# Receipts print these at least as often as numeric forms, and they
# arrive split across multiple OCR words — parse_date accepts joined
# line text as well as single words.
_MONTH_NAME_PATTERNS = [
    # Month first: JUL 25, 2026 / July 25 '26
    re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?,?\s+'?(\d{4}|\d{2})\b",
        re.IGNORECASE,
    ),
    # Day first: 25 JUL 2026 / 25 July '26
    re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\.?\s+"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+"
        r"'?(\d{4}|\d{2})\b",
        re.IGNORECASE,
    ),
]


def _expand_year(value: int) -> int:
    """Two-digit years follow the same 00-49/50-99 window as numeric dates."""
    if value >= 100:
        return value
    return 2000 + value if value < 50 else 1900 + value


def extract_amount(text: str) -> float | None:
    """Extract a monetary amount from text.

    Args:
        text: Text that may contain a price (e.g., "$12.99", "TOTAL 45.67")

    Returns:
        The extracted amount as a float, or None if no amount found.
    """
    if not text:
        return None

    matches = MONEY_PATTERN.findall(text)
    if not matches:
        return None

    # Take the last match (usually the actual amount, not a product code)
    amount_str = matches[-1]
    # Trailing "-" is the accounting negative refund registers print
    # ("$16.25-"); strip it and negate so refunds carry their sign into
    # the summary financial math instead of parsing as positive spend.
    is_negative = amount_str.endswith("-")
    if is_negative:
        amount_str = amount_str[:-1]
    # Remove $ and commas
    amount_str = amount_str.replace("$", "").replace(",", "")
    try:
        value = float(amount_str)
    except ValueError:
        return None
    return -value if is_negative else value


def parse_date(text: str) -> datetime | None:
    """Parse a date from text.

    Args:
        text: Text that may contain a date (e.g., "01/15/2024", "2024-01-15")

    Returns:
        The parsed datetime, or None if no valid date found.
    """
    if not text:
        return None

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:
                    # YYYY-MM-DD format
                    year, month, day = (
                        int(groups[0]),
                        int(groups[1]),
                        int(groups[2]),
                    )
                elif len(groups[2]) == 4:
                    # MM/DD/YYYY format
                    month, day, year = (
                        int(groups[0]),
                        int(groups[1]),
                        int(groups[2]),
                    )
                else:
                    # MM/DD/YY format - use sliding window approach
                    # 00-49 -> 2000-2049, 50-99 -> 1950-1999
                    month, day = int(groups[0]), int(groups[1])
                    two_digit_year = int(groups[2])
                    year = (
                        2000 + two_digit_year
                        if two_digit_year < 50
                        else 1900 + two_digit_year
                    )

                return datetime(year, month, day)
            except ValueError:
                continue

    for pattern in _MONTH_NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        try:
            if groups[0].isdigit():
                day = int(groups[0])
                month = _MONTHS[groups[1][:3].lower()]
            else:
                month = _MONTHS[groups[0][:3].lower()]
                day = int(groups[1])
            year = _expand_year(int(groups[2]))
            return datetime(year, month, day)
        except (ValueError, KeyError):
            continue

    return None


# =============================================================================
# Label extraction with dispatch pattern (fixes R0912: too many branches)
# =============================================================================


@dataclass
class _ExtractionState:
    """Mutable state for label extraction."""

    grand_total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    tip: float | None = None
    date: datetime | None = None
    item_count: int = 0


def _handle_grand_total(text: str, state: _ExtractionState) -> None:
    """Handle GRAND_TOTAL label - take the largest value."""
    amount = extract_amount(text)
    if amount is not None:
        if state.grand_total is None or amount > state.grand_total:
            state.grand_total = amount


def _handle_subtotal(text: str, state: _ExtractionState) -> None:
    """Handle SUBTOTAL label - take the largest value."""
    amount = extract_amount(text)
    if amount is not None:
        if state.subtotal is None or amount > state.subtotal:
            state.subtotal = amount


def _handle_tax(text: str, state: _ExtractionState) -> None:
    """Handle TAX label - sum all values."""
    amount = extract_amount(text)
    if amount is not None:
        state.tax = (state.tax or 0) + amount


def _handle_tip(text: str, state: _ExtractionState) -> None:
    """Handle TIP label - take the largest value."""
    amount = extract_amount(text)
    if amount is not None:
        if state.tip is None or amount > state.tip:
            state.tip = amount


def _handle_date(text: str, state: _ExtractionState) -> None:
    """Handle DATE label - take the last valid date."""
    parsed = parse_date(text)
    if parsed is not None:
        state.date = parsed


def _handle_line_total(_text: str, state: _ExtractionState) -> None:
    """Handle LINE_TOTAL label - count occurrences."""
    state.item_count += 1


# Type alias for handler functions
_HandlerFunc = Callable[[str, _ExtractionState], None]

# Dispatch table mapping label types to handler functions
_LABEL_HANDLERS: dict[str, _HandlerFunc] = {
    "GRAND_TOTAL": _handle_grand_total,
    "SUBTOTAL": _handle_subtotal,
    "TAX": _handle_tax,
    "TIP": _handle_tip,
    "DATE": _handle_date,
    "LINE_TOTAL": _handle_line_total,
}


def _extract_summary_fields(
    word_labels: list["ReceiptWordLabel"],
    word_text_lookup: dict[tuple[int, int], str],
) -> tuple[MonetaryTotals, datetime | None, int]:
    """Extract summary fields from word labels using dispatch pattern.

    Args:
        word_labels: List of ReceiptWordLabel records.
        word_text_lookup: Mapping from (line_id, word_id) to word text.

    Returns:
        Tuple of (totals, date, item_count).
    """
    state = _ExtractionState()
    date_words_by_line: dict[int, list[tuple[int, str]]] = {}

    for label in word_labels:
        # Skip labels that didn't pass validation — they're usually OCR
        # misreads or LLM mislabels that the evaluator already rejected.
        if label.validation_status != ValidationStatus.VALID.value:
            continue
        handler = _LABEL_HANDLERS.get(label.label)
        if handler:
            text = word_text_lookup.get((label.line_id, label.word_id), "")
            handler(text, state)
            if label.label == "DATE":
                date_words_by_line.setdefault(label.line_id, []).append(
                    (label.word_id, text)
                )

    # OCR splits dates across words ("JUL" "25" "2026"), so no single
    # word parses on its own. When per-word parsing found nothing, join
    # each line's DATE words in word order and parse the whole phrase.
    if state.date is None:
        for _line_id, entries in sorted(date_words_by_line.items()):
            joined = " ".join(t for _, t in sorted(entries) if t)
            parsed = parse_date(joined)
            if parsed is not None:
                state.date = parsed
                break

    totals = MonetaryTotals(
        grand_total=state.grand_total,
        subtotal=state.subtotal,
        tax=state.tax,
        tip=state.tip,
    )
    return totals, state.date, state.item_count


# =============================================================================
# Label-independent printed-total fallback
# =============================================================================

# Minimum same-row y tolerance (normalized units). Receipt OCR splits a
# summary row into separate "lines" per column ("Total:" on one line,
# "USD$ 42.54" on another), so row membership is decided by y-band overlap
# rather than line_id adjacency.
_MIN_ROW_BAND = 0.005


def _word_y_center(word: "ReceiptWord") -> float | None:
    """Return the normalized y-center of a word, or None without geometry."""
    box = getattr(word, "bounding_box", None)
    if not box:
        return None
    y = box.get("y")
    if y is None:
        return None
    return y + (box.get("height") or 0.0) / 2.0


def _word_height(word: "ReceiptWord") -> float:
    """Return the normalized height of a word (0.0 without geometry)."""
    box = getattr(word, "bounding_box", None)
    if not box:
        return 0.0
    return box.get("height") or 0.0


def _positive_amount(text: str) -> float | None:
    """Parse text as a positive printed amount, else None.

    Requires amount-like punctuation (decimals or a currency symbol) so
    bare integers such as store numbers never qualify.
    """
    if not looks_like_receipt_amount(text):
        return None
    value = parse_receipt_amount(text)
    if value is None or value <= 0:
        return None
    return value


_CURRENCY_PREFIX_RE = re.compile(r"(?:[A-Za-z]{2,3})?\$+S?", re.I)
_ISO_CURRENCY_PREFIX_RE = re.compile(r"(?:USD|EUR|GBP|CAD|AUD|JPY|MXN|CHF)S?", re.I)


def _is_currency_prefix_token(text: str) -> bool:
    """OCR currency wrapper with no digits (``USD$``, ``USD$S``, ``US$``)."""
    compact = re.sub(r"[\s:]", "", str(text).strip())
    if not compact or re.search(r"\d", compact):
        return False
    return bool(
        _CURRENCY_PREFIX_RE.fullmatch(compact)
        or _ISO_CURRENCY_PREFIX_RE.fullmatch(compact)
    )


def _amount_words_on_line(
    line_words: list["ReceiptWord"],
) -> list[tuple[float, "ReceiptWord"]]:
    """Positive amounts on a line, including split currency+number tokens.

    Vision OCR often emits ``USD$S`` and ``7.43`` as adjacent words.
    The numeric word is the one returned so callers can point a label
    at the printed figure.
    """
    found: list[tuple[float, "ReceiptWord"]] = []
    texts = [str(getattr(w, "text", "") or "") for w in line_words]
    seen: set[int] = set()
    for i, word in enumerate(line_words):
        if i in seen:
            continue
        amount = _positive_amount(texts[i])
        if amount is not None:
            found.append((amount, word))
            seen.add(i)
            continue
        if i + 1 < len(line_words) and _is_currency_prefix_token(texts[i]):
            joined = f"{texts[i]} {texts[i + 1]}"
            amount = _positive_amount(joined)
            if amount is not None:
                found.append((amount, line_words[i + 1]))
                seen.add(i)
                seen.add(i + 1)
    return found


def _positive_amounts_on_line_ids(
    words: list["ReceiptWord"], line_ids: Collection[int]
) -> list[float]:
    """Positive amounts printed on the given line ids (no y-band pairing)."""
    wanted = {int(x) for x in line_ids}
    lines: dict[int, list["ReceiptWord"]] = {}
    for word in words:
        line_id = getattr(word, "line_id", None)
        if line_id is None or int(line_id) not in wanted:
            continue
        lines.setdefault(int(line_id), []).append(word)
    amounts: list[float] = []
    for line_words in lines.values():
        line_words.sort(key=lambda w: getattr(w, "word_id", 0))
        amounts.extend(amount for amount, _ in _amount_words_on_line(line_words))
    return amounts


def _is_summary_noise_line(line_text: str) -> bool:
    """Return whether a line is a subtotal/tax/tender/non-payment row."""
    return bool(
        SUBTOTAL_KEYWORD_RE.search(line_text)
        or TAX_KEYWORD_RE.search(line_text)
        or NON_PAYMENT_SUMMARY_RE.search(line_text)
        or TENDER_KEYWORD_RE.search(line_text)
    )


def _is_grand_total_anchor(line_text: str) -> bool:
    """A grand-total anchor row: a total row that is not a tender row.

    Tender/settlement rows ("Total Tender", "Amount Tendered", "Cash",
    "Change") record how the customer paid -- tender can include tip --
    so a plain "Total" row must always outrank them. Moody Market
    (a8d7ab9f r4) printed "Total 21.45" and "Total Tender 24.67"
    (total + tips); anchoring on the tender row broke reconciliation.
    """
    return bool(
        is_grand_total_line(line_text) and not TENDER_KEYWORD_RE.search(line_text)
    )


def _is_subtotal_anchor(line_text: str) -> bool:
    """A subtotal anchor row (never a savings/tender variant)."""
    return bool(
        SUBTOTAL_KEYWORD_RE.search(line_text)
        and not NON_PAYMENT_SUMMARY_RE.search(line_text)
        and not TENDER_KEYWORD_RE.search(line_text)
    )


def _is_subtotal_noise_line(line_text: str) -> bool:
    """Rows a subtotal anchor must not pair with across the y-band."""
    return bool(
        TOTAL_KEYWORD_RE.search(line_text)
        or TAX_KEYWORD_RE.search(line_text)
        or NON_PAYMENT_SUMMARY_RE.search(line_text)
        or TENDER_KEYWORD_RE.search(line_text)
    )


def _is_tax_anchor(line_text: str) -> bool:
    """A tax anchor row (never a total/subtotal/tender variant)."""
    return bool(
        TAX_KEYWORD_RE.search(line_text)
        and not TOTAL_KEYWORD_RE.search(line_text)
        and not SUBTOTAL_KEYWORD_RE.search(line_text)
        and not NON_PAYMENT_SUMMARY_RE.search(line_text)
        and not TENDER_KEYWORD_RE.search(line_text)
    )


def _is_tax_noise_line(line_text: str) -> bool:
    """Rows a tax anchor must not pair with across the y-band."""
    return bool(
        TOTAL_KEYWORD_RE.search(line_text)
        or SUBTOTAL_KEYWORD_RE.search(line_text)
        or NON_PAYMENT_SUMMARY_RE.search(line_text)
        or TENDER_KEYWORD_RE.search(line_text)
    )


def find_printed_grand_total(
    words: list["ReceiptWord"],
    *,
    total_line_ids: Collection[int] | None = None,
) -> float | None:
    """Find the printed grand total from receipt words, without labels.

    Deterministic fallback for receipts whose GRAND_TOTAL labels are
    missing or attached to the wrong words (the evaluator then rejects
    them and the summary is left with no total even though one is
    printed). Sprouts is the canonical case: it prints "Total:" /
    "BALANCE DUE" and "USD$ 42.54" as separate OCR lines in the same
    visual row.

    Strategy:
    1. Find anchor lines whose joined text reads as a grand-total row
       (shared ``is_grand_total_line`` keywords) and is not a
       tender/settlement row (``_is_grand_total_anchor``).
    2. When the caller has already assigned a TOTAL_LINE section, those
       line ids are extra anchors (post-join, not a new section finder).
       A TOTAL_LINE row that is only ``USD$S 7.43`` still counts even
       when the "Total:" keyword OCR-failed.
    3. Take amounts printed on the anchor line itself, including split
       currency-prefix + figure tokens; otherwise pair the anchor with
       amount words on other lines whose y-center falls in the anchor's
       row band (skipping subtotal/tax/tender/savings rows).
    4. Return the largest anchored amount, mirroring the GRAND_TOTAL
       label handler's largest-value semantics.

    Args:
        words: ReceiptWord records (text + normalized bounding boxes).
        total_line_ids: Optional TOTAL_LINE section line ids.

    Returns:
        The printed grand total, or None when no anchored amount exists.
    """
    return _find_anchored_amount(
        words,
        _is_grand_total_anchor,
        _is_summary_noise_line,
        extra_anchor_ids=total_line_ids,
    )


def find_printed_subtotal(words: list["ReceiptWord"]) -> float | None:
    """Find the printed subtotal from receipt words, without labels.

    Same anchored-row strategy as :func:`find_printed_grand_total`,
    anchored on subtotal rows and skipping total/tax/tender/savings
    rows when pairing across the y-band.
    """
    return _find_anchored_amount(words, _is_subtotal_anchor, _is_subtotal_noise_line)


def find_printed_tax_words(
    words: list["ReceiptWord"],
) -> list[tuple[float, "ReceiptWord"]]:
    """Every amount word anchored to a printed tax row.

    Unlike grand total and subtotal there is no single "the" printed tax
    (receipts print several tax rows that the summary sums), so this
    returns all anchored candidates and leaves the choice to the caller.
    """
    return _anchored_amount_words(words, _is_tax_anchor, _is_tax_noise_line)


def find_printed_grand_total_words(
    words: list["ReceiptWord"],
    *,
    total_line_ids: Collection[int] | None = None,
) -> list[tuple[float, "ReceiptWord"]]:
    """Amount words anchored to a printed grand-total row.

    The word-level companion to :func:`find_printed_grand_total`, which
    returns only the winning value. Callers that need to point a label at
    the printed figure need the word it was printed on.
    """
    return _anchored_amount_words(
        words,
        _is_grand_total_anchor,
        _is_summary_noise_line,
        extra_anchor_ids=total_line_ids,
    )


def find_printed_subtotal_words(
    words: list["ReceiptWord"],
) -> list[tuple[float, "ReceiptWord"]]:
    """Amount words anchored to a printed subtotal row."""
    return _anchored_amount_words(words, _is_subtotal_anchor, _is_subtotal_noise_line)


def _find_anchored_amount(
    words: list["ReceiptWord"],
    is_anchor: Callable[[str], bool],
    is_noise: Callable[[str], bool],
    extra_anchor_ids: Collection[int] | None = None,
) -> float | None:
    """Largest amount printed on (or row-banded with) an anchor line."""
    anchored = _anchored_amount_words(
        words, is_anchor, is_noise, extra_anchor_ids=extra_anchor_ids
    )
    return max(amount for amount, _ in anchored) if anchored else None


def _anchored_amount_words(
    words: list["ReceiptWord"],
    is_anchor: Callable[[str], bool],
    is_noise: Callable[[str], bool],
    extra_anchor_ids: Collection[int] | None = None,
) -> list[tuple[float, "ReceiptWord"]]:
    """Amounts printed on (or row-banded with) an anchor line, with words."""
    lines: dict[int, list["ReceiptWord"]] = {}
    for word in words:
        line_id = getattr(word, "line_id", None)
        if line_id is None:
            continue
        lines.setdefault(line_id, []).append(word)
    for line_words in lines.values():
        line_words.sort(key=lambda w: getattr(w, "word_id", 0))
    line_texts = {
        line_id: " ".join(str(getattr(w, "text", "")) for w in line_words)
        for line_id, line_words in lines.items()
    }

    anchor_ids = [line_id for line_id, text in line_texts.items() if is_anchor(text)]
    extra = {int(x) for x in (extra_anchor_ids or [])}
    for line_id in extra:
        if line_id not in lines or line_id in anchor_ids:
            continue
        if is_noise(line_texts[line_id]):
            continue
        anchor_ids.append(line_id)
    if not anchor_ids:
        return []

    anchored: list[tuple[float, "ReceiptWord"]] = []
    for anchor_id in anchor_ids:
        anchor_words = lines[anchor_id]

        # Amounts printed on the anchor line itself win outright.
        same_line = _amount_words_on_line(anchor_words)
        if same_line:
            anchored.extend(same_line)
            continue

        # Pair with amount words in the anchor's y-band on other lines.
        centers = [c for w in anchor_words if (c := _word_y_center(w)) is not None]
        if not centers:
            continue
        anchor_y = sum(centers) / len(centers)
        anchor_height = max(_word_height(w) for w in anchor_words)
        band = max(0.6 * anchor_height, _MIN_ROW_BAND)

        for line_id, line_words in lines.items():
            if line_id == anchor_id:
                continue
            if is_noise(line_texts[line_id]):
                continue
            for amount, w in _amount_words_on_line(line_words):
                center = _word_y_center(w)
                if center is None or abs(center - anchor_y) > band:
                    continue
                anchored.append((amount, w))

    return anchored


def _apply_printed_total_fallback(
    totals: MonetaryTotals,
    words: list["ReceiptWord"],
    total_line_ids: Collection[int] | None = None,
) -> None:
    """Fill grand_total/subtotal from printed rows when labels gave none.

    A NEGATIVE label-derived total is a real figure, not a missing one:
    return receipts print trailing-minus amounts ("$16.25-") and
    extract_amount now carries the sign through. The fallback only knows
    positive printed amounts, so letting it run on negatives would
    replace a refund's total with whatever stray positive figure sits in
    the total row's y-band. Only None/zero totals fall back — except
    when a TOTAL_LINE section is present and the label amount is not
    printed on those lines (a VALID GRAND_TOTAL on the tax figure).
    """
    printed = find_printed_grand_total(words, total_line_ids=total_line_ids)
    if printed is not None:
        if totals.grand_total is None or totals.grand_total == 0:
            totals.grand_total = printed
        elif total_line_ids:
            on_section = _positive_amounts_on_line_ids(words, total_line_ids)
            label_on_section = any(
                abs(amount - totals.grand_total) < 0.005 for amount in on_section
            )
            if not label_on_section:
                totals.grand_total = printed
    if totals.subtotal is None or totals.subtotal == 0:
        printed_sub = find_printed_subtotal(words)
        if printed_sub is not None:
            totals.subtotal = printed_sub


# =============================================================================
# item_count resolution
# =============================================================================


def resolve_item_count(
    label_item_count: int,
    line_item_count: int | None,
) -> int:
    """Pick the authoritative item count for a receipt summary.

    ``item_count`` has two possible sources and they do not agree:

    * ``ReceiptLineItem`` rows -- the real extracted line items (name,
      price, line_ids), written by the line-item updater's band-block
      decoder. This is the authoritative count when rows exist.
    * VALID ``LINE_TOTAL`` word labels -- the legacy source. Receipts
      ingested through the current pipeline never receive ``LINE_TOTAL``
      labels at all, which is why freshly-ingested receipts holding real
      line items still reported ``item_count == 0``.

    The label count is kept as the fallback rather than dropped: a
    receipt can carry ``LINE_TOTAL`` labels while the line-item extractor
    produced no rows (no ITEMS zone, decode declined). Preferring rows
    only when they exist raises the fresh-ingest receipts to a correct
    count without regressing those to zero.

    Args:
        label_item_count: Count of VALID ``LINE_TOTAL`` word labels.
        line_item_count: Count of ``ReceiptLineItem`` rows, or ``None``
            when the caller did not look them up (in which case the
            legacy label count is used unchanged).

    Returns:
        The item count to store on the summary.
    """
    if line_item_count:
        return line_item_count
    return label_item_count


# =============================================================================
# ReceiptSummary dataclass
# =============================================================================


@dataclass
class ReceiptSummary(ReceiptIdentifierMixin):
    """Computed summary of a receipt with derived monetary fields.

    This is a read-only view computed from ReceiptWordLabel records.
    It is NOT stored in DynamoDB - it's computed on-the-fly or cached.

    Attributes:
        image_id: UUID of the image containing the receipt.
        receipt_id: ID of the receipt within the image.
        merchant_name: Name of the merchant (from ReceiptPlace).
        date: Date of the receipt (parsed from DATE label).
        totals: Grouped monetary totals (grand_total, subtotal, tax, tip).
        item_count: Number of line items on the receipt. Preferred source
            is the receipt's extracted ``ReceiptLineItem`` rows; the count
            of VALID ``LINE_TOTAL`` word labels is the fallback for
            receipts the line-item extractor has not produced rows for.
            See :func:`resolve_item_count`.
        tender_class: How the receipt was paid -- ``cash``, ``card`` or
            ``unknown`` (None when tender was never classified).
        card_network: Card network printed on the receipt
            (e.g. ``VISA``, ``MASTERCARD``), when card-tendered.
        card_last4: Last four PAN digits printed on the receipt.
        ledger: Which bank ledger this receipt's card belongs to --
            ``chase``, ``apple`` or ``none`` (None when unknown).
        bank_amount: Settled amount from the matched bank transaction.
        bank_match_confidence: Confidence of the bank match in [0, 1].
    """

    image_id: str
    receipt_id: int
    merchant_name: str | None = None
    date: datetime | None = None
    totals: MonetaryTotals = field(default_factory=MonetaryTotals)
    item_count: int = 0
    tender_class: str | None = None
    card_network: str | None = None
    card_last4: str | None = None
    ledger: str | None = None
    bank_amount: float | None = None
    bank_match_confidence: float | None = None

    def __post_init__(self) -> None:
        """Validate identifiers and computed summary fields."""
        self._validate_receipt_identifiers()
        if self.merchant_name is not None and not isinstance(self.merchant_name, str):
            raise ValueError("merchant_name must be a string or None")
        if self.date is not None and not isinstance(self.date, datetime):
            raise ValueError("date must be a datetime or None")
        if not isinstance(self.totals, MonetaryTotals):
            raise ValueError("totals must be a MonetaryTotals object")
        validate_non_negative_int("item_count", self.item_count)
        self._validate_tender_fields()

    def _validate_tender_fields(self) -> None:
        """Validate the optional tender / bank-match fields."""
        if (
            self.tender_class is not None
            and self.tender_class not in VALID_TENDER_CLASSES
        ):
            raise ValueError(
                "tender_class must be one of " f"{sorted(VALID_TENDER_CLASSES)} or None"
            )
        if self.card_network is not None and not isinstance(self.card_network, str):
            raise ValueError("card_network must be a string or None")
        if self.card_last4 is not None and (
            not isinstance(self.card_last4, str) or not _LAST4_RE.match(self.card_last4)
        ):
            raise ValueError("card_last4 must be a 4-digit string or None")
        if self.ledger is not None and self.ledger not in VALID_LEDGERS:
            raise ValueError(f"ledger must be one of {sorted(VALID_LEDGERS)} or None")
        for field_name in ("bank_amount", "bank_match_confidence"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be a finite number or None")
            if not isfinite(value):
                raise ValueError(f"{field_name} must be a finite number or None")
            setattr(self, field_name, float(value))
        if self.bank_match_confidence is not None and not (
            0.0 <= self.bank_match_confidence <= 1.0
        ):
            raise ValueError("bank_match_confidence must be within [0, 1] or None")

    # Convenience properties for backwards compatibility
    @property
    def grand_total(self) -> float | None:
        """Get grand total from totals."""
        return self.totals.grand_total

    @property
    def subtotal(self) -> float | None:
        """Get subtotal from totals."""
        return self.totals.subtotal

    @property
    def tax(self) -> float | None:
        """Get tax from totals."""
        return self.totals.tax

    @property
    def tip(self) -> float | None:
        """Get tip from totals."""
        return self.totals.tip

    @property
    def key(self) -> str:
        """Get the composite key for this receipt summary."""
        return f"{self.image_id}_{self.receipt_id}"

    @classmethod
    def from_receipt_data(
        cls,
        receipt: "Receipt",
        place: "ReceiptPlace | None",
        word_labels: list["ReceiptWordLabel"],
        words: list["ReceiptWord"],
        *,
        line_item_count: int | None = None,
        total_line_ids: Collection[int] | None = None,
    ) -> "ReceiptSummary":
        """Compute a summary from receipt data.

        Args:
            receipt: The Receipt entity.
            place: The ReceiptPlace entity (may be None if not matched).
            word_labels: List of ReceiptWordLabel records for this receipt.
            words: List of ReceiptWord records for this receipt.
            line_item_count: Number of ``ReceiptLineItem`` rows the
                receipt currently holds. Preferred over the LINE_TOTAL
                label count when non-zero (see :func:`resolve_item_count`).
            total_line_ids: Optional TOTAL_LINE section line ids used as
                extra printed-total anchors.

        Returns:
            A ReceiptSummary with computed fields.
        """
        # Build a lookup from (line_id, word_id) -> word text
        word_text_lookup: dict[tuple[int, int], str] = {
            (word.line_id, word.word_id): word.text for word in words
        }

        # Extract values from labels
        totals, date, item_count = _extract_summary_fields(
            word_labels, word_text_lookup
        )
        _apply_printed_total_fallback(totals, words, total_line_ids=total_line_ids)

        return cls(
            image_id=receipt.image_id,
            receipt_id=receipt.receipt_id,
            merchant_name=place.merchant_name if place else None,
            date=date,
            totals=totals,
            item_count=resolve_item_count(item_count, line_item_count),
        )

    @classmethod
    def from_word_labels_and_words(
        cls,
        image_id: str,
        receipt_id: int,
        merchant_name: str | None,
        word_labels: list["ReceiptWordLabel"],
        words: list["ReceiptWord"],
        *,
        tender_class: str | None = None,
        card_network: str | None = None,
        card_last4: str | None = None,
        ledger: str | None = None,
        bank_amount: float | None = None,
        bank_match_confidence: float | None = None,
        line_item_count: int | None = None,
        total_line_ids: Collection[int] | None = None,
    ) -> "ReceiptSummary":
        """Compute a summary from word labels and words directly.

        This is a convenience method when you don't have Receipt/ReceiptPlace
        entities but have the raw data.

        Args:
            image_id: UUID of the image.
            receipt_id: ID of the receipt.
            merchant_name: Merchant name (if known).
            word_labels: List of ReceiptWordLabel records.
            words: List of ReceiptWord records.
            tender_class: Optional tender class (cash/card/unknown).
            card_network: Optional card network.
            card_last4: Optional last four PAN digits.
            ledger: Optional bank ledger (chase/apple/none).
            bank_amount: Optional matched bank transaction amount.
            bank_match_confidence: Optional match confidence in [0, 1].
            line_item_count: Number of ``ReceiptLineItem`` rows the
                receipt currently holds. Preferred over the LINE_TOTAL
                label count when non-zero (see :func:`resolve_item_count`).
            total_line_ids: Optional TOTAL_LINE section line ids used as
                extra printed-total anchors.

        Returns:
            A ReceiptSummary with computed fields.
        """
        # Build a lookup from (line_id, word_id) -> word text
        word_text_lookup: dict[tuple[int, int], str] = {
            (word.line_id, word.word_id): word.text for word in words
        }

        # Extract values from labels
        totals, date, item_count = _extract_summary_fields(
            word_labels, word_text_lookup
        )
        _apply_printed_total_fallback(totals, words, total_line_ids=total_line_ids)

        return cls(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            date=date,
            totals=totals,
            item_count=resolve_item_count(item_count, line_item_count),
            tender_class=tender_class,
            card_network=card_network,
            card_last4=card_last4,
            ledger=ledger,
            bank_amount=bank_amount,
            bank_match_confidence=bank_match_confidence,
        )

    def to_dict(self) -> dict:
        """Convert to a dictionary for JSON serialization.

        Returns:
            Dictionary with all fields, dates as ISO strings.
        """
        result = {
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "merchant_name": self.merchant_name,
            "date": self.date.isoformat() if self.date else None,
            "item_count": self.item_count,
            "tender_class": self.tender_class,
            "card_network": self.card_network,
            "card_last4": self.card_last4,
            "ledger": self.ledger,
            "bank_amount": self.bank_amount,
            "bank_match_confidence": self.bank_match_confidence,
        }
        result.update(self.totals.to_dict())
        return result

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"ReceiptSummary("
            f"image_id='{self.image_id[:8]}...', "
            f"receipt_id={self.receipt_id}, "
            f"merchant={self.merchant_name!r}, "
            f"total={self.grand_total}, "
            f"tax={self.tax}, "
            f"items={self.item_count}"
            f")"
        )
