"""DynamoDB entity for one extracted receipt line item."""

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator, Optional

from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
)

_RECONCILIATION_STATUSES = {"match", "near", "mismatch", "no-baseline"}
_NAME_QUALITIES = {"ok", "low"}


def slugify_merchant(merchant_name: str) -> str:
    """Lowercase slug used in the GSI1 merchant rollup key."""
    slug = re.sub(r"[^a-z0-9]+", "-", merchant_name.lower()).strip("-")
    return slug or "unknown"


def normalize_product_text(name: str) -> str:
    """Normalize a product name for grouping in GSI1SK."""
    return (
        re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", name))
        .strip()
        .upper()
    )


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLineItem:
    """
    Represents one extracted line item of a receipt stored in DynamoDB.

    Produced by the deterministic geometric extractor (visual bands of the
    receipt's ITEMS section). One row per item, per receipt, so consumers
    (QA agent product search, merchant catalog mining) query items directly
    instead of re-deriving them from Chroma line text.

    Trust is carried on the row, not implied by its existence:
    ``reconciliation_status`` records whether the receipt's extracted item
    sum matched its summary subtotal when this item was written, and
    ``source_section_status`` / ``source_model_source`` record which ITEMS
    section produced it. Consumers default to
    ``reconciliation_status IN ("match", "near")``.

    GSI1 (merchant rollup: every observation of a product at a merchant) is
    deliberately sparse — rows with ``name_quality == "low"`` (a price with
    no recoverable product name) omit the GSI keys so junk names never
    pollute the product index, while the row still participates in
    per-receipt reads and reconciliation.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID of the image the receipt belongs to.
        item_index (int): 0-based position of the item on the receipt.
        name (str): Extracted product name ("" when name_quality is "low").
        price (str): Decimal string, e.g. "7.98"; negative for discounts.
        line_ids (list[int]): OCR line ids the item's bands span.
        extractor_version (str): e.g. "line-items-geom-v1".
        extracted_at (datetime): When the extraction ran.
        quantity (float | None): Parsed quantity, when present.
        unit_price (float | None): Parsed per-unit price, when present.
        is_discount (bool): Discount/coupon line (negative or keyword).
        raw_text (str): The band text the item was parsed from.
        name_quality (str): "ok" or "low".
        merchant_name (str | None): For the GSI1 rollup; optional.
        source_section_status (str | None): VALID / PENDING / INVALID.
        source_model_source (str | None): ITEMS section's model_source.
        reconciliation_status (str | None): match / near / mismatch /
            no-baseline at write time.
        collapsed_banding (bool): Extraction ran on degenerate banding.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "name",
        "price",
        "line_ids",
        "extractor_version",
        "extracted_at",
    }

    receipt_id: int
    image_id: str
    item_index: int
    name: str
    price: str
    line_ids: list[int]
    extractor_version: str
    extracted_at: datetime | str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    is_discount: bool = False
    raw_text: str = ""
    name_quality: str = "ok"
    merchant_name: Optional[str] = None
    source_section_status: Optional[str] = None
    source_model_source: Optional[str] = None
    reconciliation_status: Optional[str] = None
    collapsed_banding: bool = False

    def __post_init__(self):
        """Validate and normalize the ReceiptLineItem instance."""
        if not isinstance(self.receipt_id, int) or isinstance(
            self.receipt_id, bool
        ):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        assert_valid_uuid(self.image_id)

        if not isinstance(self.item_index, int) or isinstance(
            self.item_index, bool
        ):
            raise ValueError("item_index must be an integer")
        if self.item_index < 0:
            raise ValueError("item_index must be non-negative")

        if not isinstance(self.name, str):
            raise ValueError("name must be a string")

        if isinstance(self.price, (int, float)) and not isinstance(
            self.price, bool
        ):
            self.price = f"{self.price:.2f}"
        if not isinstance(self.price, str):
            raise ValueError("price must be a decimal string")
        try:
            float(self.price)
        except ValueError as exc:
            raise ValueError(
                f"price must parse as a number, got {self.price!r}"
            ) from exc

        if not isinstance(self.line_ids, list) or not self.line_ids:
            raise ValueError("line_ids must be a non-empty list")
        if not all(
            isinstance(li, int) and not isinstance(li, bool) and li >= 0
            for li in self.line_ids
        ):
            raise ValueError("line_ids must contain non-negative integers")

        if (
            not isinstance(self.extractor_version, str)
            or not self.extractor_version
        ):
            raise ValueError("extractor_version must be a non-empty string")

        for fname in ("quantity", "unit_price"):
            value = getattr(self, fname)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{fname} must be a number or None")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{fname} must be finite and non-negative")
            setattr(self, fname, float(value))

        if not isinstance(self.is_discount, bool):
            raise ValueError("is_discount must be a boolean")
        if not isinstance(self.collapsed_banding, bool):
            raise ValueError("collapsed_banding must be a boolean")
        if not isinstance(self.raw_text, str):
            raise ValueError("raw_text must be a string")

        if self.name_quality not in _NAME_QUALITIES:
            raise ValueError(
                f"name_quality must be one of {sorted(_NAME_QUALITIES)}"
            )
        if self.name_quality == "ok" and not self.name:
            raise ValueError('name is required when name_quality is "ok"')

        if (
            self.reconciliation_status is not None
            and self.reconciliation_status not in _RECONCILIATION_STATUSES
        ):
            raise ValueError(
                "reconciliation_status must be one of "
                f"{sorted(_RECONCILIATION_STATUSES)} or None"
            )
        for fname in (
            "merchant_name",
            "source_section_status",
            "source_model_source",
        ):
            value = getattr(self, fname)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{fname} must be a string or None")

        if isinstance(self.extracted_at, str):
            self.extracted_at = datetime.fromisoformat(self.extracted_at)
        elif not isinstance(self.extracted_at, datetime):
            raise ValueError("extracted_at must be a datetime or ISO string")

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for the receipt line item."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE_ITEM#{self.item_index:05d}"
                )
            },
        }

    @property
    def gsi1_key(self) -> Optional[dict[str, Any]]:
        """Sparse merchant-rollup key; None for low-quality names."""
        if self.name_quality == "low" or not self.merchant_name:
            return None
        return {
            "GSI1PK": {
                "S": f"MERCHANT#{slugify_merchant(self.merchant_name)}"
            },
            "GSI1SK": {
                "S": (
                    f"LINE_ITEM#{normalize_product_text(self.name)}#"
                    f"{self.image_id}#{self.receipt_id:05d}#"
                    f"{self.item_index:05d}"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert the ReceiptLineItem to a DynamoDB item."""
        item: dict[str, Any] = {
            **self.key,
            "TYPE": {"S": "RECEIPT_LINE_ITEM"},
            "name": {"S": self.name},
            "price": {"S": self.price},
            "line_ids": {"L": [{"N": str(li)} for li in self.line_ids]},
            "extractor_version": {"S": self.extractor_version},
            "extracted_at": {"S": self._extracted_at_iso()},
            "is_discount": {"BOOL": self.is_discount},
            "collapsed_banding": {"BOOL": self.collapsed_banding},
            "name_quality": {"S": self.name_quality},
            "raw_text": {"S": self.raw_text},
        }
        gsi = self.gsi1_key
        if gsi:
            item.update(gsi)
        if self.quantity is not None:
            item["quantity"] = {"N": str(self.quantity)}
        if self.unit_price is not None:
            item["unit_price"] = {"N": str(self.unit_price)}
        for fname in (
            "merchant_name",
            "source_section_status",
            "source_model_source",
            "reconciliation_status",
        ):
            value = getattr(self, fname)
            if value is not None:
                item[fname] = {"S": value}
        return item

    def __repr__(self) -> str:
        return (
            f"ReceiptLineItem("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"item_index={self.item_index}, "
            f"name={_repr_str(self.name)}, "
            f"price={_repr_str(self.price)}, "
            f"quantity={self.quantity}, "
            f"unit_price={self.unit_price}, "
            f"is_discount={self.is_discount}, "
            f"name_quality={_repr_str(self.name_quality)}, "
            f"reconciliation_status="
            f"{_repr_str(self.reconciliation_status)}, "
            f"extractor_version={_repr_str(self.extractor_version)}"
            f")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "item_index", self.item_index
        yield "name", self.name
        yield "price", self.price
        yield "quantity", self.quantity
        yield "unit_price", self.unit_price
        yield "is_discount", self.is_discount
        yield "raw_text", self.raw_text
        yield "line_ids", self.line_ids
        yield "name_quality", self.name_quality
        yield "merchant_name", self.merchant_name
        yield "source_section_status", self.source_section_status
        yield "source_model_source", self.source_model_source
        yield "reconciliation_status", self.reconciliation_status
        yield "collapsed_banding", self.collapsed_banding
        yield "extractor_version", self.extractor_version
        yield "extracted_at", self._extracted_at_iso()

    def __hash__(self) -> int:
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.item_index,
                self.name,
                self.price,
                tuple(self.line_ids),
                self.quantity,
                self.unit_price,
                self.is_discount,
                self.name_quality,
                self.merchant_name,
                self.reconciliation_status,
                self.extractor_version,
                self._extracted_at_iso(),
            )
        )

    def _extracted_at_iso(self) -> str:
        if not isinstance(self.extracted_at, datetime):  # pragma: no cover
            raise TypeError("extracted_at was not normalized")
        return self.extracted_at.isoformat()

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptLineItem":
        """Converts a DynamoDB item to a ReceiptLineItem object."""
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - set(item.keys())
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        try:
            pk = item["PK"]["S"]
            if not pk.startswith("IMAGE#"):
                raise ValueError(f"PK must start with IMAGE#, got {pk!r}")
            image_id = pk.split("#")[1]
            sk_parts = item["SK"]["S"].split("#")
            if (
                len(sk_parts) != 4
                or sk_parts[0] != "RECEIPT"
                or sk_parts[2] != "LINE_ITEM"
            ):
                raise ValueError(
                    "SK must have the form "
                    "RECEIPT#{id:05d}#LINE_ITEM#{index:05d}, "
                    f"got {item['SK']['S']!r}"
                )
            receipt_id = int(sk_parts[1])
            item_index = int(sk_parts[3])

            def opt_s(name: str) -> Optional[str]:
                return item[name]["S"] if name in item else None

            def opt_n(name: str) -> Optional[float]:
                return float(item[name]["N"]) if name in item else None

            return cls(
                receipt_id=receipt_id,
                image_id=image_id,
                item_index=item_index,
                name=item["name"]["S"],
                price=item["price"]["S"],
                line_ids=[int(li["N"]) for li in item["line_ids"]["L"]],
                extractor_version=item["extractor_version"]["S"],
                extracted_at=datetime.fromisoformat(item["extracted_at"]["S"]),
                quantity=opt_n("quantity"),
                unit_price=opt_n("unit_price"),
                is_discount=item.get("is_discount", {}).get("BOOL", False),
                raw_text=item.get("raw_text", {}).get("S", ""),
                name_quality=item.get("name_quality", {}).get("S", "ok"),
                merchant_name=opt_s("merchant_name"),
                source_section_status=opt_s("source_section_status"),
                source_model_source=opt_s("source_model_source"),
                reconciliation_status=opt_s("reconciliation_status"),
                collapsed_banding=item.get("collapsed_banding", {}).get(
                    "BOOL", False
                ),
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"Error converting item to ReceiptLineItem: {e}"
            ) from e


def item_to_receipt_line_item(item: dict[str, Any]) -> ReceiptLineItem:
    """Converts a DynamoDB item to a ReceiptLineItem object."""
    return ReceiptLineItem.from_item(item)
