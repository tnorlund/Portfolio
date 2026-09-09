"""Immutable facts, explicit physical bases, and provenance contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)


def decimal_input(value: object) -> Decimal:
    """Reject binary float coercion, nonfinite values, and excess precision."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(
            "decimal values require a string, integer, or Decimal"
        )
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid decimal value") from error
    if not number.is_finite():
        raise ValueError("decimal values must be finite")
    if abs(number) > Decimal("1e12") or (
        number != 0 and abs(number) < Decimal("1e-12")
    ):
        raise ValueError("decimal value outside supported magnitude")
    return number


Number = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(max_digits=24, decimal_places=12),
]
Positive = Annotated[Number, Field(gt=0)]
# Canonical physical quantities include exact unit-conversion products. Their
# scale can exceed that of source facts (e.g. 1.125 US fl oz in millilitres).
PhysicalPositive = Annotated[
    Decimal, BeforeValidator(decimal_input), Field(gt=0)
]
Nonnegative = Annotated[Number, Field(ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=2048, pattern=r"\S")]
Unit = Literal["g", "ml", "each"]
NutrientId = Literal[
    "208",
    "203",
    "204",
    "205",
    "269",
    "539",
    "291",
    "307",
    "606",
    "605",
    "601",
    "301",
    "303",
    "306",
    "328",
]
NUTRIENT_UNITS: dict[str, str] = {
    "208": "kcal",
    "203": "g",
    "204": "g",
    "205": "g",
    "269": "g",
    "539": "g",
    "291": "g",
    "307": "mg",
    "606": "g",
    "605": "g",
    "601": "mg",
    "301": "mg",
    "303": "mg",
    "306": "mg",
    "328": "ug",
}


class FrozenModel(BaseModel):
    """Validate input at boundaries; nested collections are immutable tuples."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Amount(FrozenModel):
    value: PhysicalPositive
    unit: Unit


class SourceEvidence(FrozenModel):
    evidence_id: Text
    source: Literal["fdc", "off", "tj", "manual", "synthetic"]
    record_id: Text
    reference: Text
    observed_on: date
    verification: Literal["unverified", "source_record", "user", "synthetic"]
    license: Text
    public_allowed: bool = False
    payload_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = (
        None
    )

    @model_validator(mode="after")
    def validate_origin(self) -> Self:
        if (self.source == "synthetic") != (self.verification == "synthetic"):
            raise ValueError("synthetic evidence must be explicitly labelled")
        if self.public_allowed and self.verification == "unverified":
            raise ValueError("unverified evidence cannot be public")
        return self


class NutrientFact(FrozenModel):
    nutrient_id: NutrientId
    amount: Nonnegative
    unit: Literal["g", "mg", "ug", "kcal"]
    basis: Literal["100g", "100ml", "serving", "each"]
    source_ref: Text

    @model_validator(mode="after")
    def validate_nutrient_unit(self) -> Self:
        if NUTRIENT_UNITS[self.nutrient_id] != self.unit:
            raise ValueError(
                "nutrient unit does not match canonical identifier"
            )
        return self


class Product(FrozenModel):
    product_id: Text
    name: Text
    brand: str = ""
    identity_kind: Literal["exact", "generic"]
    evidence: Annotated[tuple[SourceEvidence, ...], Field(min_length=1)]
    nutrients: tuple[NutrientFact, ...] = ()
    net_amount: Amount | None = None
    serving: Amount | None = None
    serving_household: str | None = None
    servings_per_container: Positive | None = None
    package_source_ref: Text | None = None
    mass_per_each_g: Positive | None = None
    density_g_per_ml: Positive | None = None
    conversion_source_ref: Text | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        sources = {source.evidence_id for source in self.evidence}
        if len(sources) != len(self.evidence):
            raise ValueError("duplicate source evidence ID")
        if len({n.nutrient_id for n in self.nutrients}) != len(self.nutrients):
            raise ValueError("duplicate nutrient identifier")
        if any(n.source_ref not in sources for n in self.nutrients):
            raise ValueError("nutrient source is not in product evidence")
        if (
            any(n.basis == "serving" for n in self.nutrients)
            and not self.serving
        ):
            raise ValueError("per-serving facts require an explicit serving")
        if (
            self.net_amount or self.serving
        ) and self.package_source_ref not in sources:
            raise ValueError("package/serving amounts require source evidence")
        conversions = self.mass_per_each_g or self.density_g_per_ml
        if conversions and self.conversion_source_ref not in sources:
            raise ValueError("physical conversions require source evidence")
        if (
            self.conversion_source_ref
            and self.conversion_source_ref not in sources
        ):
            raise ValueError("unknown physical conversion source")
        if self.servings_per_container is not None and self.serving is None:
            raise ValueError("container servings require a declared serving")
        return self

    @property
    def content_hash(self) -> str:
        return content_hash(self.model_dump(mode="python"))


class QuantityEvidence(FrozenModel):
    value: PhysicalPositive
    unit: Literal["g", "ml", "each", "package"]
    method: Literal["receipt", "user"]
    reference: Text


class QuantityResolution(FrozenModel):
    status: Literal["known", "unknown", "conflict"]
    reason: Text
    quantity: QuantityEvidence | None = None

    @model_validator(mode="after")
    def validate_known(self) -> Self:
        if (self.status == "known") != (self.quantity is not None):
            raise ValueError("only known quantities carry usable evidence")
        return self


class Purchase(FrozenModel):
    extended_price: Number
    quantity: QuantityResolution
    is_adjustment: bool = False


class PurchasedNutrient(FrozenModel):
    nutrient_id: NutrientId
    amount: Decimal
    unit: Literal["g", "mg", "ug", "kcal"]
    source_ref: Text
    calculation_basis: Literal["100g", "100ml", "serving", "each"]


class CostingResult(FrozenModel):
    quantity_status: Literal["known", "unknown", "conflict", "excluded"]
    identity_kind: Literal["exact", "generic"]
    purchased_amount: Amount | None = None
    servings_purchased: Decimal | None = None
    servings_derived: bool = False
    cost_per_serving: Decimal | None = None
    cost_per_100g: Decimal | None = None
    cost_per_100ml: Decimal | None = None
    nutrients: tuple[PurchasedNutrient, ...] = ()
    unavailable_nutrients: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def content_hash(value: Any) -> str:
    """Canonical decimal strings make equivalent number spellings identical."""

    def canonical(item: Any) -> Any:
        if isinstance(item, Decimal):
            if item == 0:
                return "0"
            rendered = format(item, "f")
            return (
                rendered.rstrip("0").rstrip(".")
                if "." in rendered
                else rendered
            )
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, dict):
            return {key: canonical(val) for key, val in item.items()}
        if isinstance(item, (tuple, list)):
            return [canonical(val) for val in item]
        return item

    payload = json.dumps(
        canonical(value), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()
