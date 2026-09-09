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
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Literal

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.product_alias_observation import (
    ProductAliasObservation,
    product_alias_id,
)

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
    resolve_merchant_slug,
)
from receipt_nutrition.units import (
    as_decimal,
    exact_amount_in,
    verified_source,
)

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
# An inferred weight is an assumption. It is stored with this many decimal
# places (a nanogram), truncated, so the value is representable in the
# 40-significant-digit model and any cost rounding lands on the same cent
# as the exact ratio would.
INFERRED_GRAMS = Decimal("0.000000001")

CoverageChecker = Callable[..., dict[str, Any]]


class MealError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


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
    allowance: Decimal | None = None  # None = auto (explicit owner choice)
    allowance_text: str = "0"


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
    error: MealError | None = None


# ---------------------------------------------------------------- parsing


def _parse_portion(text: str) -> PortionSpec:
    if text == "all":
        return PortionSpec(None, None)
    for unit in sorted(PORTION_UNITS, key=len, reverse=True):
        if text.endswith(unit):
            number = text[: -len(unit)].strip()
            if not number:
                break
            try:
                return PortionSpec(decimal_input(number), unit)
            except (ValueError, ArithmeticError) as error:
                raise MealError(
                    f"bad portion {text!r}: {error}", EXIT_POINTER
                ) from error
    raise MealError(
        f"bad portion {text!r}; use <n><unit> or all", EXIT_POINTER
    )


def _int(text: str, what: str) -> int:
    if not text.isdigit():
        raise MealError(f"{what} must be a non-negative integer", EXIT_POINTER)
    return int(text)


def parse_item(text: str) -> ItemSpec:
    if "=" not in text:
        raise MealError(
            f"--item needs KEY=SRC:PORTION, got {text!r}", EXIT_POINTER
        )
    key, rest = text.split("=", 1)
    if not key:
        raise MealError("--item key must not be empty", EXIT_POINTER)
    kind, _, remainder = rest.partition(":")
    middle, _, portion_text = remainder.rpartition(":")
    if not portion_text:
        raise MealError(f"bad --item source {rest!r}", EXIT_POINTER)
    portion = _parse_portion(portion_text)
    if kind == "line":
        parts = middle.split(":")
        if len(parts) != 3:
            raise MealError(
                "line source needs <image_id>:<receipt_id>:<item_index>",
                EXIT_POINTER,
            )
        return ItemSpec(
            key,
            "line",
            portion,
            image_id=parts[0],
            receipt_id=_int(parts[1], "receipt_id"),
            item_index=_int(parts[2], "item_index"),
        )
    if kind == "latest":
        slug, _, alias = middle.partition(":")
        alias_kind, _, alias_text = alias.partition("#")
        if not slug or alias_kind not in ("ITEM", "TEXT") or not alias_text:
            raise MealError(
                "latest source needs <slug>:ITEM#<n> or <slug>:TEXT#<text>",
                EXIT_POINTER,
            )
        return ItemSpec(
            key,
            "latest",
            portion,
            merchant_slug=slug,
            alias_kind=alias_kind,
            alias_text=alias_text,
        )
    if kind == "product":
        product_id, _, revision = middle.rpartition("@")
        if not product_id or not revision:
            raise MealError(
                "product source needs <product_id>@<revision>", EXIT_POINTER
            )
        return ItemSpec(
            key,
            "product",
            portion,
            product_id=product_id,
            product_revision=revision,
        )
    if kind == "none":
        slug, _, on_text = middle.partition(":")
        try:
            on = date.fromisoformat(on_text)
        except ValueError as error:
            raise MealError(
                "none source needs <merchant_slug>:<yyyy-mm-dd>", EXIT_POINTER
            ) from error
        if not slug:
            raise MealError("none source needs a merchant slug", EXIT_POINTER)
        return ItemSpec(key, "none", portion, merchant_slug=slug, on=on)
    raise MealError(f"bad --item source {rest!r}", EXIT_POINTER)


def _key_value(flag: str, text: str) -> tuple[str, str]:
    if "=" not in text:
        raise MealError(f"{flag} needs KEY=VALUE, got {text!r}", EXIT_POINTER)
    key, value = text.split("=", 1)
    return key, value


def _decimal(flag: str, text: str) -> Decimal:
    try:
        value = decimal_input(text)
    except (ValueError, ArithmeticError) as error:
        raise MealError(f"{flag}: {error}", EXIT_POINTER) from error
    if value <= 0:
        raise MealError(f"{flag} must be positive", EXIT_POINTER)
    return value


def parse_overrides(args: argparse.Namespace) -> Overrides:
    overrides = Overrides()
    for text in args.rate:
        key, value = _key_value("--rate", text)
        price, _, unit = value.rpartition(":")
        if unit not in RATE_UNITS or not price:
            raise MealError(
                "--rate needs KEY=<price>:<lb|kg|oz>", EXIT_POINTER
            )
        overrides.rates.setdefault(key, []).append(
            (_decimal("--rate", price), unit)
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
        overrides.quantities[key] = (_decimal("--quantity", number), unit)
    for text in args.price:
        key, value = _key_value("--price", text)
        overrides.prices[key] = _decimal("--price", value)
    for text in args.generic:
        key, value = _key_value("--generic", text)
        product_id, _, revision = value.rpartition("@")
        if not product_id or not revision:
            raise MealError(
                "--generic needs KEY=<product_id>@<revision>", EXIT_POINTER
            )
        overrides.generics[key] = (product_id, revision)
    overrides.allowance_text = args.allowance
    if args.allowance == "auto":
        overrides.allowance = None
    else:
        overrides.allowance = Decimal(0)
        if args.allowance != "0":
            overrides.allowance = _decimal("--allowance", args.allowance)
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


def _dal_number(value: Any) -> str | None:
    """DAL rows carry floats; the quantity adapter refuses floats by design."""
    if value is None or value == "":
        return None
    return str(value)


def _resolve_pointer(
    client: Any,
    item: ResolvedItem,
    *,
    as_of: date,
    fleet_map: dict[str, str] | None,
) -> None:
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
    item.raw_quantity = _dal_number(getattr(line, "quantity", None))
    item.raw_unit_price = _dal_number(getattr(line, "unit_price", None))
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
        item.error = MealError(
            f"{spec.key}: alias pending for {item.merchant_slug} "
            f"{[(r.kind, r.text) for r in resolution.alias_refs]} "
            f"({resolution.reason})",
            EXIT_PENDING,
        )
    else:
        item.error = MealError(
            f"{spec.key}: no usable alias for {item.merchant_slug!r} text "
            f"{line.name!r} ({resolution.status}: {resolution.reason})",
            EXIT_POINTER,
        )


def _merchant_lines(client: Any, merchant_slug: str) -> list[Any]:
    lines: list[Any] = []
    cursor = None
    while True:
        result = client.list_receipt_line_items_by_merchant(
            merchant_slug, last_evaluated_key=cursor
        )
        if isinstance(result, tuple):
            page, cursor = result[0], result[1]
        else:
            page, cursor = result, None
        lines.extend(page)
        if not cursor:
            return lines


def _resolve_latest(
    client: Any,
    item: ResolvedItem,
    *,
    as_of: date,
    fleet_map: dict[str, str] | None,
) -> None:
    spec = item.spec
    assert spec.merchant_slug and spec.alias_kind and spec.alias_text
    candidates = []
    for line in _merchant_lines(client, spec.merchant_slug):
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
            grams = as_decimal(_rate_to_grams(item.price, rate)).quantize(
                INFERRED_GRAMS, rounding=ROUND_DOWN
            )
            item.quantity = QuantityResolution(
                status="known",
                reason=f"inferred_from_rate:{rate.source}",
                quantity=QuantityEvidence(
                    value=grams,
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
    return Portion(value=spec.value, unit=unit, reference="--item portion")


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


def _needed_allowance(product: Product) -> Fraction | None:
    """The smallest rounding allowance that reconciles net and servings."""
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


def _allowance(item: ResolvedItem, overrides: Overrides) -> Fraction:
    product = item.product
    assert product is not None
    if overrides.allowance is not None:
        allowance = Fraction(overrides.allowance)
        if allowance and _needed_allowance(product) is not None:
            item.assumptions.append(
                f"allowance:{item.spec.key}:{overrides.allowance}:owner"
            )
        return allowance
    needed = _needed_allowance(product)
    if needed is None or product.serving is None:
        return Fraction(0)
    cap = min(Fraction(1, 2), Fraction(product.serving.value) / 20)
    if product.serving.unit == "each":
        cap = Fraction()
    if needed <= cap:
        item.assumptions.append(
            f"allowance:{item.spec.key}:{as_decimal(needed)}:auto"
        )
        return needed
    return Fraction(0)


def _build_meal_item(
    item: ResolvedItem, overrides: Overrides
) -> MealItem | None:
    product = item.product
    if product is None or item.quantity is None:
        return None
    portion = _portion(item)
    if portion is None:
        item.unknown_reason = item.unknown_reason or "portion_needs_quantity"
        return None
    if item.price is None:
        # Nutrients depend only on the quantity; the cost stays unknown by
        # marking the purchase excluded from costing.
        purchase = Purchase(
            extended_price=Decimal(0),
            quantity=item.quantity,
            is_adjustment=True,
        )
        item.unknown_reason = item.unknown_reason or "price_unknown"
    else:
        purchase = Purchase(extended_price=item.price, quantity=item.quantity)
    allowance = _allowance(item, overrides)
    household = _household(item)
    try:
        return MealItem(
            key=item.spec.key,
            product=product,
            purchase=purchase,
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
    """One pointer per (alias key, receipt line) for every key that was read.

    Every key the resolver looked up gets an expectation, including keys with
    no row (revision None), so a decision written under any of them between
    the read and this publish fails the transaction.
    """
    observations: list[ProductAliasObservation] = []
    expectations: dict[tuple[str, str, str], int | None] = {}
    seen: set[tuple[str, str, str, str, int, int]] = set()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in items:
        resolution = item.resolution
        spec = item.spec
        if resolution is None or item.merchant_slug is None:
            continue
        assert spec.image_id is not None and spec.receipt_id is not None
        assert spec.item_index is not None
        read = {
            (ref.kind, ref.text): ref.revision for ref in resolution.alias_refs
        }
        for kind, text in resolution.keys.lookups:
            alias_key = (item.merchant_slug, kind, text)
            revision = read.get((kind, text))
            if (
                alias_key in expectations
                and expectations[alias_key] != revision
            ):
                raise MealError(
                    f"{spec.key}: alias {kind}#{text} read at two revisions",
                    EXIT_INTERNAL,
                )
            expectations[alias_key] = revision
            pointer = (
                *alias_key,
                spec.image_id,
                spec.receipt_id,
                spec.item_index,
            )
            if pointer in seen:
                continue
            seen.add(pointer)
            matched = resolution.status == "matched" and revision is not None
            if matched:
                status = "matched"
            elif revision is None:
                status = "no_match"
            elif resolution.status in ("pending", "rejected", "not_food"):
                status = resolution.status
            else:
                status = "no_match"
            observations.append(
                ProductAliasObservation(
                    alias_id=product_alias_id(*alias_key),
                    merchant_slug=item.merchant_slug,
                    kind=kind,
                    text=text,
                    status=status,
                    alias_revision=revision or 0,
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
    if observations:
        client.publish_alias_observations(
            observations,
            alias_expectations=[
                (*alias_key, revision)
                for alias_key, revision in expectations.items()
            ],
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


def _share(value: str, containers: int) -> str:
    """A portion divided among containers, shown exactly."""
    share = Fraction(decimal_input(value)) / containers
    if share.denominator == 1:
        return str(share.numerator)
    whole, rest = divmod(share.numerator, share.denominator)
    fraction = f"{rest}/{share.denominator}"
    return f"{whole} {fraction}" if whole else fraction


def build_report(
    items: list[ResolvedItem],
    *,
    title: str,
    containers: int,
    overrides: Overrides,
    as_of: date,
) -> dict[str, Any]:
    meal_items = []
    for item in items:
        if item.product is not None:
            item.meal_item = _build_meal_item(item, overrides)
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
        portion = row["portion"] if row else None
        per_item.append(
            {
                "key": item.spec.key,
                "source": item.spec.source,
                "pointer": (
                    f"{item.spec.image_id}:{item.spec.receipt_id}:"
                    f"{item.spec.item_index}"
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
                "portion_per_container": (
                    f"{_share(portion['value'], containers)} {portion['unit']}"
                    if portion
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
        "allowance": overrides.allowance_text,
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
        "calculator_version": (
            result["calculator_version"] if result else None
        ),
    }


def _fmt(value: str | None, digits: int = 1) -> str:
    return f"{Decimal(value):.{digits}f}" if value is not None else "unknown"


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['title']}",
        "",
        f"Per container, {report['containers']} containers, "
        f"as of {report['as_of']}.",
        "",
        "| Item | Portion per container | Cost | kcal | Protein g | Fat g "
        "| Carb g | Sugar g | Sodium mg |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    n = report["item_count"]
    for item in report["items"]:
        row = item["row"]
        if row is None:
            portion = "unknown"
            cells = ["unknown"] * 7
        else:
            portion = item["portion_per_container"]
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
            f"${report['available_cost_subtotal']} "
            f"({report['cost_items']}/{n})"
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
                f"{_fmt(total['available_subtotal'])} "
                f"({total['items_with_value']}/{n})"
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
                    f"| {item['key']} | {choice['price_per_unit']}/"
                    f"{choice['unit']} | {choice['source']} | "
                    f"{choice['effective_on']} |"
                )
    lines += ["", "Cost basis:"]
    for item in report["items"]:
        row = item["row"]
        quantity = item["quantity"]
        if quantity and quantity.get("quantity"):
            shown_quantity = (
                f"{quantity['quantity']['value']} "
                f"{quantity['quantity']['unit']} ({quantity['reason']})"
            )
        elif quantity:
            shown_quantity = quantity["status"] + ":" + quantity["reason"]
        else:
            shown_quantity = "unknown"
        lines.append(
            f"- {item['key']}: pointer {item['pointer'] or item['source']}, "
            f"price {item['price'] or 'unknown'}, quantity {shown_quantity}, "
            f"cost basis {row['cost_basis'] if row else 'unknown'}, "
            f"product {item['product_id'] or 'unknown'}@"
            f"{item['product_revision'] or '-'}"
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
        f"Report `{report['report_version']}`; calculator "
        f"`{report['calculator_version']}`; input hash "
        f"`{report['input_hash']}`.",
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
        "--rate",
        action="append",
        default=[],
        metavar="KEY=<price>:<lb|kg|oz>",
        help="owner-typed rate; source owner; no band",
    )
    parser.add_argument(
        "--assume-package",
        action="append",
        default=[],
        metavar="KEY=N",
        help="N packages bought (explicit; marked estimated)",
    )
    parser.add_argument(
        "--quantity",
        action="append",
        default=[],
        metavar="KEY=<n>:<g|ml|each>",
        help="dimensioned purchase quantity (explicit; marked estimated)",
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
    parser.add_argument(
        "--allowance",
        default="0",
        metavar="0|auto|<decimal>",
        help=(
            "servings-conflict tolerance per label serving; default 0; "
            "auto derives the smallest reconciling value and names it"
        ),
    )
    parser.add_argument(
        "--coverage",
        choices=("on", "off"),
        default="on",
        help="off records coverage:<key>:not_checked",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="bounds coverage windows and observation eligibility",
    )
    parser.add_argument("--table", default=None)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--save", type=Path, default=None)
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="skip alias-outcome pointer writes",
    )
    return parser


def _first_error(items: list[ResolvedItem]) -> MealError | None:
    errors = [item.error for item in items if item.error is not None]
    if not errors:
        return None
    # A pending alias (4) is actionable; a missing one (2) is reported first
    # only when nothing is pending.
    for code in (EXIT_PENDING, EXIT_POINTER):
        for error in errors:
            if error.exit_code == code:
                return error
    return errors[0]


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
        needs_client = bool(overrides.generics) or any(
            spec.source != "none" for spec in specs
        )
        if client is None and needs_client:
            if not args.table:
                raise MealError(
                    "--table is required for catalog lookups", EXIT_POINTER
                )
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
            else:
                item.unknown_reason = "no_receipt"
                if args.coverage == "on":
                    if coverage_checker is None:
                        item.coverage = {
                            "outcome": "lookup_unavailable",
                            "reason": "no_backend",
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
            if item.price is None and spec.key in overrides.prices:
                item.price = overrides.prices[spec.key]
                item.assumptions.append(f"price:{spec.key}:{item.price}")
            if item.product is not None:
                _decide_quantity(client, item, overrides, as_of=as_of)
        if (
            client is not None
            and not args.no_publish
            and hasattr(client, "publish_alias_observations")
        ):
            _publish_pointers(client, items)
        failure = _first_error(items)
        if failure is not None:
            raise failure
        report = build_report(
            items,
            title=args.title,
            containers=args.containers,
            overrides=overrides,
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
