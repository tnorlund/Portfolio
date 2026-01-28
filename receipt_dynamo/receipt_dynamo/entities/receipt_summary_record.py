"""Persisted summary of receipt data for efficient querying.

This module provides a DynamoDB entity that stores pre-computed summary
fields from ReceiptWordLabel records. Queries are done by listing all
summaries via the TYPE GSI and filtering in memory for flexibility.

The summary is computed from LayoutLM labels (GRAND_TOTAL, TAX, DATE, etc.)
and stored once per receipt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from receipt_dynamo.entities.receipt_summary import (
    ReceiptSummary,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_positive_int,
)

logger = logging.getLogger(__name__)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptSummaryRecord:
    """Persisted summary of a receipt stored in DynamoDB.

    This entity stores pre-computed fields from ReceiptWordLabel records.
    Use TYPE GSI to list all summaries, then filter in memory.

    Primary Key:
        PK: IMAGE#{image_id}
        SK: RECEIPT#{receipt_id}#SUMMARY

    GSI2 (List all by type):
        GSI2PK: RECEIPT_SUMMARY
        GSI2SK: IMAGE#{image_id}#RECEIPT#{receipt_id}

    GSI4 (Receipt details pattern):
        GSI4PK: IMAGE#{image_id}#RECEIPT#{receipt_id}
        GSI4SK: 5_SUMMARY

    Attributes:
        image_id: UUID of the image containing the receipt.
        receipt_id: ID of the receipt within the image.
        merchant_name: Name of the merchant (from ReceiptPlace).
        date: Datetime of the receipt (parsed from DATE label).
        grand_total: Total amount (from GRAND_TOTAL label).
        subtotal: Subtotal before tax (from SUBTOTAL label).
        tax: Tax amount (from TAX label).
        tip: Tip amount (from TIP label, if present).
        item_count: Number of line items (count of LINE_TOTAL labels).
        timestamp_computed: When this summary was computed.
    """

    REQUIRED_KEYS = {"PK", "SK", "TYPE", "timestamp_computed"}

    # Primary identifiers (required)
    image_id: str
    receipt_id: int

    # Computed fields (optional)
    merchant_name: str | None = None
    date: datetime | None = None
    grand_total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    tip: float | None = None
    item_count: int = 0

    # Metadata
    timestamp_computed: str | datetime = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)

        # Set timestamp if not provided
        if self.timestamp_computed is None:
            self.timestamp_computed = datetime.now(timezone.utc).isoformat()
        else:
            self.timestamp_computed = validate_iso_timestamp(
                self.timestamp_computed, "timestamp_computed", default_now=True
            )

        # Validate numeric fields
        for field_name in ["grand_total", "subtotal", "tax", "tip"]:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be a number or None")

        if not isinstance(self.item_count, int) or self.item_count < 0:
            raise ValueError("item_count must be a non-negative integer")

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for this summary."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#SUMMARY"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        """Generate GSI2 key for listing all summaries by type."""
        return {
            "GSI2PK": {"S": "RECEIPT_SUMMARY"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def gsi4_key(self) -> dict[str, Any]:
        """Generate GSI4 key for receipt details access pattern."""
        return {
            "GSI4PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI4SK": {"S": "5_SUMMARY"},
        }

    def to_item(self) -> dict[str, Any]:
        """Convert to DynamoDB item format."""
        item = {
            **self.key,
            **self.gsi2_key(),
            **self.gsi4_key(),
            "TYPE": {"S": "RECEIPT_SUMMARY"},
            "timestamp_computed": {"S": self.timestamp_computed},
            "item_count": {"N": str(self.item_count)},
        }

        # Optional string fields
        if self.merchant_name:
            item["merchant_name"] = {"S": self.merchant_name}
        else:
            item["merchant_name"] = {"NULL": True}

        # Optional date field (stored as ISO string with time)
        if self.date:
            item["date"] = {"S": self.date.isoformat()}
        else:
            item["date"] = {"NULL": True}

        # Optional numeric fields
        for field_name in ["grand_total", "subtotal", "tax", "tip"]:
            value = getattr(self, field_name)
            if value is not None:
                item[field_name] = {"N": str(value)}
            else:
                item[field_name] = {"NULL": True}

        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptSummaryRecord":
        """Create from DynamoDB item."""
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing = cls.REQUIRED_KEYS - item.keys()
            raise ValueError(f"Missing required keys: {missing}")

        # Parse primary key
        image_id = item["PK"]["S"].split("#")[1]
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[1])

        # Parse optional fields
        merchant_name = None
        if "merchant_name" in item and "S" in item["merchant_name"]:
            merchant_name = item["merchant_name"]["S"]

        date = None
        if "date" in item and "S" in item["date"]:
            try:
                date = datetime.fromisoformat(item["date"]["S"])
            except ValueError:
                pass

        # Parse numeric fields
        def parse_number(field_name: str) -> float | None:
            if field_name in item and "N" in item[field_name]:
                try:
                    return float(item[field_name]["N"])
                except ValueError:
                    pass
            return None

        return cls(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            date=date,
            grand_total=parse_number("grand_total"),
            subtotal=parse_number("subtotal"),
            tax=parse_number("tax"),
            tip=parse_number("tip"),
            item_count=int(item.get("item_count", {}).get("N", "0")),
            timestamp_computed=item["timestamp_computed"]["S"],
        )

    @classmethod
    def from_summary(
        cls,
        summary: ReceiptSummary,
    ) -> "ReceiptSummaryRecord":
        """Create a persisted record from a computed summary."""
        return cls(
            image_id=summary.image_id,
            receipt_id=summary.receipt_id,
            merchant_name=summary.merchant_name,
            date=summary.date,
            grand_total=summary.grand_total,
            subtotal=summary.subtotal,
            tax=summary.tax,
            tip=summary.tip,
            item_count=summary.item_count,
        )

    def to_summary(self) -> ReceiptSummary:
        """Convert to a ReceiptSummary."""
        return ReceiptSummary(
            image_id=self.image_id,
            receipt_id=self.receipt_id,
            merchant_name=self.merchant_name,
            date=self.date,
            grand_total=self.grand_total,
            subtotal=self.subtotal,
            tax=self.tax,
            tip=self.tip,
            item_count=self.item_count,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
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
            f"ReceiptSummaryRecord("
            f"image_id={_repr_str(self.image_id[:8] + '...')}, "
            f"receipt_id={self.receipt_id}, "
            f"merchant={_repr_str(self.merchant_name)}, "
            f"date={self.date.strftime('%Y-%m-%d') if self.date else None}, "
            f"total={self.grand_total}"
            f")"
        )

    def __hash__(self) -> int:
        """Return hash value."""
        return hash((self.image_id, self.receipt_id))


def item_to_receipt_summary_record(
    item: dict[str, Any],
) -> ReceiptSummaryRecord:
    """Convert a DynamoDB item to a ReceiptSummaryRecord."""
    return ReceiptSummaryRecord.from_item(item)
