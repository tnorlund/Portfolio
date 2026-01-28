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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from receipt_dynamo.entities.identifier_mixins import ReceiptIdentifierMixin

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt import Receipt
    from receipt_dynamo.entities.receipt_place import ReceiptPlace
    from receipt_dynamo.entities.receipt_word import ReceiptWord
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logger = logging.getLogger(__name__)


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
MONEY_PATTERN = re.compile(
    r"\$?\d{1,3}(?:,\d{3})+(?:\.\d{2})?"  # Comma-grouped: $1,234.56
    r"|\$?\d+(?:\.\d{2})?"  # Ungrouped: $1234.56, 1234, $50
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
    # Remove $ and commas
    amount_str = amount_str.replace("$", "").replace(",", "")
    try:
        return float(amount_str)
    except ValueError:
        return None


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

    for label in word_labels:
        handler = _LABEL_HANDLERS.get(label.label)
        if handler:
            text = word_text_lookup.get((label.line_id, label.word_id), "")
            handler(text, state)

    totals = MonetaryTotals(
        grand_total=state.grand_total,
        subtotal=state.subtotal,
        tax=state.tax,
        tip=state.tip,
    )
    return totals, state.date, state.item_count


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
        item_count: Number of line items (count of LINE_TOTAL labels).
    """

    image_id: str
    receipt_id: int
    merchant_name: str | None = None
    date: datetime | None = None
    totals: MonetaryTotals = field(default_factory=MonetaryTotals)
    item_count: int = 0

    def __post_init__(self) -> None:
        """Validate identifiers."""
        self._validate_receipt_identifiers()

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
    ) -> "ReceiptSummary":
        """Compute a summary from receipt data.

        Args:
            receipt: The Receipt entity.
            place: The ReceiptPlace entity (may be None if not matched).
            word_labels: List of ReceiptWordLabel records for this receipt.
            words: List of ReceiptWord records for this receipt.

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

        return cls(
            image_id=receipt.image_id,
            receipt_id=receipt.receipt_id,
            merchant_name=place.merchant_name if place else None,
            date=date,
            totals=totals,
            item_count=item_count,
        )

    @classmethod
    def from_word_labels_and_words(
        cls,
        image_id: str,
        receipt_id: int,
        merchant_name: str | None,
        word_labels: list["ReceiptWordLabel"],
        words: list["ReceiptWord"],
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

        return cls(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            date=date,
            totals=totals,
            item_count=item_count,
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
