"""Portion and purchase are separate explicit quantities, with exact costs."""

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from receipt_nutrition.portions import Meal, calculate_meal, render_meal
from receipt_nutrition.units import normalized_amount


def item(key="test", price="8", purchased="2", portion="1", unit="tsp"):
    return {
        "key": key,
        "product": {
            "product_id": key,
            "name": "Synthetic label product",
            "identity_kind": "exact",
            "evidence": [
                {
                    "evidence_id": "label",
                    "source": "synthetic",
                    "record_id": "toy",
                    "reference": "synthetic:test",
                    "observed_on": "2026-09-08",
                    "verification": "synthetic",
                    "license": "synthetic",
                }
            ],
            "nutrients": [
                {
                    "nutrient_id": "208",
                    "amount": "90",
                    "unit": "kcal",
                    "basis": "serving",
                    "source_ref": "label",
                },
                {
                    "nutrient_id": "203",
                    "amount": "0",
                    "unit": "g",
                    "basis": "serving",
                    "source_ref": "label",
                },
            ],
            "net_amount": {"value": "120", "unit": "g"},
            "serving": {"value": "15", "unit": "g"},
            "servings_per_container": "8",
            "package_source_ref": "label",
        },
        "household_serving": {
            "value": "1",
            "unit": "tbsp",
            "source_ref": "label",
        },
        "purchase": {
            "extended_price": price,
            "quantity": {
                "status": "known",
                "reason": "explicit toy count",
                "quantity": {
                    "value": purchased,
                    "unit": "package",
                    "method": "user",
                    "reference": "toy",
                },
            },
        },
        "portion": {
            "value": portion,
            "unit": unit,
            "reference": "toy explicit portion",
        },
    }


def meal(*items):
    return Meal.model_validate(
        {"title": "Synthetic test meal", "items": items}
    )


def test_teaspoon_tablespoon_and_exact_cost():
    teaspoon = calculate_meal(meal(item()))
    tablespoon = calculate_meal(meal(item(unit="tbsp")))
    assert teaspoon["rows"][0]["label_servings"] == "1/3"
    assert teaspoon["nutrients"]["208"]["amount"] == "30"
    assert teaspoon["cost"] == "0.17"  # $8 / (2 packs * 8 servings) / 3
    assert tablespoon["nutrients"]["208"]["amount"] == "90"
    assert tablespoon["cost"] == "0.50"


def test_three_eggs_and_purchase_override_are_independent():
    row = item(portion="3", unit="each")
    row["product"]["serving"] = {"value": "1", "unit": "each"}
    row["product"]["net_amount"] = {"value": "12", "unit": "each"}
    row["product"]["servings_per_container"] = "12"
    row["household_serving"] = None
    first = calculate_meal(meal(row))
    assert first["nutrients"]["208"]["amount"] == "270"
    assert first["cost"] == "1.00"
    row["purchase"]["quantity"]["quantity"]["value"] = "1"
    second = calculate_meal(meal(row))
    assert second["cost"] == "2.00"
    assert second["nutrients"] == first["nutrients"]
    assert second["input_hash"] != first["input_hash"]


def test_total_money_rounds_once_not_sum_of_displayed_cents():
    result = calculate_meal(meal(item("a"), item("b"), item("c")))
    assert [r["cost"] for r in result["rows"]] == ["0.17"] * 3
    assert result["cost"] == "0.50"


@pytest.mark.parametrize("status", ["unknown", "conflict"])
def test_unknown_purchase_leaves_cost_unknown_but_portion_facts_usable(status):
    row = item()
    row["purchase"]["quantity"] = {
        "status": status,
        "reason": "quantity not established",
    }
    result = calculate_meal(meal(row))
    assert result["cost"] is None
    assert result["nutrients"]["208"]["amount"] == "30"


def test_missing_nutrient_is_not_a_zero_or_complete_total():
    first, second = item("a"), item("b")
    second["product"]["nutrients"] = []
    result = calculate_meal(meal(first, second))
    assert result["nutrients"]["208"] == {
        "unit": "kcal",
        "complete": False,
        "amount": None,
        "available_subtotal": "30",
        "items_with_value": 1,
        "items": 2,
    }
    assert result["rows"][0]["nutrients"]["203"] == "0"
    assert "unknown" in render_meal(result)
    assert "incomplete" in render_meal(result)


def test_unverified_facts_never_compute():
    row = item()
    row["product"]["evidence"].append(
        {
            "evidence_id": "unverified",
            "source": "manual",
            "record_id": "guess",
            "reference": "guess",
            "observed_on": "2026-09-08",
            "verification": "unverified",
            "license": "unknown",
        }
    )
    row["product"]["nutrients"][0]["source_ref"] = "unverified"
    result = calculate_meal(meal(row))
    assert result["nutrients"]["208"]["amount"] is None


def test_no_density_or_household_equivalence_is_guessed():
    row = item(unit="ml")
    assert calculate_meal(meal(row))["nutrients"]["208"]["amount"] is None
    row["portion"]["unit"] = "tsp"
    row["household_serving"] = None
    assert calculate_meal(meal(row))["cost"] is None


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", 1.5])
def test_bad_portion_input_rejected(value):
    with pytest.raises(ValidationError):
        meal(item(portion=value))


def test_duplicate_keys_rejected():
    with pytest.raises(ValidationError):
        meal(item(), item())


def test_mass_and_package_portions():
    gram = calculate_meal(meal(item(portion="30", unit="g")))
    package = calculate_meal(meal(item(portion="0.25", unit="package")))
    assert gram["cost"] == package["cost"] == "1.00"
    assert (
        gram["nutrients"]["208"]["amount"]
        == package["nutrients"]["208"]["amount"]
        == "180"
    )


def test_cli_explicit_overrides_and_invalid_target(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"title": "Synthetic meal", "items": [item()]}))
    command = [
        sys.executable,
        "-m",
        "receipt_nutrition.portions",
        str(path),
        "--format",
        "json",
    ]
    result = subprocess.run(
        command + ["--portion", "test=1:tbsp", "--quantity", "test=1:package"],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(result.stdout)
    assert parsed["cost"] == "1.00"
    assert parsed["nutrients"]["208"]["amount"] == "90"
    bad = subprocess.run(
        command + ["--portion", "missing=1:tbsp"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0


def test_converted_physical_quantity_preserves_precision_in_cli(tmp_path):
    row = item(portion="10", unit="ml", price="5")
    amount = normalized_amount("1.125", "fl oz")
    row["purchase"]["quantity"]["quantity"].update(
        value=str(amount.value), unit=amount.unit
    )
    row["product"]["nutrients"] = []
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"title": "Converted volume", "items": [row]}))
    command = [
        sys.executable,
        "-m",
        "receipt_nutrition.portions",
        str(path),
        "--format",
        "json",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=True
    )
    parsed = json.loads(result.stdout)
    assert parsed["cost"] == "1.50"
    assert parsed["rows"][0]["purchase"]["quantity"]["quantity"][
        "value"
    ] == str(amount.value)
    whole = subprocess.run(
        command + ["--portion", f"test={amount.value}:ml"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(whole.stdout)["cost"] == "5.00"


@pytest.mark.parametrize("quantity,cost", [("1", "0.33"), ("4", "0.08")])
def test_readable_cli_shows_effective_quantity_and_base_notes(
    tmp_path, quantity, cost
):
    row = item()
    row["assumptions"] = ["Original evidence suggested two packages."]
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"title": "Quantity scenario", "items": [row]}))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "receipt_nutrition.portions",
            str(path),
            "--quantity",
            f"test={quantity}:package",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"Portion cost: ${cost}" in result.stdout
    assert f"Effective purchase quantity: {quantity} package" in result.stdout
    assert (
        "Base input notes (scenario overrides take precedence)"
        in result.stdout
    )
    assert "Original evidence suggested two packages." in result.stdout


def test_conflicting_package_bases_are_rejected():
    row = item(purchased="1", unit="package")
    row["product"]["servings_per_container"] = "4"
    with pytest.raises(ValidationError, match="conflicting package"):
        meal(row)
    row["portion"] = {
        "value": "120",
        "unit": "g",
        "reference": "same package mass",
    }
    with pytest.raises(ValidationError, match="conflicting package"):
        meal(row)


def test_label_rounding_allowed_but_measured_cost_uses_net_mass():
    row = item(purchased="1", portion="120", unit="g")
    row["product"]["serving"]["value"] = "7"
    row["product"]["servings_per_container"] = "17"
    row["serving_rounding_allowance"] = "0.1"
    result = calculate_meal(meal(row))
    assert result["cost"] == "8.00"
    assert result["rows"][0]["cost_basis"] == "physical_quantity:g"
    row["product"]["serving"]["value"] = "7.00"
    assert calculate_meal(meal(row))["cost"] == "8.00"
    row["serving_rounding_allowance"] = "0"
    with pytest.raises(ValidationError, match="conflicting package"):
        meal(row)


def test_measured_portion_and_100g_facts_need_no_serving_metadata():
    row = item(portion="100", unit="g", price="5")
    row["product"]["nutrients"] = [
        {
            "nutrient_id": "208",
            "amount": "120",
            "unit": "kcal",
            "basis": "100g",
            "source_ref": "label",
        }
    ]
    for field in (
        "serving",
        "net_amount",
        "servings_per_container",
        "package_source_ref",
    ):
        row["product"].pop(field)
    row["household_serving"] = None
    row["purchase"]["quantity"]["quantity"].update(value="500", unit="g")
    result = calculate_meal(meal(row))
    assert result["nutrients"]["208"]["amount"] == "120"
    assert result["cost"] == "1.00"


def test_generic_estimates_remain_visible_in_readable_report():
    row = item(unit="tbsp")
    row["product"]["identity_kind"] = "generic"
    result = calculate_meal(meal(row))
    assert result["contains_generic_estimates"]
    text = render_meal(result)
    assert "estimated; generic product" in text
    assert "includes generic product estimates" in text
