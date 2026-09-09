"""Nutrition serialization preserves exact numbers and conditional identity."""

from datetime import date
from decimal import Decimal

import pytest

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.food_product import (
    FoodProduct,
    item_to_food_product,
)
from receipt_dynamo.entities.nutrition_support import (
    nutrition_hash,
    nutrition_json,
    nutrition_key,
)
from receipt_dynamo.entities.product_alias import (
    ProductAlias,
    item_to_product_alias,
)

pytestmark = [pytest.mark.unit, pytest.mark.unused_in_production]


def test_nutrition_exact_roundtrip() -> None:
    facts = {"product_id": "test:é#1", "amount": Decimal("1.0000000000001")}
    product = FoodProduct(facts["product_id"], nutrition_json(facts))
    assert item_to_food_product(product.to_item()) == product
    assert '"1.0000000000001"' in product.payload_json
    assert "#" not in nutrition_key(facts["product_id"])
    assert nutrition_hash({"value": Decimal("1.000")}) == nutrition_hash(
        {"value": Decimal("1")}
    )
    assert nutrition_json({"day": date(2026, 9, 8)}) == '{"day":"2026-09-08"}'


@pytest.mark.parametrize("value", [1.1, Decimal("NaN"), {1: "bad"}, object()])
def test_nutrition_json_rejects_unsafe_values(value: object) -> None:
    with pytest.raises(EntityValidationError):
        nutrition_json(value)


def test_nutrition_product_integrity_and_size() -> None:
    with pytest.raises(EntityValidationError, match="identity"):
        FoodProduct("one", '{"product_id":"two"}')
    with pytest.raises(EntityValidationError, match="300 KB"):
        FoodProduct(
            "one", nutrition_json({"product_id": "one", "x": "x" * 300_000})
        )
    product = FoodProduct("one", '{"product_id":"one"}')
    item = product.to_item()
    item["payload_json"] = {"S": '{"product_id":"one","changed":true}'}
    with pytest.raises(EntityValidationError, match="integrity"):
        item_to_food_product(item)


def user_alias(**changes: object) -> ProductAlias:
    fields = dict(
        merchant_slug="test",
        kind="TEXT",
        text="milk",
        revision=1,
        status="rejected",
        method="user",
        confirmed_by_user=True,
        changed_at="2026-09-08T00:00:00+00:00",
        applicability_json='{"variant":"unresolved"}',
    )
    fields.update(changes)
    return ProductAlias(**fields)


def test_nutrition_alias_roundtrip_and_expiry() -> None:
    alias = user_alias()
    assert item_to_product_alias(alias.to_item()) == alias
    assert not alias.is_expired(9999999999)
    automatic = user_alias(
        method="lexical", confirmed_by_user=False, expires_at=2_000_000_000
    )
    assert automatic.is_expired(2_000_000_000)
    assert not automatic.is_expired(1_999_999_999)
    assert "time_to_live" not in automatic.to_item()


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"revision": 0},
        {"kind": "GTIN"},
        {"status": "matched"},
        {"product_revision": "a" * 64},
        {"method": "model"},
        {"expires_at": 2_000_000_000},
        {"applicability_json": "{}"},
        {"changed_at": "2026-09-08T00:00:00Z"},
        {"method": "model", "confirmed_by_user": False},
        {"method": "model", "confirmed_by_user": False, "expires_at": 1},
    ],
)
def test_nutrition_alias_rejects_invalid_decisions(changes: dict) -> None:
    with pytest.raises(EntityValidationError):
        user_alias(**changes)
