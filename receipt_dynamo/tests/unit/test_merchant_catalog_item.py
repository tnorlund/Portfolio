# pylint: disable=redefined-outer-name
import pytest

from receipt_dynamo import MerchantCatalogItem, item_to_merchant_catalog_item
from receipt_dynamo.entities.merchant_catalog_item import (
    normalize_product_text,
    slugify_merchant,
)


@pytest.fixture
def catalog_item():
    return MerchantCatalogItem(
        merchant_name="Sprouts Farmers Market",
        product_text="org sour cream 16oz",
        price="1.99",
        category="DAIRY",
        taxable=False,
        source="online_catalog",
        observed_count=3,
        upc="012345678905",
        source_receipt_keys=["abc#00001", "def#00002"],
    )


@pytest.mark.unit
def test_helpers():
    assert (
        slugify_merchant("Sprouts Farmers Market") == "sprouts_farmers_market"
    )
    assert normalize_product_text("  org   Sour  CREAM ") == "ORG SOUR CREAM"


@pytest.mark.unit
def test_init_and_keys(catalog_item):
    it = catalog_item.to_item()
    assert it["PK"]["S"] == "MERCHANT_CATALOG#sprouts_farmers_market"
    assert it["SK"]["S"] == "ITEM#DAIRY#ORG SOUR CREAM 16OZ"
    assert it["TYPE"]["S"] == "MERCHANT_CATALOG_ITEM"
    assert it["GSI1PK"]["S"] == "MERCHANT_CATALOG_ITEM"
    assert it["price"]["S"] == "1.99"
    assert it["taxable"]["BOOL"] is False
    assert it["observed_count"]["N"] == "3"
    assert it["upc"]["S"] == "012345678905"
    assert [x["S"] for x in it["source_receipt_keys"]["L"]] == [
        "abc#00001",
        "def#00002",
    ]


@pytest.mark.unit
def test_roundtrip(catalog_item):
    restored = item_to_merchant_catalog_item(catalog_item.to_item())
    assert restored == catalog_item
    assert restored.to_item() == catalog_item.to_item()


@pytest.mark.unit
def test_null_upc_roundtrip():
    it = MerchantCatalogItem(
        merchant_name="Target",
        product_text="MKT PANTRY WHITE BREAD",
        price="1.29",
        category="UNCATEGORIZED",
        taxable=True,
        source="observed",
        observed_count=0,
    )
    assert it.to_item()["upc"] == {"NULL": True}
    assert item_to_merchant_catalog_item(it.to_item()).upc is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,match",
    [
        ("merchant_name", "", "merchant_name must be a non-empty string"),
        ("product_text", "", "product_text must be a non-empty string"),
        ("price", "abc", "price must be a decimal string"),
        ("taxable", "yes", "taxable must be a bool"),
        ("source", "bogus", "source must be one of"),
        (
            "observed_count",
            -1,
            "observed_count must be a non-negative integer",
        ),
    ],
)
def test_invalid_fields(field, value, match):
    kwargs = dict(
        merchant_name="Sprouts Farmers Market",
        product_text="x",
        price="1.00",
        category="DAIRY",
        taxable=False,
        source="observed",
        observed_count=0,
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=match):
        MerchantCatalogItem(**kwargs)


@pytest.mark.unit
def test_empty_category_defaults_uncategorized():
    it = MerchantCatalogItem(
        merchant_name="Sprouts Farmers Market",
        product_text="LIMES",
        price="1.50",
        category="",
        taxable=False,
        source="observed",
    )
    assert it.category == "UNCATEGORIZED"


@pytest.mark.unit
def test_from_item_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        item_to_merchant_catalog_item({"PK": {"S": "MERCHANT_CATALOG#x"}})
