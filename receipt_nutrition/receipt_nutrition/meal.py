"""Build a private meal from receipt line pointers and portions.

Every number that is not read from a receipt is either sourced (a stored
price observation, a label) or stamped as an assumption; unknowns stay
unknown. ``python -m receipt_nutrition.meal --help`` lists the flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Protocol

from receipt_dynamo.entities.nutrition_support import (
    check_revision,
    nutrition_json,
)
from receipt_dynamo.entities.product_alias_observation import (
    ProductAliasObservation,
    product_alias_id,
)

from receipt_nutrition.costing import ratio_money
from receipt_nutrition.models import (
    HouseholdServing,
    Product,
    Purchase,
    QuantityEvidence,
    QuantityResolution,
    decimal_input,
)
from receipt_nutrition.persistence import product_from_record
from receipt_nutrition.portions import (
    Meal,
    MealItem,
    Portion,
    calculate_meal,
    render_meal,
)
from receipt_nutrition.prices import (
    RateChoice,
    RateSelection,
    owner_rate,
    select_rate,
)
from receipt_nutrition.quantity import resolve_explicit_quantity
from receipt_nutrition.resolution import (
    LineResolution,
    derive_alias_keys,
    load_fleet_alias_map,
    resolve_line,
)
from receipt_nutrition.units import UNIT_FACTORS, as_decimal

CORE_NUTRIENTS = ("208", "203", "204", "205", "269", "307")
PORTION_UNITS = ("pkg", "g", "ml", "cup", "tbsp", "tsp", "each", "serving")
RATE_UNITS = ("lb", "kg", "oz")
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_POINTER = 2
EXIT_UNKNOWNS = 3
EXIT_PENDING = 4
REPORT_VERSION = "meal-v2"
KG = Fraction(1000)


class MealError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class CoverageChecker(Protocol):
    def __call__(
        self, *, merchant_slug: str, on: date, as_of: date
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PortionSpec:
    value: Decimal | None  # None means "all"
    unit: str | None


@dataclass(frozen=True)
class ItemSpec:
    key: str
    source: Literal["line", "latest", "product", "none"]
    portion: PortionSpec
    image_id: str | None = None
    receipt_id: int | None = None
    item_index: int | None = None
    merchant_slug: str | None = None
    alias_kind: str | None = None
    alias_text: str | None = None
    product_id: str | None = None
    product_revision: str | None = None
    on: date | None = None


@dataclass
class Overrides:
    rates: dict[str, list[tuple[Decimal, str]]] = field(default_factory=dict)
    packages: dict[str, int] = field(default_factory=dict)
    quantities: dict[str, tuple[Decimal, str]] = field(default_factory=dict)
    prices: dict[str, Decimal] = field(default_factory=dict)
    generics: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Rate:
    """A chosen rate with the price and unit it was quoted in."""

    price_per_unit: Decimal
    unit: str
    choice: RateChoice

    @property
    def source(self) -> str:
        return self.choice.source

    @property
    def effective_on(self) -> date:
        return self.choice.effective_on


@dataclass
class ResolvedItem:
    spec: ItemSpec
    product: Product | None = None
    product_revision: str | None = None
    merchant_slug: str | None = None
    purchase_date: date | None = None
    price: Decimal | None = None
    line_text: str = ""
    raw_quantity: Any = None
    raw_unit_price: Any = None
    resolution: LineResolution | None = None
    assumptions: list[str] = field(default_factory=list)
    band: list[Rate] = field(default_factory=list)
    coverage: dict[str, Any] | None = None
    quantity: QuantityResolution | None = None
    meal_item: MealItem | None = None
    unknown_reason: str | None = None


# ---------------------------------------------------------------- parsing


def _parse_portion(text: str) -> PortionSpec:
    if text == "all":
        return PortionSpec(None, None)
    for unit in sorted(PORTION_UNITS, key=len, reverse=True):
        if text.endswith(unit):
            number = text[: -len(unit)].strip()
            if not number:
                break
            return PortionSpec(decimal_input(number), unit)
    raise MealError(
        f"bad portion {text!r}; use <n><unit> or all", EXIT_POINTER
    )


def parse_item(text: str) -> ItemSpec:
    if "=" not in text:
        raise MealError(
            f"--item needs KEY=SRC:PORTION, got {text!r}", EXIT_POINTER
        )
    key, rest = text.split("=", 1)
    parts = rest.split(":")
    kind = parts[0]
    if kind == "line" and len(parts) == 5:
        return ItemSpec(
            key,
            "line",
            _parse_portion(parts[4]),
            image_id=parts[1],
            receipt_id=int(parts[2]),
            item_index=int(parts[3]),
        )
    if kind == "latest" and len(parts) >= 4:
        alias = ":".join(parts[2:-1])
        alias_kind, _, alias_text = alias.partition("#")
        if alias_kind not in ("ITEM", "TEXT") or not alias_text:
            raise MealError(f"bad latest alias {alias!r}", EXIT_POINTER)
        return ItemSpec(
            key,
            "latest",
            _parse_portion(parts[-1]),
            merchant_slug=parts[1],
            alias_kind=alias_kind,
            alias_text=alias_text,
        )
    if kind == "product" and len(parts) == 3:
        product_id, _, revision = parts[1].rpartition("@")
        if not product_id or not revision:
            raise MealError(
                "product source needs <product_id>@<revision>", EXIT_POINTER
            )
        return ItemSpec(
            key,
            "product",
            _parse_portion(parts[2]),
            product_id=product_id,
            product_revision=revision,
        )
    if kind == "none" and len(parts) == 4:
        return ItemSpec(
            key,
            "none",
            _parse_portion(parts[3]),
            merchant_slug=parts[1],
            on=date.fromisoformat(parts[2]),
        )
    raise MealError(f"bad --item source {rest!r}", EXIT_POINTER)


def _key_value(flag: str, text: str) -> tuple[str, str]:
    if "=" not in text:
        raise MealError(f"{flag} needs KEY=VALUE, got {text!r}", EXIT_POINTER)
    key, value = text.split("=", 1)
    return key, value


def parse_overrides(args: argparse.Namespace) -> Overrides:
    overrides = Overrides()
    for text in args.rate:
        key, value = _key_value("--rate", text)
        price, _, unit = value.rpartition(":")
        if unit not in RATE_UNITS or not price:
            raise MealError(
                f"--rate needs KEY=<price>:<lb|kg|oz>", EXIT_POINTER
            )
        overrides.rates.setdefault(key, []).append(
            (decimal_input(price), unit)
        )
    for text in args.assume_package:
        key, value = _key_value("--assume-package", text)
        if not value.isdigit() or int(value) < 1:
            raise MealError(
                "--assume-package needs an explicit package count KEY=N",
                EXIT_POINTER,
            )
        overrides.packages[key] = int(value)
    for text in args.quantity:
        key, value = _key_value("--quantity", text)
        number, _, unit = value.rpartition(":")
        if unit not in ("g", "ml", "each") or not number:
            raise MealError(
                "--quantity needs KEY=<n>:<g|ml|each>", EXIT_POINTER
            )
        overrides.quantities[key] = (decimal_input(number), unit)
    for text in args.price:
        key, value = _key_value("--price", text)
        overrides.prices[key] = decimal_input(value)
    for text in args.generic:
        key, value = _key_value("--generic", text)
        product_id, _, revision = value.rpartition("@")
        if not product_id or not revision:
            raise MealError(
                "--generic needs KEY=<product_id>@<revision>", EXIT_POINTER
            )
        overrides.generics[key] = (product_id, revision)
    return overrides


# ---------------------------------------------------------------- loading


def _line_items(client: Any, image_id: str, receipt_id: int) -> list[Any]:
    result = client.get_receipt_line_items_from_receipt(image_id, receipt_id)
    return list(result[0] if isinstance(result, tuple) else result)


def _purchase_date(
    client: Any, image_id: str, receipt_id: int
) -> tuple[date | None, str | None]:
    summary = client.get_receipt_summary(image_id, receipt_id)
    raw = getattr(summary, "date", None) or getattr(
        summary, "timestamp_added", None
    )
    if isinstance(raw, datetime):
        purchase = raw.date()
    elif isinstance(raw, date):
        purchase = raw
    elif isinstance(raw, str) and raw:
        purchase = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    else:
        purchase = None
    return purchase, getattr(summary, "merchant_name", None)


def _load_product(client: Any, product_id: str, revision: str) -> Product:
    record = client.get_food_product(product_id, revision)
    if record is None:
        raise MealError(
            f"product {product_id}@{revision} does not exist", EXIT_POINTER
        )
    return product_from_record(record)


def _resolve_pointer(
    client: Any,
    item: ResolvedItem,
    *,
    as_of: date,
    fleet_map: dict[str, str] | None,
) -> None:
    from receipt_nutrition.resolution import resolve_merchant_slug

    spec = item.spec
    assert spec.image_id is not None and spec.receipt_id is not None
    lines = _line_items(client, spec.image_id, spec.receipt_id)
    matches = [
        line
        for line in lines
        if getattr(line, "item_index", None) == spec.item_index
    ]
    if len(matches) != 1:
        raise MealError(
            f"{spec.key}: no line item {spec.item_index} on receipt "
            f"{spec.image_id}:{spec.receipt_id}",
            EXIT_POINTER,
        )
    line = matches[0]
    purchase_date, summary_merchant = _purchase_date(
        client, spec.image_id, spec.receipt_id
    )
    # The line's own merchant string and the receipt summary's may differ
    # ("COSTCO" vs "Costco Wholesale"); both come from the receipt, so try
    # the line's first and fall back to the summary's when nothing is aliased.
    candidates: list[str] = []
    for raw_merchant in (
        getattr(line, "merchant_name", None),
        summary_merchant,
    ):
        slug = resolve_merchant_slug(raw_merchant, fleet_alias_map=fleet_map)
        if slug and slug not in candidates:
            candidates.append(slug)
    if not candidates:
        raise MealError(f"{spec.key}: receipt has no merchant", EXIT_POINTER)
    item.purchase_date = purchase_date
    item.line_text = getattr(line, "raw_text", "") or line.name
    item.price = decimal_input(str(line.price))
    item.raw_quantity = getattr(line, "quantity", None)
    item.raw_unit_price = getattr(line, "unit_price", None)
    resolution = None
    for slug in candidates:
        resolution = resolve_line(
            client,
            merchant_slug=slug,
            line_text=line.name,
            size_evidence=None,
            as_of=as_of,
        )
        item.merchant_slug = slug
        if resolution.status != "unaliased":
            break
    assert resolution is not None
    item.resolution = resolution
    if resolution.status == "matched":
        assert resolution.product_id and resolution.product_revision
        item.product = _load_product(
            client, resolution.product_id, resolution.product_revision
        )
        item.product_revision = resolution.product_revision
    elif resolution.status == "pending":
        raise MealError(
            f"{spec.key}: alias pending for {slug} "
            f"{[ (r.kind, r.text) for r in resolution.alias_refs ]} "
            f"({resolution.reason})",
            EXIT_PENDING,
        )
    else:
        raise MealError(
            f"{spec.key}: no usable alias for {slug!r} text "
            f"{item.line_text!r} ({resolution.status}: {resolution.reason})",
            EXIT_POINTER,
        )


def _resolve_latest(
    client: Any,
    item: ResolvedItem,
    *,
    as_of: date,
    fleet_map: dict[str, str] | None,
) -> None:
    spec = item.spec
    assert spec.merchant_slug and spec.alias_kind and spec.alias_text
    result = client.list_receipt_line_items_by_merchant(spec.merchant_slug)
    lines = list(result[0] if isinstance(result, tuple) else result)
    candidates = []
    for line in lines:
        keys = derive_alias_keys(line.name, merchant_slug=spec.merchant_slug)
        if (spec.alias_kind, spec.alias_text) in keys.lookups:
            purchase_date, _ = _purchase_date(
                client, line.image_id, line.receipt_id
            )
            candidates.append((purchase_date or date.min, line))
    if not candidates:
        raise MealError(
            f"{spec.key}: no line at {spec.merchant_slug} matches "
            f"{spec.alias_kind}#{spec.alias_text}",
            EXIT_POINTER,
        )
    candidates.sort(key=lambda pair: pair[0])
    latest = candidates[-1][1]
    item.spec = ItemSpec(
        spec.key,
        "line",
        spec.portion,
        image_id=latest.image_id,
        receipt_id=latest.receipt_id,
        item_index=latest.item_index,
    )
    _resolve_pointer(client, item, as_of=as_of, fleet_map=fleet_map)


# ---------------------------------------------------------------- quantity


def _rate_to_grams(price: Decimal, rate: Rate) -> Fraction:
    return Fraction(price) / rate.choice.price_per_kg * KG


def _needed_allowance(product: Product) -> Fraction | None:
    """The smallest rounding allowance that reconciles net and servings."""
    from receipt_nutrition.units import exact_amount_in, verified_source

    if not (
        product.net_amount
        and product.serving
        and product.servings_per_container is not None
        and verified_source(product, product.package_source_ref)
    ):
        return None
    net = exact_amount_in(product.net_amount, product.serving.unit, product)
    if net is None:
        return None
    count = Fraction(product.servings_per_container)
    declared = count * Fraction(product.serving.value)
    if net == declared:
        return None
    return abs(net - declared) / count


def _decide_quantity(
    client: Any,
    item: ResolvedItem,
    overrides: Overrides,
    *,
    as_of: date,
) -> None:
    spec = item.spec
    product = item.product
    assert product is not None
    if spec.key in overrides.quantities:
        value, unit = overrides.quantities[spec.key]
        item.quantity = QuantityResolution(
            status="known",
            reason="owner_quantity_override",
            quantity=QuantityEvidence(
                value=value, unit=unit, method="user", reference="--quantity"
            ),
        )
        item.assumptions.append(f"quantity:{spec.key}:{value}:{unit}")
        return
    if spec.key in overrides.packages:
        count = overrides.packages[spec.key]
        item.quantity = QuantityResolution(
            status="known",
            reason="owner_package_count_estimated",
            quantity=QuantityEvidence(
                value=Decimal(count),
                unit="package",
                method="user",
                reference="--assume-package",
            ),
        )
        item.assumptions.append(f"assume_package:{spec.key}:{count}")
        return
    explicit = None
    if item.price is not None and item.line_text:
        explicit = resolve_explicit_quantity(
            item.line_text, item.raw_quantity, item.raw_unit_price, item.price
        )
    if explicit is not None and explicit.status == "known":
        item.quantity = explicit
        return
    if explicit is not None and explicit.status == "conflict":
        item.quantity = explicit
        item.unknown_reason = f"receipt_quantity_conflict:{explicit.reason}"
        return
    if product.sold_by == "weight" and item.price is not None:
        rate = _select_weight_rate(client, item, overrides, as_of=as_of)
        if rate is not None:
            grams = _rate_to_grams(item.price, rate)
            # An inferred weight is an assumption; a milligram is far below
            # the rate's own uncertainty and keeps the value representable.
            item.quantity = QuantityResolution(
                status="known",
                reason=f"inferred_from_rate:{rate.source}",
                quantity=QuantityEvidence(
                    value=as_decimal(grams).quantize(Decimal("0.001")),
                    unit="g",
                    method="user",
                    reference=(
                        f"price / rate ({rate.source} {rate.price_per_unit}"
                        f" per {rate.unit})"
                    ),
                ),
            )
            return
    item.quantity = explicit or QuantityResolution(
        status="unknown", reason="no_explicit_unit"
    )
    item.unknown_reason = item.unknown_reason or "quantity_unknown"


def _select_weight_rate(
    client: Any, item: ResolvedItem, overrides: Overrides, *, as_of: date
) -> Rate | None:
    spec = item.spec
    purchase_date = item.purchase_date or as_of
    if spec.key in overrides.rates:
        price, unit = overrides.rates[spec.key][0]
        rate = Rate(price, unit, owner_rate(price, unit, as_of=purchase_date))
        item.assumptions.append(f"rate:{spec.key}:owner:{price}:{unit}")
        item.band = []
        return rate
    if item.resolution is None or item.merchant_slug is None:
        return None
    for ref in item.resolution.alias_refs:
        if ref.kind != "ITEM":
            continue
        observations = client.list_price_observations(
            item.merchant_slug, ref.kind, ref.text
        )
        by_sk = {
            observation.sort_key: observation for observation in observations
        }
        selection: RateSelection = select_rate(
            observations, purchase_date=purchase_date
        )
        if selection.primary is None:
            continue

        def quoted(choice: RateChoice) -> Rate:
            observation = by_sk[choice.sk]
            return Rate(observation.price_per_unit, observation.unit, choice)

        primary = quoted(selection.primary)
        item.assumptions.append(
            f"rate:{spec.key}:observation:{primary.effective_on.isoformat()}:"
            f"{primary.source}:{primary.price_per_unit}:{primary.unit}"
        )
        item.band = [
            quoted(choice)
            for choice in selection.band
            if choice.sk != selection.primary.sk
        ]
        for rate in item.band:
            item.assumptions.append(
                f"band:{spec.key}:{rate.price_per_unit}:{rate.unit}"
            )
        return primary
    return None


# ---------------------------------------------------------------- portions


def _portion(item: ResolvedItem) -> Portion | None:
    spec = item.spec.portion
    if spec.value is None:
        quantity = item.quantity.quantity if item.quantity else None
        if quantity is None:
            return None
        return Portion(
            value=quantity.value, unit=quantity.unit, reference="all purchased"
        )
    unit = "package" if spec.unit == "pkg" else spec.unit
    return Portion(value=spec.value, unit=unit, reference=f"--item portion")


def _household(item: ResolvedItem) -> HouseholdServing | None:
    product = item.product
    if product is None or product.household is None:
        return None
    equivalence = product.household
    if equivalence.unit == "each":
        return None
    item.assumptions.append(
        f"household:{item.spec.key}:{equivalence.unit}:label"
    )
    return HouseholdServing(
        value=equivalence.value,
        unit=equivalence.unit,
        source_ref=equivalence.source_ref,
    )


def _build_meal_item(
    item: ResolvedItem, allowance_mode: str
) -> MealItem | None:
    product = item.product
    if product is None or item.quantity is None or item.price is None:
        return None
    portion = _portion(item)
    if portion is None:
        item.unknown_reason = item.unknown_reason or "portion_needs_quantity"
        return None
    allowance = Fraction(0)
    if allowance_mode == "auto":
        needed = _needed_allowance(product)
        if needed is not None:
            cap = min(Fraction(1, 2), Fraction(product.serving.value) / 20)
            if product.serving.unit == "each":
                cap = Fraction()
            if needed <= cap:
                allowance = needed
                item.assumptions.append(
                    f"allowance:{item.spec.key}:{as_decimal(needed)}"
                )
    household = _household(item)
    try:
        return MealItem(
            key=item.spec.key,
            product=product,
            purchase=Purchase(
                extended_price=item.price, quantity=item.quantity
            ),
            portion=portion,
            household_serving=household,
            assumptions=tuple(item.assumptions),
            serving_rounding_allowance=as_decimal(allowance),
        )
    except ValueError as error:
        item.unknown_reason = f"meal_item_invalid:{error}"
        return None


# ---------------------------------------------------------------- pointers


def _publish_pointers(client: Any, items: list[ResolvedItem]) -> None:
    observations: list[ProductAliasObservation] = []
    expectations: list[tuple[str, str, str, int | None]] = []
    seen: set[tuple[str, str, str]] = set()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in items:
        resolution = item.resolution
        spec = item.spec
        if resolution is None or item.merchant_slug is None:
            continue
        assert spec.image_id is not None and spec.receipt_id is not None
        assert spec.item_index is not None
        for ref in resolution.alias_refs:
            key = (item.merchant_slug, ref.kind, ref.text)
            if key in seen:
                continue
            seen.add(key)
            status = resolution.status if ref.revision else "no_match"
            matched = (
                resolution.status == "matched" and ref.revision is not None
            )
            observations.append(
                ProductAliasObservation(
                    alias_id=product_alias_id(*key),
                    merchant_slug=item.merchant_slug,
                    kind=ref.kind,
                    text=ref.text,
                    status=(
                        "matched"
                        if matched
                        else (
                            status
                            if status
                            in ("pending", "rejected", "not_food", "no_match")
                            else "no_match"
                        )
                    ),
                    alias_revision=ref.revision or 0,
                    image_id=spec.image_id,
                    receipt_id=spec.receipt_id,
                    item_index=spec.item_index,
                    observed_at=now,
                    product_id=resolution.product_id if matched else None,
                    product_revision=(
                        resolution.product_revision if matched else None
                    ),
                )
            )
            expectations.append((*key, ref.revision or None))
    if observations:
        client.publish_alias_observations(
            observations,
            alias_expectations=expectations,
            expected_table_name=client.table_name,
        )


# ---------------------------------------------------------------- report


def _unknown_fields(
    item: ResolvedItem, row: dict[str, Any] | None
) -> list[str]:
    key = item.spec.key
    unknown: list[str] = []
    if row is None or row["cost"] is None:
        unknown.append(f"{key}.cost")
    for nutrient in CORE_NUTRIENTS:
        if row is None or nutrient not in row["nutrients"]:
            unknown.append(f"{key}.{nutrient}")
    return unknown


def build_report(
    items: list[ResolvedItem],
    *,
    title: str,
    containers: int,
    allowance_mode: str,
    as_of: date,
) -> dict[str, Any]:
    meal_items = []
    for item in items:
        if item.product is not None:
            item.meal_item = _build_meal_item(item, allowance_mode)
        if item.meal_item is not None:
            meal_items.append(item.meal_item)
    result = (
        calculate_meal(
            Meal(title=title, items=tuple(meal_items)), containers=containers
        )
        if meal_items
        else None
    )
    rows_by_key = {
        row["key"]: row for row in (result["rows"] if result else [])
    }
    everything_rowed = result is not None and len(result["rows"]) == len(items)
    totals = None
    if result is not None:
        totals = {
            key: {
                **total,
                "items": len(items),
                "complete": total["complete"] and everything_rowed,
                "amount": total["amount"] if everything_rowed else None,
            }
            for key, total in result["nutrients"].items()
        }
    unknown: list[str] = []
    assumptions: list[str] = []
    per_item: list[dict[str, Any]] = []
    for item in items:
        row = rows_by_key.get(item.spec.key)
        unknown.extend(_unknown_fields(item, row))
        assumptions.extend(item.assumptions)
        if item.coverage is not None:
            assumptions.append(
                f"coverage:{item.spec.key}:{item.coverage['outcome']}"
                + (
                    f":{item.coverage['reason']}"
                    if item.coverage.get("reason")
                    else ""
                )
            )
            if item.coverage.get("window"):
                start, end = item.coverage["window"]
                assumptions.append(
                    f"coverage_window:{item.spec.key}:{start}:{end}"
                )
        per_item.append(
            {
                "key": item.spec.key,
                "source": item.spec.source,
                "pointer": (
                    f"{item.spec.image_id}:{item.spec.receipt_id}:{item.spec.item_index}"
                    if item.spec.image_id
                    else None
                ),
                "merchant_slug": item.merchant_slug,
                "purchase_date": (
                    item.purchase_date.isoformat()
                    if item.purchase_date
                    else None
                ),
                "product_id": (
                    item.product.product_id if item.product else None
                ),
                "product_revision": item.product_revision,
                "price": str(item.price) if item.price is not None else None,
                "quantity": (
                    item.quantity.model_dump(mode="json")
                    if item.quantity
                    else None
                ),
                "band": [
                    {
                        "price_per_unit": str(choice.price_per_unit),
                        "unit": choice.unit,
                        "source": choice.source,
                        "effective_on": choice.effective_on.isoformat(),
                    }
                    for choice in item.band
                ],
                "coverage": item.coverage,
                "unknown_reason": item.unknown_reason,
                "row": row,
            }
        )
    return {
        "title": title,
        "report_version": REPORT_VERSION,
        "as_of": as_of.isoformat(),
        "containers": containers,
        "items": per_item,
        "totals": totals,
        "cost": result["cost"] if result and everything_rowed else None,
        "available_cost_subtotal": (
            result["available_cost_subtotal"] if result else None
        ),
        "cost_items": result["cost_items"] if result else 0,
        "item_count": len(items),
        "input_hash": result["input_hash"] if result else None,
        "contains_generic_estimates": (
            result["contains_generic_estimates"] if result else False
        ),
        "unknown": unknown,
        "assumptions": assumptions,
        "calculator_version": result["calculator_version"] if result else None,
    }


def _fmt(value: str | None, digits: int = 1) -> str:
    return f"{Decimal(value):.{digits}f}" if value is not None else "unknown"


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['title']}",
        "",
        f"Per container, {report['containers']} containers, as of {report['as_of']}.",
        "",
        "| Item | Portion per container | Cost | kcal | Protein g | Fat g | Carb g | Sugar g | Sodium mg |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    n = report["item_count"]
    for item in report["items"]:
        row = item["row"]
        if row is None:
            portion = "unknown"
            cells = ["unknown"] * 7
        else:
            portion = f"{row['portion']['value']} {row['portion']['unit']}"
            nutrients = row["nutrients"]
            cells = ["$" + row["cost"] if row["cost"] else "unknown"] + [
                _fmt(nutrients.get(key)) for key in CORE_NUTRIENTS
            ]
        label = item["key"]
        if item["product_id"]:
            label += f" ({item['product_id']})"
        lines.append(f"| {label} | {portion} | " + " | ".join(cells) + " |")
    totals = report["totals"] or {}
    subtotal_cells = []
    if report["cost"] is not None:
        subtotal_cells.append("$" + report["cost"])
    elif report["cost_items"]:
        subtotal_cells.append(
            f"${report['available_cost_subtotal']} ({report['cost_items']}/{n})"
        )
    else:
        subtotal_cells.append("unknown")
    for key in CORE_NUTRIENTS:
        total = totals.get(key)
        if total is None or not total["items_with_value"]:
            subtotal_cells.append("unknown")
        elif total["complete"] and total["items"] == n:
            subtotal_cells.append(_fmt(total["amount"]))
        else:
            subtotal_cells.append(
                f"{_fmt(total['available_subtotal'])} ({total['items_with_value']}/{n})"
            )
    lines.append("| **subtotal** | | " + " | ".join(subtotal_cells) + " |")
    complete = report["cost"] is not None and all(
        totals.get(key, {}).get("complete") and totals[key]["items"] == n
        for key in CORE_NUTRIENTS
    )
    lines += [
        "",
        (
            "All items known; totals are complete."
            if complete
            else "Totals are available subtotals; unknown items are not zero."
        ),
    ]
    if report["unknown"]:
        lines += ["", "Unknown: " + ", ".join(report["unknown"])]
    if report["assumptions"]:
        lines += ["", "Assumptions:"]
        lines += [
            f"{index}. {text}"
            for index, text in enumerate(report["assumptions"], 1)
        ]
    bands = [item for item in report["items"] if item["band"]]
    if bands:
        lines += [
            "",
            "| Item | Band rate | Source | Effective |",
            "|---|---:|---|---|",
        ]
        for item in bands:
            for choice in item["band"]:
                lines.append(
                    f"| {item['key']} | {choice['price_per_unit']}/{choice['unit']} | {choice['source']} | {choice['effective_on']} |"
                )
    lines += ["", "Cost basis:"]
    for item in report["items"]:
        row = item["row"]
        quantity = item["quantity"]
        shown_quantity = (
            f"{quantity['quantity']['value']} {quantity['quantity']['unit']} ({quantity['reason']})"
            if quantity and quantity.get("quantity")
            else (
                quantity["status"] + ":" + quantity["reason"]
                if quantity
                else "unknown"
            )
        )
        lines.append(
            f"- {item['key']}: pointer {item['pointer'] or item['source']}, "
            f"price {item['price'] or 'unknown'}, quantity {shown_quantity}, "
            f"cost basis {row['cost_basis'] if row else 'unknown'}, "
            f"product {item['product_id'] or 'unknown'}@{item['product_revision'] or '-'}"
            + (
                f", coverage {item['coverage']['outcome']}"
                if item["coverage"]
                else ""
            )
        )
    if report["contains_generic_estimates"]:
        lines += ["", "Includes generic product estimates."]
    lines += [
        "",
        f"Report `{report['report_version']}`; calculator `{report['calculator_version']}`; input hash `{report['input_hash']}`.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="receipt_nutrition.meal", description=__doc__
    )
    parser.add_argument(
        "--item", action="append", default=[], metavar="KEY=SRC:PORTION"
    )
    parser.add_argument("--title", default="meal")
    parser.add_argument("--containers", type=int, default=1)
    parser.add_argument(
        "--rate", action="append", default=[], metavar="KEY=<price>:<lb|kg|oz>"
    )
    parser.add_argument(
        "--assume-package", action="append", default=[], metavar="KEY=N"
    )
    parser.add_argument(
        "--quantity",
        action="append",
        default=[],
        metavar="KEY=<n>:<g|ml|each>",
    )
    parser.add_argument(
        "--price", action="append", default=[], metavar="KEY=<amount>"
    )
    parser.add_argument(
        "--generic",
        action="append",
        default=[],
        metavar="KEY=<product_id>@<revision>",
    )
    parser.add_argument("--allowance", choices=("auto", "0"), default="auto")
    parser.add_argument("--coverage", choices=("on", "off"), default="on")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--table", default=None)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--save", type=Path, default=None)
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="skip alias-outcome pointer writes",
    )
    return parser


def run(
    argv: list[str] | None,
    *,
    client: Any = None,
    coverage_checker: CoverageChecker | None = None,
    fleet_map: dict[str, str] | None = None,
    stdout: Any = None,
) -> int:
    out = stdout or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        specs = [parse_item(text) for text in args.item]
        if not specs:
            raise MealError("at least one --item is required", EXIT_POINTER)
        if len({spec.key for spec in specs}) != len(specs):
            raise MealError("item keys must be unique", EXIT_POINTER)
        overrides = parse_overrides(args)
        as_of = args.as_of or date.today()
        if args.containers < 1:
            raise MealError("--containers must be >= 1", EXIT_POINTER)
        needs_client = any(spec.source != "none" for spec in specs)
        if client is None and needs_client:
            if not args.table:
                raise MealError(
                    "--table is required for receipt lookups", EXIT_POINTER
                )
            from receipt_dynamo import DynamoClient

            client = DynamoClient(args.table)
        if fleet_map is None:
            fleet_map = load_fleet_alias_map()
        items = [ResolvedItem(spec=spec) for spec in specs]
        for item in items:
            spec = item.spec
            if spec.source == "line":
                _resolve_pointer(
                    client, item, as_of=as_of, fleet_map=fleet_map
                )
            elif spec.source == "latest":
                _resolve_latest(client, item, as_of=as_of, fleet_map=fleet_map)
            elif spec.source == "product":
                assert spec.product_id and spec.product_revision
                item.product = _load_product(
                    client, spec.product_id, spec.product_revision
                )
                item.product_revision = spec.product_revision
                item.price = overrides.prices.get(spec.key)
                if item.price is None:
                    item.unknown_reason = "no_price"
            else:
                item.unknown_reason = "no_receipt"
                if args.coverage == "on":
                    if coverage_checker is None:
                        item.coverage = {
                            "outcome": "lookup_unavailable",
                            "reason": "no_checker",
                        }
                    else:
                        assert spec.merchant_slug and spec.on
                        item.coverage = coverage_checker(
                            merchant_slug=spec.merchant_slug,
                            on=spec.on,
                            as_of=as_of,
                        )
                else:
                    item.coverage = {"outcome": "not_checked"}
            if spec.key in overrides.generics and item.product is None:
                product_id, revision = overrides.generics[spec.key]
                item.product = _load_product(client, product_id, revision)
                item.product_revision = revision
                item.assumptions.append(
                    f"generic:{spec.key}:{product_id}@{revision}"
                )
            if item.product is not None and item.price is not None:
                _decide_quantity(client, item, overrides, as_of=as_of)
            elif item.product is not None:
                item.quantity = QuantityResolution(
                    status="unknown", reason="no_price"
                )
        if (
            client is not None
            and not args.no_publish
            and hasattr(client, "publish_alias_observations")
        ):
            _publish_pointers(client, items)
        report = build_report(
            items,
            title=args.title,
            containers=args.containers,
            allowance_mode=args.allowance,
            as_of=as_of,
        )
    except MealError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    text = (
        json.dumps(report, indent=2)
        if args.format == "json"
        else render_report(report)
    )
    out.write(text)
    if args.save is not None:
        args.save.write_text(json.dumps(report, indent=2))
    return EXIT_UNKNOWNS if report["unknown"] else EXIT_OK


def main() -> None:
    sys.exit(run(None))


if __name__ == "__main__":
    main()
