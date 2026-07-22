"""Accessor methods for MerchantCatalogItem items in DynamoDB.

MerchantCatalogItem rows form the per-merchant catalog AUTHORING partition
(``MERCHANT_CATALOG#<slug>``) that the merchant-truth migration snapshots
into the ``catalog_snapshot`` component. The catalog is mined-only: rows are
written exclusively by ``scripts/ingest_merchant_catalog.py`` (owner-gated
``--apply``), which deletes the merchant's partition and rewrites it fresh —
hence the idempotent delete/put surface here.
"""

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.base_operations.shared_utils import (
    validate_pagination_params,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.merchant_catalog_item import (
    MerchantCatalogItem,
    item_to_merchant_catalog_item,
    normalize_product_text,
    slugify_merchant,
)


class _MerchantCatalogItem(FlattenedStandardMixin):
    """Data-access methods for the per-merchant observed item catalog.

    Methods
    -------
    add_merchant_catalog_item(item)
    add_merchant_catalog_items(items)
    put_merchant_catalog_items(items)      # upsert (overwrite) for re-ingest
    get_merchant_catalog_item(merchant_name, category, product_text)
    list_merchant_catalog_items(merchant_name)   # one merchant's partition
    list_all_merchant_catalog_items(limit, last_evaluated_key)
    delete_merchant_catalog_items(items)
    delete_merchant_catalog(merchant_name)       # clear before re-ingest
    """

    @handle_dynamodb_errors("add_merchant_catalog_item")
    def add_merchant_catalog_item(self, item: MerchantCatalogItem) -> None:
        """Add one catalog item; fails if the item already exists."""
        if not isinstance(item, MerchantCatalogItem):
            raise EntityValidationError(
                "item must be an instance of MerchantCatalogItem"
            )
        self._add_entity(item, condition_expression="attribute_not_exists(PK)")

    @handle_dynamodb_errors("add_merchant_catalog_items")
    def add_merchant_catalog_items(
        self, items: list[MerchantCatalogItem]
    ) -> None:
        """Batch-add catalog items (no existence condition)."""
        self._validate_items(items)
        self._add_entities(items, MerchantCatalogItem, "items")

    @handle_dynamodb_errors("put_merchant_catalog_items")
    def put_merchant_catalog_items(
        self, items: list[MerchantCatalogItem]
    ) -> None:
        """Upsert catalog items (overwrite by key) — for idempotent ingest."""
        self._validate_items(items)
        # No condition -> plain put (overwrite) via the update path.
        self._update_entities(items, MerchantCatalogItem, "items")

    @handle_dynamodb_errors("get_merchant_catalog_item")
    def get_merchant_catalog_item(
        self, merchant_name: str, category: str, product_text: str
    ) -> MerchantCatalogItem:
        """Fetch one catalog item by its natural key components."""
        slug = slugify_merchant(merchant_name)
        norm = normalize_product_text(product_text)
        result = self._get_entity(
            primary_key=f"MERCHANT_CATALOG#{slug}",
            sort_key=f"ITEM#{category}#{norm}",
            entity_class=MerchantCatalogItem,
            converter_func=item_to_merchant_catalog_item,
        )
        if result is None:
            raise EntityNotFoundError(
                f"MerchantCatalogItem for merchant={merchant_name}, "
                f"category={category}, product={product_text} not found"
            )
        return result

    @handle_dynamodb_errors("list_merchant_catalog_items")
    def list_merchant_catalog_items(
        self, merchant_name: str
    ) -> list[MerchantCatalogItem]:
        """All catalog items for one merchant (Query on the partition)."""
        slug = slugify_merchant(merchant_name)
        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression=("#pk = :pk AND begins_with(#sk, :sk)"),
            expression_attribute_names={"#pk": "PK", "#sk": "SK"},
            expression_attribute_values={
                ":pk": {"S": f"MERCHANT_CATALOG#{slug}"},
                ":sk": {"S": "ITEM#"},
            },
            converter_func=item_to_merchant_catalog_item,
            limit=None,
            last_evaluated_key=None,
        )
        return results

    @handle_dynamodb_errors("list_all_merchant_catalog_items")
    def list_all_merchant_catalog_items(
        self,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[MerchantCatalogItem], dict[str, Any] | None]:
        """All catalog items across merchants (GSITYPE query)."""
        validate_pagination_params(limit, last_evaluated_key)
        return self._query_by_type(
            entity_type="MERCHANT_CATALOG_ITEM",
            converter_func=item_to_merchant_catalog_item,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("delete_merchant_catalog_items")
    def delete_merchant_catalog_items(
        self, items: list[MerchantCatalogItem]
    ) -> None:
        """Batch-delete the given catalog items."""
        self._validate_items(items)
        self._delete_entities(items)

    @handle_dynamodb_errors("delete_merchant_catalog")
    def delete_merchant_catalog(self, merchant_name: str) -> None:
        """Delete all catalog items for a merchant (idempotent re-ingest)."""
        items = self.list_merchant_catalog_items(merchant_name)
        if items:
            self.delete_merchant_catalog_items(items)

    @staticmethod
    def _validate_items(items: list[MerchantCatalogItem]) -> None:
        if not isinstance(items, list):
            raise EntityValidationError("items must be a list")
        for index, item in enumerate(items):
            if not isinstance(item, MerchantCatalogItem):
                raise EntityValidationError(
                    f"items[{index}] must be a MerchantCatalogItem, "
                    f"got {type(item).__name__}"
                )
