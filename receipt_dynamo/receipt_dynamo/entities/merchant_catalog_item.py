from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Generator


def slugify_merchant(name: str) -> str:
    """Merchant name -> stable slug (matches the online_catalogs/*.json names)."""
    return "".join(
        c if c.isalnum() else "_" for c in name.strip().lower()
    ).strip("_")


def normalize_product_text(text: str) -> str:
    """Uppercase + collapse whitespace so the same item de-dupes in the SK."""
    return " ".join(text.upper().split())


@dataclass(eq=True, unsafe_hash=False)
class MerchantCatalogItem:
    """A catalog item for a merchant, used to inject realistic line items into
    synthetic receipts (the add-line-item augmentation).

    Persisted in DynamoDB so the augmentation queries the catalog instead of
    re-deriving it from curated JSON + a full receipt scan on every run. The
    catalog is the union of curated brand-correct products (``source`` =
    ``online_catalog``) and items observed across the merchant's real receipts
    (``source`` = ``observed``); items seen both ways are ``merged``.

    Key:
        PK = MERCHANT_CATALOG#<slug>
        SK = ITEM#<category>#<normalized_product_text>
        TYPE = MERCHANT_CATALOG_ITEM
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "merchant_name",
        "product_text",
        "price",
        "category",
        "taxable",
        "source",
        "observed_count",
        "last_updated",
    }

    _SOURCES = {"online_catalog", "observed", "merged"}

    merchant_name: str
    product_text: str
    price: str  # Decimal-as-string, e.g. "1.99"
    category: str  # PRODUCE / DAIRY / GROCERY / ... / UNCATEGORIZED
    taxable: bool
    source: str  # online_catalog | observed | merged
    observed_count: int = 0
    upc: str | None = None
    source_receipt_keys: list[str] = field(default_factory=list)
    last_updated: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.merchant_name, str) or not self.merchant_name:
            raise ValueError("merchant_name must be a non-empty string")
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
        if self.source not in self._SOURCES:
            raise ValueError(f"source must be one of {sorted(self._SOURCES)}")
        if not isinstance(self.observed_count, int) or self.observed_count < 0:
            raise ValueError("observed_count must be a non-negative integer")
        if self.upc is not None and not isinstance(self.upc, str):
            raise ValueError("upc must be a string or None")
        if not isinstance(self.source_receipt_keys, list):
            raise ValueError("source_receipt_keys must be a list")
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()
        else:
            try:
                datetime.fromisoformat(self.last_updated)
            except (TypeError, ValueError) as exc:
                raise ValueError("last_updated must be ISO formatted") from exc

    @property
    def slug(self) -> str:
        return slugify_merchant(self.merchant_name)

    @property
    def normalized_name(self) -> str:
        return normalize_product_text(self.product_text)

    @property
    def key(self) -> dict[str, dict[str, str]]:
        return {
            "PK": {"S": f"MERCHANT_CATALOG#{self.slug}"},
            "SK": {"S": f"ITEM#{self.category}#{self.normalized_name}"},
        }

    def to_item(self) -> dict[str, dict[str, Any]]:
        return {
            **self.key,
            "TYPE": {"S": "MERCHANT_CATALOG_ITEM"},
            "GSI1PK": {"S": "MERCHANT_CATALOG_ITEM"},
            "GSI1SK": {
                "S": f"MERCHANT#{self.slug}#ITEM#{self.normalized_name}"
            },
            "merchant_name": {"S": self.merchant_name},
            "product_text": {"S": self.product_text},
            "price": {"S": self.price},
            "category": {"S": self.category},
            "taxable": {"BOOL": self.taxable},
            "source": {"S": self.source},
            "observed_count": {"N": str(self.observed_count)},
            "source_receipt_keys": {
                "L": [{"S": k} for k in self.source_receipt_keys]
            },
            "upc": {"S": self.upc} if self.upc else {"NULL": True},
            "last_updated": {"S": self.last_updated},
        }

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield "merchant_name", self.merchant_name
        yield "product_text", self.product_text
        yield "price", self.price
        yield "category", self.category
        yield "taxable", self.taxable
        yield "source", self.source
        yield "observed_count", self.observed_count
        yield "upc", self.upc
        yield "source_receipt_keys", self.source_receipt_keys
        yield "last_updated", self.last_updated

    def __repr__(self) -> str:
        return (
            f"MerchantCatalogItem(merchant_name='{self.merchant_name}', "
            f"product_text='{self.product_text}', price='{self.price}', "
            f"category='{self.category}', taxable={self.taxable}, "
            f"source='{self.source}', observed_count={self.observed_count})"
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantCatalogItem":
        if not cls.REQUIRED_KEYS.issubset(item):
            raise ValueError("Item is missing required keys")
        try:
            upc_attr = item.get("upc", {})
            upc = None if upc_attr.get("NULL") else upc_attr.get("S")
            source_keys = [
                x["S"]
                for x in item.get("source_receipt_keys", {}).get("L", [])
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
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Error converting item to MerchantCatalogItem: {exc}"
            ) from exc


def item_to_merchant_catalog_item(
    item: dict[str, Any],
) -> MerchantCatalogItem:
    """Convert a DynamoDB item to a MerchantCatalogItem."""
    return MerchantCatalogItem.from_item(item)
