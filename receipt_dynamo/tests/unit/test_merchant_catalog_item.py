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
        merchant_name="Costco Wholesale",
        product_text="KS ORG PNT BTR",
        price="9.99",
        category="GROCERY",
        taxable=False,
        source="observed",
        observed_count=3,
        upc="012345678905",
        source_receipt_keys=["abc#00001", "def#00002"],
        last_updated="2026-07-22T00:00:00+00:00",
    )


@pytest.mark.unit
def test_helpers():
    assert slugify_merchant("Costco Wholesale") == "costco_wholesale"
    assert (
        slugify_merchant("Sprouts Farmers Market") == "sprouts_farmers_market"
    )
    assert normalize_product_text("  ks   Org  PNT ") == "KS ORG PNT"


@pytest.mark.unit
def test_init_and_keys(catalog_item):
    item = catalog_item.to_item()
    assert item["PK"]["S"] == "MERCHANT_CATALOG#costco_wholesale"
    assert item["SK"]["S"] == "ITEM#GROCERY#KS ORG PNT BTR"
    assert item["TYPE"]["S"] == "MERCHANT_CATALOG_ITEM"
    assert item["price"]["S"] == "9.99"
    assert item["taxable"]["BOOL"] is False
    assert item["source"]["S"] == "observed"
    assert item["observed_count"]["N"] == "3"
    assert item["upc"]["S"] == "012345678905"
    assert [x["S"] for x in item["source_receipt_keys"]["L"]] == [
        "abc#00001",
        "def#00002",
    ]


@pytest.mark.unit
def test_roundtrip(catalog_item):
    restored = item_to_merchant_catalog_item(catalog_item.to_item())
    assert restored == catalog_item
    assert restored.to_item() == catalog_item.to_item()


@pytest.mark.unit
def test_taxable_none_roundtrip():
    """taxable=None (no taxability signal observed) serializes as NULL and
    survives the roundtrip — never coerced to False."""
    item = MerchantCatalogItem(
        merchant_name="Costco Wholesale",
        product_text="KS WATER 40PK",
        price="3.99",
        category="GROCERY",
        taxable=None,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    )
    assert item.to_item()["taxable"] == {"NULL": True}
    restored = item_to_merchant_catalog_item(item.to_item())
    assert restored.taxable is None
    assert restored == item


@pytest.mark.unit
def test_taxable_true_roundtrip():
    item = MerchantCatalogItem(
        merchant_name="Costco Wholesale",
        product_text="PAPER TOWELS",
        price="12.99",
        category="HOUSEHOLD",
        taxable=True,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    )
    assert item.to_item()["taxable"] == {"BOOL": True}
    assert item_to_merchant_catalog_item(item.to_item()).taxable is True


@pytest.mark.unit
def test_null_upc_roundtrip():
    item = MerchantCatalogItem(
        merchant_name="Costco Wholesale",
        product_text="ROTISSERIE CHICK",
        price="4.99",
        category="UNCATEGORIZED",
        taxable=False,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    )
    assert item.to_item()["upc"] == {"NULL": True}
    assert item_to_merchant_catalog_item(item.to_item()).upc is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,match",
    [
        ("merchant_name", "", "merchant_name must be a non-empty string"),
        ("product_text", "", "product_text must be a non-empty string"),
        ("price", "abc", "price must be a decimal string"),
        ("taxable", "yes", "taxable must be a bool"),
        ("source", "online_catalog", "source must be 'observed'"),
        ("source", "merged", "source must be 'observed'"),
        ("observed_count", 0, "observed_count must be a positive integer"),
        ("observed_count", -1, "observed_count must be a positive integer"),
        ("source_receipt_keys", [], "source_receipt_keys must be a non-empty"),
        (
            "source_receipt_keys",
            ["abc#00001", ""],
            "source_receipt_keys must be a non-empty",
        ),
        (
            "source_receipt_keys",
            "abc#00001",
            "source_receipt_keys must be a non-empty",
        ),
        ("last_updated", "not-a-date", "last_updated must be ISO formatted"),
    ],
)
def test_invalid_fields(field, value, match):
    kwargs = dict(
        merchant_name="Costco Wholesale",
        product_text="x",
        price="1.00",
        category="GROCERY",
        taxable=False,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=match):
        MerchantCatalogItem(**kwargs)


@pytest.mark.unit
def test_source_receipt_keys_always_required():
    """The mined-only catalog carries provenance on EVERY item."""
    with pytest.raises(ValueError, match="source_receipt_keys"):
        MerchantCatalogItem(
            merchant_name="Costco Wholesale",
            product_text="KS WATER 40PK",
            price="3.99",
            category="GROCERY",
            taxable=False,
        )


@pytest.mark.unit
def test_empty_category_defaults_uncategorized():
    item = MerchantCatalogItem(
        merchant_name="Costco Wholesale",
        product_text="LIMES",
        price="1.50",
        category="",
        taxable=False,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    )
    assert item.category == "UNCATEGORIZED"


@pytest.mark.unit
def test_last_updated_defaults_to_now():
    item = MerchantCatalogItem(
        merchant_name="Costco Wholesale",
        product_text="LIMES",
        price="1.50",
        category="PRODUCE",
        taxable=False,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    )
    assert item.last_updated  # ISO stamp filled in


@pytest.mark.unit
def test_from_item_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        item_to_merchant_catalog_item({"PK": {"S": "MERCHANT_CATALOG#x"}})


@pytest.mark.unit
def test_from_item_wrong_type():
    good = MerchantCatalogItem(
        merchant_name="Costco Wholesale",
        product_text="LIMES",
        price="1.50",
        category="PRODUCE",
        taxable=False,
        source="observed",
        observed_count=1,
        source_receipt_keys=["abc#00001"],
    ).to_item()
    good["TYPE"] = {"S": "MERCHANT_FONT"}
    with pytest.raises(ValueError, match="Invalid MerchantCatalogItem TYPE"):
        item_to_merchant_catalog_item(good)
