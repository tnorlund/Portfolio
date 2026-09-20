"""An honest contract evaluator with independent, explicit expected outputs."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from receipt_nutrition.costing import cost_purchase
from receipt_nutrition.models import (
    FrozenModel,
    Number,
    Product,
    Purchase,
    Text,
    decimal_input,
)

# Expectations are compared exactly against computed values, which can carry
# more decimal places than a label input (e.g. 1.125 fl oz in millilitres),
# so they are not capped at Number's 12 places.
Expected = Annotated[Decimal, BeforeValidator(decimal_input)]


class ExpectedCost(FrozenModel):
    quantity_status: Literal["known", "unknown", "conflict", "excluded"]
    servings: Expected | None = None
    cost_per_serving: Expected | None = None
    cost_per_100g: Expected | None = None
    cost_per_100ml: Expected | None = None
    energy: Expected | None = None
    protein: Expected | None = None
    sodium: Expected | None = None


class ContractCase(FrozenModel):
    case_id: Text
    product_id: Text
    purchase: Purchase
    expected: ExpectedCost


class ContractFixture(FrozenModel):
    schema_version: Literal[1]
    evidence_tier: Literal["synthetic"]
    description: Text
    products: Annotated[tuple[Product, ...], Field(min_length=1)]
    cases: Annotated[tuple[ContractCase, ...], Field(min_length=30)]

    @model_validator(mode="after")
    def validate_fixture(self) -> Self:
        ids = {product.product_id for product in self.products}
        if len(ids) != len(self.products):
            raise ValueError("duplicate fixture product ID")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate contract case ID")
        if any(case.product_id not in ids for case in self.cases):
            raise ValueError("case references absent product")
        if any(
            source.source != "synthetic"
            for product in self.products
            for source in product.evidence
        ):
            raise ValueError("contract fixtures must use synthetic evidence")
        return self


def evaluate_contract(path: Path) -> dict[str, object]:
    """Never present synthetic arithmetic as real model/corpus accuracy."""
    data = path.read_bytes()
    fixture = ContractFixture.model_validate(json.loads(data))
    products = {product.product_id: product for product in fixture.products}
    failures: list[dict[str, object]] = []
    for case in fixture.cases:
        result = cost_purchase(products[case.product_id], case.purchase)
        nutrients = {
            item.nutrient_id: item.amount for item in result.nutrients
        }
        actual: dict[str, str | Decimal | None] = {
            "quantity_status": result.quantity_status,
            "servings": result.servings_purchased,
            "cost_per_serving": result.cost_per_serving,
            "cost_per_100g": result.cost_per_100g,
            "cost_per_100ml": result.cost_per_100ml,
            "energy": nutrients.get("208"),
            "protein": nutrients.get("203"),
            "sodium": nutrients.get("307"),
        }
        mismatches = {
            key: {"expected": str(expected), "actual": str(actual[key])}
            for key, expected in case.expected.model_dump().items()
            if expected != actual[key]
        }
        if mismatches:
            failures.append({"case_id": case.case_id, "fields": mismatches})
    return {
        "mode": "contract",
        "evidence_tier": "synthetic",
        "fixture_sha256": hashlib.sha256(data).hexdigest(),
        "cases_total": len(fixture.cases),
        "cases_passed": len(fixture.cases) - len(failures),
        "failures": failures,
        "contract_passed": not failures,
        "real_catalog_quality": "NOT RUN",
        "real_model_quality": "NOT RUN",
        "dev_end_to_end": "NOT RUN",
        "automatic_acceptance_enabled": False,
    }
