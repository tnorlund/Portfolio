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
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt import Receipt
    from receipt_dynamo.entities.receipt_place import ReceiptPlace
    from receipt_dynamo.entities.receipt_word import ReceiptWord
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logger = logging.getLogger(__name__)


# Regex pattern to extract monetary values from text
# Matches: $12.99, 12.99, $1,234.56, 1234.56
MONEY_PATTERN = re.compile(r"\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+\.\d{2}")

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
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                elif len(groups[2]) == 4:
                    # MM/DD/YYYY format
                    month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
                else:
                    # MM/DD/YY format - assume 2000s
                    month, day = int(groups[0]), int(groups[1])
                    year = 2000 + int(groups[2])

                return datetime(year, month, day)
            except ValueError:
                continue

    return None


@dataclass
class ReceiptSummary:
    """Computed summary of a receipt with derived monetary fields.

    This is a read-only view computed from ReceiptWordLabel records.
    It is NOT stored in DynamoDB - it's computed on-the-fly or cached.

    Attributes:
        image_id: UUID of the image containing the receipt.
        receipt_id: ID of the receipt within the image.
        merchant_name: Name of the merchant (from ReceiptPlace).
        date: Date of the receipt (parsed from DATE label).
        grand_total: Total amount (from GRAND_TOTAL label).
        subtotal: Subtotal before tax (from SUBTOTAL label).
        tax: Tax amount (from TAX label).
        tip: Tip amount (from TIP label, if present).
        item_count: Number of line items (count of LINE_TOTAL labels).
    """

    image_id: str
    receipt_id: int
    merchant_name: str | None = None
    date: datetime | None = None
    grand_total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    tip: float | None = None
    item_count: int = 0

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
        word_text_lookup: dict[tuple[int, int], str] = {}
        for word in words:
            word_text_lookup[(word.line_id, word.word_id)] = word.text

        # Extract values from labels
        grand_total: float | None = None
        subtotal: float | None = None
        tax: float | None = None
        tip: float | None = None
        date: datetime | None = None
        item_count = 0

        for label in word_labels:
            text = word_text_lookup.get((label.line_id, label.word_id), "")

            if label.label == "GRAND_TOTAL":
                amount = extract_amount(text)
                if amount is not None:
                    # Take the largest GRAND_TOTAL if multiple
                    if grand_total is None or amount > grand_total:
                        grand_total = amount

            elif label.label == "SUBTOTAL":
                amount = extract_amount(text)
                if amount is not None:
                    if subtotal is None or amount > subtotal:
                        subtotal = amount

            elif label.label == "TAX":
                amount = extract_amount(text)
                if amount is not None:
                    # Sum all TAX amounts (there may be multiple tax lines)
                    if tax is None:
                        tax = amount
                    else:
                        tax += amount

            elif label.label == "TIP":
                amount = extract_amount(text)
                if amount is not None:
                    if tip is None or amount > tip:
                        tip = amount

            elif label.label == "DATE":
                parsed = parse_date(text)
                if parsed is not None:
                    date = parsed

            elif label.label == "LINE_TOTAL":
                item_count += 1

        return cls(
            image_id=receipt.image_id,
            receipt_id=receipt.receipt_id,
            merchant_name=place.merchant_name if place else None,
            date=date,
            grand_total=grand_total,
            subtotal=subtotal,
            tax=tax,
            tip=tip,
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
        word_text_lookup: dict[tuple[int, int], str] = {}
        for word in words:
            word_text_lookup[(word.line_id, word.word_id)] = word.text

        # Extract values from labels
        grand_total: float | None = None
        subtotal: float | None = None
        tax: float | None = None
        tip: float | None = None
        date: datetime | None = None
        item_count = 0

        for label in word_labels:
            text = word_text_lookup.get((label.line_id, label.word_id), "")

            if label.label == "GRAND_TOTAL":
                amount = extract_amount(text)
                if amount is not None:
                    if grand_total is None or amount > grand_total:
                        grand_total = amount

            elif label.label == "SUBTOTAL":
                amount = extract_amount(text)
                if amount is not None:
                    if subtotal is None or amount > subtotal:
                        subtotal = amount

            elif label.label == "TAX":
                amount = extract_amount(text)
                if amount is not None:
                    if tax is None:
                        tax = amount
                    else:
                        tax += amount

            elif label.label == "TIP":
                amount = extract_amount(text)
                if amount is not None:
                    if tip is None or amount > tip:
                        tip = amount

            elif label.label == "DATE":
                parsed = parse_date(text)
                if parsed is not None:
                    date = parsed

            elif label.label == "LINE_TOTAL":
                item_count += 1

        return cls(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            date=date,
            grand_total=grand_total,
            subtotal=subtotal,
            tax=tax,
            tip=tip,
            item_count=item_count,
        )

    def to_dict(self) -> dict:
        """Convert to a dictionary for JSON serialization.

        Returns:
            Dictionary with all fields, dates as ISO strings.
        """
        return {
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "merchant_name": self.merchant_name,
            "date": self.date.isoformat() if self.date else None,
            "grand_total": self.grand_total,
            "subtotal": self.subtotal,
            "tax": self.tax,
            "tip": self.tip,
            "item_count": self.item_count,
        }

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"ReceiptSummary("
            f"image_id='{self.image_id[:8]}...', "
            f"receipt_id={self.receipt_id}, "
            f"merchant_name={self.merchant_name!r}, "
            f"grand_total={self.grand_total}, "
            f"tax={self.tax}, "
            f"item_count={self.item_count}"
            f")"
        )
