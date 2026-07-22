"""Integration tests (moto) for MerchantCatalogItem operations in DynamoDB."""

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.merchant_catalog_item import MerchantCatalogItem

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


def make_item(
    product_text: str = "KS ORG PNT BTR",
    category: str = "GROCERY",
    merchant_name: str = "Costco Wholesale",
    observed_count: int = 2,
    price: str = "9.99",
) -> MerchantCatalogItem:
    return MerchantCatalogItem(
        merchant_name=merchant_name,
        product_text=product_text,
        price=price,
        category=category,
        taxable=False,
        source="observed",
        observed_count=observed_count,
        source_receipt_keys=["img-1#00001", "img-2#00002"],
        last_updated="2026-07-22T00:00:00+00:00",
    )


@pytest.fixture
def client(dynamodb_table: str) -> DynamoClient:
    return DynamoClient(table_name=dynamodb_table)


# -------------------------------------------------------------------
#                    SUCCESSFUL OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestMerchantCatalogItemOperations:
    def test_add_and_get(self, client: DynamoClient) -> None:
        item = make_item()
        client.add_merchant_catalog_item(item)
        fetched = client.get_merchant_catalog_item(
            "Costco Wholesale", "GROCERY", "KS ORG PNT BTR"
        )
        assert fetched == item
        assert fetched.source_receipt_keys == ["img-1#00001", "img-2#00002"]

    def test_add_duplicate_raises(self, client: DynamoClient) -> None:
        item = make_item()
        client.add_merchant_catalog_item(item)
        with pytest.raises(EntityAlreadyExistsError):
            client.add_merchant_catalog_item(item)

    def test_add_items_and_list_by_merchant(
        self, client: DynamoClient
    ) -> None:
        items = [
            make_item("KS ORG PNT BTR", "GROCERY"),
            make_item("ORG SPINACH", "PRODUCE"),
            make_item("ORG MILK", "DAIRY", merchant_name="Sprouts"),
        ]
        client.add_merchant_catalog_items(items)
        costco = client.list_merchant_catalog_items("Costco Wholesale")
        assert {i.product_text for i in costco} == {
            "KS ORG PNT BTR",
            "ORG SPINACH",
        }
        sprouts = client.list_merchant_catalog_items("Sprouts")
        assert [i.product_text for i in sprouts] == ["ORG MILK"]

    def test_put_items_upserts(self, client: DynamoClient) -> None:
        client.add_merchant_catalog_item(make_item(observed_count=1))
        client.put_merchant_catalog_items([make_item(observed_count=7)])
        fetched = client.get_merchant_catalog_item(
            "Costco Wholesale", "GROCERY", "KS ORG PNT BTR"
        )
        assert fetched.observed_count == 7

    def test_list_all(self, client: DynamoClient) -> None:
        client.add_merchant_catalog_items(
            [
                make_item("A ITEM", "GROCERY"),
                make_item("B ITEM", "PRODUCE", merchant_name="Sprouts"),
            ]
        )
        all_items, last_key = client.list_all_merchant_catalog_items()
        assert last_key is None
        assert {i.merchant_name for i in all_items} == {
            "Costco Wholesale",
            "Sprouts",
        }

    def test_delete_items(self, client: DynamoClient) -> None:
        item = make_item()
        client.add_merchant_catalog_item(item)
        client.delete_merchant_catalog_items([item])
        with pytest.raises(EntityNotFoundError):
            client.get_merchant_catalog_item(
                "Costco Wholesale", "GROCERY", "KS ORG PNT BTR"
            )

    def test_delete_merchant_catalog_clears_partition(
        self, client: DynamoClient
    ) -> None:
        client.add_merchant_catalog_items(
            [
                make_item("A ITEM", "GROCERY"),
                make_item("B ITEM", "PRODUCE"),
                make_item("KEEP ME", "DAIRY", merchant_name="Sprouts"),
            ]
        )
        client.delete_merchant_catalog("Costco Wholesale")
        assert client.list_merchant_catalog_items("Costco Wholesale") == []
        assert len(client.list_merchant_catalog_items("Sprouts")) == 1

    def test_delete_merchant_catalog_empty_is_noop(
        self, client: DynamoClient
    ) -> None:
        client.delete_merchant_catalog("Costco Wholesale")  # no raise
        assert client.list_merchant_catalog_items("Costco Wholesale") == []


# -------------------------------------------------------------------
#                    VALIDATION ERRORS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestMerchantCatalogItemValidation:
    def test_add_wrong_type(self, client: DynamoClient) -> None:
        with pytest.raises(EntityValidationError):
            client.add_merchant_catalog_item("not-an-item")  # type: ignore

    def test_add_items_wrong_element_type(self, client: DynamoClient) -> None:
        with pytest.raises(EntityValidationError, match=r"items\[1\]"):
            client.add_merchant_catalog_items([make_item(), "nope"])

    def test_put_items_not_a_list(self, client: DynamoClient) -> None:
        with pytest.raises(EntityValidationError, match="must be a list"):
            client.put_merchant_catalog_items(make_item())  # type: ignore

    def test_get_missing_raises_not_found(self, client: DynamoClient) -> None:
        with pytest.raises(EntityNotFoundError):
            client.get_merchant_catalog_item(
                "Costco Wholesale", "GROCERY", "NO SUCH ITEM"
            )
