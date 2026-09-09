"""Private portion calculations, independent of ingestion and persistence."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from receipt_nutrition.costing import ratio_money
from receipt_nutrition.models import (
    HOUSEHOLD_TEASPOONS,
    NUTRIENT_UNITS,
    Amount,
    FrozenModel,
    HouseholdServing,
    Nonnegative,
    PhysicalPositive,
    Product,
    Purchase,
    Text,
    Unit,
    content_hash,
)
from receipt_nutrition.units import (
    as_decimal,
    exact_amount_in,
    verified_source,
)

NUTRIENT_NAMES = {
    "208": "Calories",
    "203": "Protein",
    "204": "Fat",
    "205": "Carbohydrate",
    "269": "Sugars",
    "539": "Added sugars",
    "291": "Fiber",
    "307": "Sodium",
    "606": "Saturated fat",
    "605": "Trans fat",
    "601": "Cholesterol",
    "301": "Calcium",
    "303": "Iron",
    "306": "Potassium",
    "328": "Vitamin D",
}

PortionUnit = Literal[
    "serving", "package", "each", "g", "ml", "tsp", "tbsp", "cup"
]
# ``HouseholdServing`` lives in ``models``; it stays importable from here.
__all__ = ["HouseholdServing", "Meal", "MealItem", "Portion", "calculate_meal"]


class Portion(FrozenModel):
    value: PhysicalPositive
    unit: PortionUnit
    reference: Text


class MealItem(FrozenModel):
    key: Text
    product: Product
    purchase: Purchase
    portion: Portion
    household_serving: HouseholdServing | None = None
    assumptions: tuple[Text, ...] = ()
    serving_rounding_allowance: Nonnegative = Decimal("0")

    @property
    def verified_household_serving(self) -> HouseholdServing | None:
        """Unverified equivalence evidence is ignored, never used."""
        if self.household_serving and verified_source(
            self.product, self.household_serving.source_ref
        ):
            return self.household_serving
        return None

    @property
    def calculation_notes(self) -> tuple[str, ...]:
        if self.household_serving and self.verified_household_serving is None:
            return (
                "Household equivalence cites unverified evidence and was "
                "ignored; teaspoon/tablespoon portions stay unknown.",
            )
        return ()

    @model_validator(mode="after")
    def validate_package_consistency(self) -> Self:
        product = self.product
        if (
            product.net_amount
            and product.serving
            and product.servings_per_container is not None
            and verified_source(product, product.package_source_ref)
        ):
            net = exact_amount_in(
                product.net_amount, product.serving.unit, product
            )
            if net is not None:
                count = Fraction(product.servings_per_container)
                declared = count * Fraction(product.serving.value)
                # Explicit calculation allowance, not inferred from decimal
                # formatting (canonical storage removes insignificant zeros).
                allowance = Fraction(self.serving_rounding_allowance)
                cap = min(Fraction(1, 2), Fraction(product.serving.value) / 20)
                if product.serving.unit == "each":
                    cap = Fraction()
                if allowance > cap:
                    raise ValueError(
                        "serving rounding allowance exceeds conservative bound"
                    )
                tolerance = count * allowance
                if abs(net - declared) > tolerance:
                    raise ValueError(
                        "conflicting package and serving quantities"
                    )
        return self


class Meal(FrozenModel):
    title: Text
    items: Annotated[tuple[MealItem, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        if len({item.key for item in self.items}) != len(self.items):
            raise ValueError("meal item keys must be unique")
        return self


def serving_fraction(item: MealItem, portion: Portion) -> Fraction | None:
    """Convert only on an evidenced label basis; tsp:tbsp:cup is 1:3:48."""
    product = item.product
    if not verified_source(product, product.package_source_ref):
        return None
    value = Fraction(portion.value)
    if portion.unit == "serving":
        return value if product.serving else None
    if portion.unit == "package":
        if product.servings_per_container is not None:
            return value * Fraction(product.servings_per_container)
        if product.net_amount is None or product.serving is None:
            return None
        amount = exact_amount_in(
            product.net_amount, product.serving.unit, product
        )
        return (
            value * amount / Fraction(product.serving.value)
            if amount is not None
            else None
        )
    if portion.unit in HOUSEHOLD_TEASPOONS:
        label = item.verified_household_serving
        if label is None or product.serving is None:
            return None
        return (
            value
            * HOUSEHOLD_TEASPOONS[portion.unit]
            / (Fraction(label.value) * HOUSEHOLD_TEASPOONS[label.unit])
        )
    if product.serving is None:
        return None
    amount = exact_amount_in(
        Amount(value=portion.value, unit=portion.unit),
        product.serving.unit,
        product,
    )
    return (
        amount / Fraction(product.serving.value)
        if amount is not None
        else None
    )


def portion_amount(
    item: MealItem, portion: Portion, unit: Unit
) -> Fraction | None:
    """A known physical amount does not require a serving-size label."""
    product = item.product
    if portion.unit in ("g", "ml", "each"):
        physical_unit: Unit = (
            "g"
            if portion.unit == "g"
            else "ml" if portion.unit == "ml" else "each"
        )
        return exact_amount_in(
            Amount(value=portion.value, unit=physical_unit), unit, product
        )
    if not verified_source(product, product.package_source_ref):
        return None
    if portion.unit == "package" and product.net_amount is not None:
        amount = exact_amount_in(product.net_amount, unit, product)
        if amount is not None:
            return amount * Fraction(portion.value)
        # The net amount cannot reach this basis (e.g. grams to "each");
        # fall through to the declared serving count, which may.
    servings = serving_fraction(item, portion)
    amount = exact_amount_in(product.serving, unit, product)
    return (
        servings * amount
        if servings is not None and amount is not None
        else None
    )


def portion_cost(item: MealItem) -> tuple[Fraction | None, str]:
    quantity = item.purchase.quantity.quantity
    if (
        quantity is None
        or item.purchase.is_adjustment
        or item.purchase.extended_price < 0
    ):
        return None, "purchase_quantity_unknown_or_excluded"
    purchase = Portion(
        value=quantity.value, unit=quantity.unit, reference=quantity.reference
    )
    portion = item.portion
    price = Fraction(item.purchase.extended_price)
    if portion.unit == purchase.unit == "package":
        return (
            price * Fraction(portion.value) / Fraction(purchase.value),
            "explicit_package_counts",
        )
    # A measured portion uses compatible physical purchase/net quantity.
    # Household/serving inputs retain the declared label-serving basis.
    if portion.unit in ("g", "ml", "each") or purchase.unit in (
        "g",
        "ml",
        "each",
    ):
        for unit in ("g", "ml", "each"):
            physical_unit: Unit = (
                "g" if unit == "g" else "ml" if unit == "ml" else "each"
            )
            eaten = portion_amount(item, portion, physical_unit)
            bought = portion_amount(item, purchase, physical_unit)
            if eaten is not None and bought is not None:
                return price * eaten / bought, "physical_quantity:" + unit
    eaten_servings = serving_fraction(item, portion)
    bought_servings = serving_fraction(item, purchase)
    if eaten_servings is None or bought_servings is None:
        return None, "incompatible_or_missing_basis"
    return price * eaten_servings / bought_servings, "label_servings"


def calculate_meal(meal: Meal) -> dict[str, Any]:
    """Keep exact ratios until output; incomplete totals stay null."""
    rows: list[dict[str, Any]] = []
    raw_costs: list[Fraction | None] = []
    raw_nutrients: list[dict[str, Fraction]] = []
    for item in meal.items:
        product = item.product
        servings = serving_fraction(item, item.portion)
        cost, cost_basis = portion_cost(item)
        nutrients: dict[str, Fraction] = {}
        for fact in product.nutrients:
            if not verified_source(product, fact.source_ref):
                continue
            multiplier = servings
            if fact.basis != "serving":
                basis_units: dict[str, Unit] = {
                    "100g": "g",
                    "100ml": "ml",
                    "each": "each",
                }
                amount = portion_amount(
                    item, item.portion, basis_units[fact.basis]
                )
                multiplier = (
                    amount / (100 if fact.basis != "each" else 1)
                    if amount is not None
                    else None
                )
            if multiplier is not None:
                nutrients[fact.nutrient_id] = (
                    Fraction(fact.amount) * multiplier
                )
        raw_costs.append(cost)
        raw_nutrients.append(nutrients)
        rows.append(
            {
                "key": item.key,
                "product": product.name,
                "identity_kind": product.identity_kind,
                "cost_basis": cost_basis,
                "serving_rounding_allowance": str(
                    item.serving_rounding_allowance
                ),
                "portion": item.portion.model_dump(mode="json"),
                "label_servings": (
                    str(servings) if servings is not None else None
                ),
                "cost": str(ratio_money(cost)) if cost is not None else None,
                "nutrients": {
                    key: str(as_decimal(value))
                    for key, value in nutrients.items()
                },
                "purchase": item.purchase.model_dump(mode="json"),
                "assumptions": list(item.assumptions),
                "notes": list(item.calculation_notes),
                "sources": [
                    source.model_dump(mode="json")
                    for source in product.evidence
                ],
            }
        )
    totals = {}
    for key, unit in NUTRIENT_UNITS.items():
        present = [
            nutrients[key] for nutrients in raw_nutrients if key in nutrients
        ]
        subtotal = str(as_decimal(sum(present, Fraction())))
        totals[key] = {
            "unit": unit,
            "complete": len(present) == len(rows),
            "amount": subtotal if len(present) == len(rows) else None,
            "available_subtotal": subtotal if present else None,
            "items_with_value": len(present),
            "items": len(rows),
        }
    costs = [cost for cost in raw_costs if cost is not None]
    return {
        "title": meal.title,
        "input_hash": content_hash(meal.model_dump(mode="python")),
        "calculator_version": "portion-v1",
        "contains_generic_estimates": any(
            item.product.identity_kind == "generic" for item in meal.items
        ),
        "rows": rows,
        "nutrients": totals,
        "cost": (
            str(ratio_money(sum(costs, Fraction())))
            if len(costs) == len(rows)
            else None
        ),
        "available_cost_subtotal": str(ratio_money(sum(costs, Fraction()))),
        "cost_items": len(costs),
        "items": len(rows),
    }


def render_meal(result: dict[str, Any]) -> str:
    """Readable private report; do not present missing contributions as zero."""
    lines = [
        f"# {result['title']}",
        "",
        "| Product | Portion | Calories | Cost |",
        "|---|---|---:|---:|",
    ]
    for row in result["rows"]:
        display_name = row["product"] + (
            " (estimated; generic product)"
            if row["identity_kind"] == "generic"
            else ""
        )
        portion = row["portion"]
        kcal = row["nutrients"].get("208")
        shown_kcal = f"{Decimal(kcal):.1f}" if kcal is not None else "unknown"
        cost = "$" + row["cost"] if row["cost"] is not None else "unknown"
        lines.append(
            f"| {display_name} | {portion['value']} {portion['unit']} | {shown_kcal} | {cost} |"
        )
    if result["contains_generic_estimates"]:
        lines += [
            "",
            "This meal includes generic product estimates; complete coverage does not mean an exact product match.",
        ]
    calories = result["nutrients"]["208"]
    lines += [
        "",
        f"Portion cost: {'$' + result['cost'] if result['cost'] is not None else 'unknown'}.",
    ]
    if calories["complete"]:
        lines.append(
            f"Calories: {Decimal(calories['amount']):.1f} kcal (scaled source values)."
        )
    elif calories["available_subtotal"] is None:
        lines.append(
            f"Calories: unknown. No item carries a calorie value (0/{calories['items']} items)."
        )
    else:
        lines.append(
            f"Calories: incomplete. Available subtotal {Decimal(calories['available_subtotal']):.1f} kcal from {calories['items_with_value']}/{calories['items']} items."
        )
    lines += [
        "",
        "| Nutrient | Complete meal total | Available subtotal | Coverage |",
        "|---|---:|---:|---:|",
    ]
    for key, total in result["nutrients"].items():
        amount = (
            f"{Decimal(total['amount']):.2f} {total['unit']}"
            if total["complete"]
            else "unknown"
        )
        subtotal = (
            f"{Decimal(total['available_subtotal']):.2f} {total['unit']}"
            if total["items_with_value"]
            else "unknown"
        )
        lines.append(
            f"| {NUTRIENT_NAMES[key]} | {amount} | {subtotal} | {total['items_with_value']}/{total['items']} items |"
        )
    lines += [
        "",
        "Quantities are explicit scenario inputs. Costs use the supplied item prices and exclude unallocated taxes or discounts. Label values are rounded; portion scaling is not a measurement of food eaten or pan residue.",
    ]
    for row in result["rows"]:
        quantity_result = row["purchase"]["quantity"]
        quantity = quantity_result["quantity"]
        shown_quantity = (
            f"{quantity['value']} {quantity['unit']}"
            if quantity is not None
            else quantity_result["status"]
        )
        lines += [
            "",
            f"**{row['product']}**",
            f"Portion basis: {row['portion']['reference']}",
            f"Cost basis: {row['cost_basis']}",
            f"Explicit rounding allowance per label serving: {row['serving_rounding_allowance']} (in the serving unit).",
            f"Effective purchase quantity: {shown_quantity}.",
            f"Purchase quantity basis: {quantity_result['reason']}",
        ]
        if row["assumptions"]:
            lines.append(
                "Base input notes (scenario overrides take precedence):"
            )
        lines += [f"- {note}" for note in row["assumptions"]]
        lines += [f"- Calculator note: {note}" for note in row["notes"]]
        lines += [
            f"- Source ({s['verification']}): [{s['record_id']}]({s['reference']})"
            for s in row["sources"]
        ]
    lines += [
        "",
        f"Input hash: `{result['input_hash']}`; calculator: `{result['calculator_version']}`.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown"
    )
    parser.add_argument(
        "--portion",
        action="append",
        default=[],
        metavar="KEY=VALUE:UNIT",
        help="Explicit portion override, for example butter=1:tbsp",
    )
    parser.add_argument(
        "--quantity",
        action="append",
        default=[],
        metavar="KEY=VALUE:UNIT",
        help="Explicit purchased quantity override, for example eggs=12:each",
    )
    args = parser.parse_args()
    try:
        value = json.loads(args.input.read_text())
        overrides = [("portion", v) for v in args.portion] + [
            ("quantity", v) for v in args.quantity
        ]
        for kind, override in overrides:
            key, amount_unit = override.split("=", 1)
            amount, unit = amount_unit.split(":", 1)
            matches = [item for item in value["items"] if item["key"] == key]
            if len(matches) != 1:
                raise ValueError(
                    "portion override must identify exactly one item"
                )
            if kind == "portion":
                matches[0]["portion"] = {
                    "value": amount,
                    "unit": unit,
                    "reference": "Explicit CLI scenario override",
                }
            else:
                matches[0]["purchase"]["quantity"] = {
                    "status": "known",
                    "reason": "Explicit CLI purchase quantity scenario override",
                    "quantity": {
                        "value": amount,
                        "unit": unit,
                        "method": "user",
                        "reference": "Explicit CLI purchase quantity scenario override",
                    },
                }
        result = calculate_meal(Meal.model_validate(value))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(
        (
            json.dumps(result, indent=2)
            if args.format == "json"
            else render_meal(result)
        ),
        end="\n",
    )


if __name__ == "__main__":
    main()
