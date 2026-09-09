"""Physical/source ambiguity must never silently become a computed fact."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from receipt_nutrition import Amount, Product, Purchase, cost_purchase
from receipt_nutrition.models import NutrientFact, QuantityResolution
from receipt_nutrition.units import amount_in, normalized_amount


def purchase(unit: str = "package", value: str = "1") -> Purchase:
    return Purchase.model_validate(
        {
            "extended_price": "4",
            "quantity": {
                "status": "known",
                "reason": "test explicit purchase",
                "quantity": {
                    "unit": unit,
                    "value": value,
                    "method": "user",
                    "reference": "synthetic purchase confirmation",
                },
            },
        }
    )


@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "-Infinity", "-1", "0", "1e13", "1e-13", 1.1, True],
)
def test_invalid_amount(value: object) -> None:
    with pytest.raises(ValidationError):
        Amount(value=value, unit="g")


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("g", "2"),
        ("kg", "2000"),
        ("lb", "907.18474"),
        ("oz", "56.699046250"),
        ("ml", "2"),
        ("l", "2000"),
        ("fl oz", "59.1470591250"),
        ("each", "2"),
    ],
)
def test_explicit_unit_constants(unit: str, expected: str) -> None:
    assert normalized_amount("2", unit).value == Decimal(expected)


def test_unsupported_unit() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        normalized_amount("1", "cup")


def test_product_is_immutable(products: dict[str, Product]) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        products["grain"].brand = "changed"


def test_content_hash_preserves_equivalent_numbers(
    products: dict[str, Product],
) -> None:
    original = products["grain"]
    data = original.model_dump(mode="json")
    data["net_amount"]["value"] = "100.000"
    equivalent = Product.model_validate(data)
    assert original.content_hash == equivalent.content_hash
    data["net_amount"]["value"] = "101"
    assert Product.model_validate(data).content_hash != original.content_hash


@pytest.mark.parametrize(
    "mutate",
    [
        "duplicate",
        "unknown_source",
        "missing_package_source",
        "unsourced_density",
        "missing_serving",
    ],
)
def test_invalid_product_dependencies(
    products: dict[str, Product], mutate: str
) -> None:
    data = products["rounded-label"].model_dump(mode="json")
    if mutate == "duplicate":
        data["nutrients"].append(data["nutrients"][0])
    elif mutate == "unknown_source":
        data["nutrients"][0]["source_ref"] = "absent"
    elif mutate == "missing_package_source":
        data["package_source_ref"] = None
    elif mutate == "unsourced_density":
        data["density_g_per_ml"] = "1.03"
    else:
        data["serving"] = None
    with pytest.raises(ValidationError):
        Product.model_validate(data)


def test_nutrient_unit_validation() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        NutrientFact(
            nutrient_id="307",
            amount="5",
            unit="g",
            basis="100g",
            source_ref="label",
        )


def test_unknown_quantity_cannot_carry_amount() -> None:
    data = purchase().quantity.model_dump(mode="json")
    data["status"] = "unknown"
    with pytest.raises(ValidationError, match="only known"):
        QuantityResolution.model_validate(data)


def test_missing_nutrients_are_not_zero(products: dict[str, Product]) -> None:
    result = cost_purchase(products["grain"], purchase())
    assert "539" in result.unavailable_nutrients
    assert "307" not in result.unavailable_nutrients
    sodium = next(n for n in result.nutrients if n.nutrient_id == "307")
    assert sodium.amount == 0


def test_generic_identity_stays_generic(products: dict[str, Product]) -> None:
    assert (
        cost_purchase(products["eggs"], purchase()).identity_kind == "generic"
    )


def test_unverified_sources_do_not_supply_calculations(
    products: dict[str, Product],
) -> None:
    data = products["grain"].model_dump(mode="json")
    data["evidence"][0].update(source="manual", verification="unverified")
    result = cost_purchase(Product.model_validate(data), purchase())
    assert result.cost_per_serving is None
    assert result.purchased_amount is None
    assert result.nutrients == ()


def test_dimensions_need_explicit_evidence(
    products: dict[str, Product],
) -> None:
    mass = Amount(value="103", unit="g")
    assert amount_in(mass, "ml", products["milk"]) is None
    assert amount_in(mass, "ml", products["dense-milk"]) == Decimal("100")
    assert amount_in(mass, "each", products["grain"]) is None


def test_unverified_conversion_is_not_used(
    products: dict[str, Product],
) -> None:
    data = products["dense-milk"].model_dump(mode="json")
    data["evidence"][0].update(source="manual", verification="unverified")
    assert (
        amount_in(
            Amount(value="103", unit="g"), "ml", Product.model_validate(data)
        )
        is None
    )


def test_container_label_vs_measured_mass(
    products: dict[str, Product],
) -> None:
    label = products["rounded-label"]
    container = cost_purchase(label, purchase())
    measured = cost_purchase(label, purchase("g", "99"))
    assert container.servings_purchased == 4
    assert not container.servings_derived
    assert measured.servings_purchased == Decimal("3.96")
    assert measured.servings_derived


def test_money_uses_exact_ratios(products: dict[str, Product]) -> None:
    data = products["grain"].model_dump(mode="json")
    data.update(
        net_amount={"value": "1000", "unit": "g"},
        serving={"value": "60", "unit": "g"},
        servings_per_container=None,
    )
    bought = purchase().model_dump(mode="json")
    bought["extended_price"] = "16.25"
    result = cost_purchase(
        Product.model_validate(data), Purchase.model_validate(bought)
    )
    assert result.cost_per_serving == Decimal("0.98")
    # Exercise an evidenced volume-to-mass division with the same cent tie.
    data.update(
        serving={"value": "60", "unit": "ml"},
        density_g_per_ml="3",
        conversion_source_ref=data["package_source_ref"],
    )
    result = cost_purchase(
        Product.model_validate(data), Purchase.model_validate(bought)
    )
    assert result.cost_per_serving == Decimal("2.93")


def test_declared_servings_conflict_blocks_serving_totals(
    products: dict[str, Product],
) -> None:
    """Servings/container contradicting net and serving sizes is rejected."""
    data = products["grain"].model_dump(mode="json")
    data["net_amount"] = {"value": "100", "unit": "g"}
    data["serving"] = {"value": "25", "unit": "g"}
    data["servings_per_container"] = "20"
    product = Product.model_validate(data)
    result = cost_purchase(product, purchase())
    assert result.servings_purchased is None
    assert result.cost_per_serving is None
    assert "servings_per_container_conflict" in result.reasons
    assert all(n.calculation_basis != "serving" for n in result.nutrients)


def test_declared_servings_within_label_rounding_are_kept(
    products: dict[str, Product],
) -> None:
    data = products["grain"].model_dump(mode="json")
    data["net_amount"] = {"value": "907.18474", "unit": "g"}
    data["serving"] = {"value": "14", "unit": "g"}
    data["servings_per_container"] = "64"
    product = Product.model_validate(data)
    result = cost_purchase(product, purchase())
    assert result.servings_purchased == Decimal("64")
    assert "servings_per_container_conflict" not in result.reasons


def test_excess_precision_rejected_before_costing() -> None:
    with pytest.raises(ValidationError, match="significant digits"):
        purchase("package", "1." + "0" * 49 + "1")
