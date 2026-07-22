"""Per-merchant observed item catalog (MerchantCatalogItem).

Catalog items are MINED from the merchant's own labeled receipts — there is
no curated/online-catalog source (owner decision: observed miner only). The
mined rows live in the ``MERCHANT_CATALOG#<slug>`` authoring partition that
the merchant-truth migration reads to build the ``catalog_snapshot``
component, so every item must carry full receipt provenance
(``source_receipt_keys``): the sealed snapshot hash can then be recomputed
and checked against this partition at any time.

Key:
    PK = MERCHANT_CATALOG#<slug>
    SK = ITEM#<category>#<normalized_product_text>
    TYPE = MERCHANT_CATALOG_ITEM  (list-all via the GSITYPE index)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from receipt_dynamo.entities.base import DynamoDBEntity


def slugify_merchant(name: str) -> str:
    """Merchant name -> stable slug.

    Must match ``receipt_dynamo.migrations.merchant_truth_v1.slugify_merchant``
    exactly — the migration derives the catalog partition key from it.
    """
    return "".join(
        character if character.isalnum() else "_"
        for character in name.strip().lower()
    ).strip("_")


def normalize_product_text(text: str) -> str:
    """Uppercase + collapse whitespace so the same item de-dupes in the SK."""
    return " ".join(text.upper().split())


@dataclass(eq=True)
class MerchantCatalogItem(DynamoDBEntity):
    """One observed catalog item for a merchant.

    Attributes:
        merchant_name: Canonical merchant name as stored on receipts.
        product_text: Product text exactly as observed on the receipt row.
        price: Decimal-as-string, e.g. ``"1.99"`` (mode across observations).
        category: Section category (PRODUCE / DAIRY / ... / UNCATEGORIZED).
        taxable: Whether the item is taxable.
        source: Always ``"observed"`` — the catalog is mined-only.
        observed_count: How many times the item was observed (>= 1).
        upc: Optional UPC when a barcode was decoded for the item.
        source_receipt_keys: Non-empty provenance list of
            ``<image_id>#<receipt_id:05d>`` keys the item was mined from.
        last_updated: ISO-8601 timestamp of the last ingest write.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "merchant_name",
        "product_text",
        "price",
        "category",
        "taxable",
        "source",
        "observed_count",
        "source_receipt_keys",
        "last_updated",
    }

    merchant_name: str
    product_text: str
    price: str
    category: str
    taxable: bool
    source: str = "observed"
    observed_count: int = 1
    upc: str | None = None
    source_receipt_keys: list[str] = field(default_factory=list)
    last_updated: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.merchant_name, str) or not self.merchant_name:
            raise ValueError("merchant_name must be a non-empty string")
        if not slugify_merchant(self.merchant_name):
            raise ValueError("merchant_name must produce a non-empty slug")
        if not isinstance(self.product_text, str) or not self.product_text:
            raise ValueError("product_text must be a non-empty string")
        try:
            Decimal(str(self.price))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("price must be a decimal string") from exc
        self.price = str(self.price)
        if not isinstance(self.category, str) or not self.category:
            self.category = "UNCATEGORIZED"
        if not isinstance(self.taxable, bool):
            raise ValueError("taxable must be a bool")
        if self.source != "observed":
            raise ValueError(
                "source must be 'observed' — the catalog is mined-only "
                "(curated/online-catalog sources were removed by owner "
                "decision)"
            )
        if (
            isinstance(self.observed_count, bool)
            or not isinstance(self.observed_count, int)
            or self.observed_count < 1
        ):
            raise ValueError("observed_count must be a positive integer")
        if self.upc is not None and not isinstance(self.upc, str):
            raise ValueError("upc must be a string or None")
        if (
            not isinstance(self.source_receipt_keys, list)
            or not self.source_receipt_keys
            or not all(
                isinstance(key, str) and key
                for key in self.source_receipt_keys
            )
        ):
            raise ValueError(
                "source_receipt_keys must be a non-empty list of receipt "
                "keys — every mined item records its provenance"
            )
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()
        else:
            try:
                datetime.fromisoformat(self.last_updated)
            except (TypeError, ValueError) as exc:
                raise ValueError("last_updated must be ISO formatted") from exc

    @property
    def slug(self) -> str:
        """Stable merchant slug (the catalog partition discriminator)."""
        return slugify_merchant(self.merchant_name)

    @property
    def normalized_name(self) -> str:
        """Normalized product text (the de-dupe component of the SK)."""
        return normalize_product_text(self.product_text)

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """Primary key for this catalog item."""
        return {
            "PK": {"S": f"MERCHANT_CATALOG#{self.slug}"},
            "SK": {"S": f"ITEM#{self.category}#{self.normalized_name}"},
        }

    def to_item(self) -> dict[str, dict[str, Any]]:
        """Serialize to a DynamoDB item."""
        return {
            **self.key,
            "TYPE": {"S": "MERCHANT_CATALOG_ITEM"},
            "merchant_name": {"S": self.merchant_name},
            "product_text": {"S": self.product_text},
            "price": {"S": self.price},
            "category": {"S": self.category},
            "taxable": {"BOOL": self.taxable},
            "source": {"S": self.source},
            "observed_count": {"N": str(self.observed_count)},
            "source_receipt_keys": {
                "L": [{"S": key} for key in self.source_receipt_keys]
            },
            "upc": {"S": self.upc} if self.upc else {"NULL": True},
            "last_updated": {"S": self.last_updated},
        }

    def __repr__(self) -> str:
        return (
            f"MerchantCatalogItem(merchant_name='{self.merchant_name}', "
            f"product_text='{self.product_text}', price='{self.price}', "
            f"category='{self.category}', taxable={self.taxable}, "
            f"observed_count={self.observed_count}, "
            f"source_receipt_keys={self.source_receipt_keys})"
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantCatalogItem":
        """Deserialize a DynamoDB item into a MerchantCatalogItem."""
        missing = DynamoDBEntity.validate_keys(item, cls.REQUIRED_KEYS)
        if missing:
            raise ValueError(
                f"Item is missing required keys: {sorted(missing)}"
            )
        if item["TYPE"].get("S") != "MERCHANT_CATALOG_ITEM":
            raise ValueError("Invalid MerchantCatalogItem TYPE")
        try:
            upc_attr = item.get("upc", {})
            upc = None if upc_attr.get("NULL") else upc_attr.get("S")
            source_keys = [
                entry["S"]
                for entry in item["source_receipt_keys"].get("L", [])
            ]
            return cls(
                merchant_name=item["merchant_name"]["S"],
                product_text=item["product_text"]["S"],
                price=item["price"]["S"],
                category=item["category"]["S"],
                taxable=item["taxable"]["BOOL"],
                source=item["source"]["S"],
                observed_count=int(item["observed_count"]["N"]),
                upc=upc,
                source_receipt_keys=source_keys,
                last_updated=item["last_updated"]["S"],
            )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Error converting item to MerchantCatalogItem: {exc}"
            ) from exc


def item_to_merchant_catalog_item(
    item: dict[str, Any],
) -> MerchantCatalogItem:
    """Convert a DynamoDB item to a MerchantCatalogItem."""
    return MerchantCatalogItem.from_item(item)
