"""Seed FoodProduct revisions and ProductAlias rows from a pilot lookup file.

Input is the merged pilot output (one record per merchant x normalized receipt
line) produced by the lookup lanes. Three modes: the offline dry run (default)
parses and counts; ``--plan --table`` reads the catalog and prints every
proposed write without writing; ``--apply --table`` writes. The prod table is
refused before any input is read.

Rules: retailer panels and UPC matches become ``matched`` aliases at full
confidence; name proxies stay ``matched`` at their capped confidence with the
proxy note kept; ambiguous products become ``pending`` with their candidates;
non-food lines become ``not_food``; products whose serving size could not be
parsed are stored without per-serving facts (identity only) rather than
guessed. Identifier lanes also write an ``ITEM#<identifier>`` alias with the
same pin. ``--manual-evidence`` mints a ``manual`` revision from the owner's
typed label and re-pins the product's aliases to it. Existing user-confirmed
aliases are never overwritten. Every dropped or unparsed field is counted and
printed with a reason.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, ValidationError, model_validator

# isort: off
# The receipt_agent CI leg lints changed files with an environment that
# classifies the local packages differently from repository-tests; fence
# the local-package block so both legs accept one ordering.
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityValidationError,
    NutritionConflictError,
)
from receipt_dynamo.entities.nutrition_support import nutrition_hash
from receipt_dynamo.entities.product_alias import ProductAlias
from receipt_dynamo.entities.receipt_line_item import (
    normalize_product_text,
    slugify_merchant,
)

from receipt_nutrition.models import (
    NUTRIENT_UNITS,
    Amount,
    FrozenModel,
    HouseholdEquivalence,
    Nonnegative,
    NutrientFact,
    Positive,
    Product,
    SourceEvidence,
    Text,
)
from receipt_nutrition.persistence import product_record
from receipt_nutrition.units import UNIT_FACTORS, normalized_amount

# isort: on

PROD_TABLE_FRAGMENTS = ("d7ff76a",)
MATCHED_TTL_DAYS = 180
PENDING_TTL_DAYS = 90
LANE_SOURCE = {
    "target": ("target", "retailer product page", False),
    "sprouts": ("instacart", "retailer storefront", False),
    "traderjoes": ("tj", "retailer product page", False),
    "costco": ("costco", "retailer product page", False),
}
FALLBACK_SOURCE = {
    "fdc": ("fdc", "public domain (USDA FoodData Central)", True),
    "off": ("off", "ODbL (Open Food Facts)", False),
}
# A number is complete only when nothing numeric or a dot sits immediately
# before it (with or without a space) and it is not the tail of a fraction:
# ".5 g", "1/2 g", "1 / 2 g" all abstain rather than reading the last digits
# as the whole amount. A slash after a word ("1 Tbsp/14g") is a separator.
_BOUNDARY = (
    r"(?<![\d.])(?<![\d.]\s)" r"(?<!\d/)(?<!\d/\s)(?<!\d\s/)(?<!\d\s/\s)"
)
_MASS = re.compile(
    _BOUNDARY + r"(\d+(?:\.\d+)?)\s*"
    r"(g|gram|grams|kg|ml|mL|milliliters?|millilitres?|l|liter|litre)\b",
    re.IGNORECASE,
)
# US customary label units; "fl oz" is tried before "oz" so volume never reads
# as mass. Metric text on the same label wins over these (see parse_serving).
_CUSTOMARY = re.compile(
    _BOUNDARY + r"(\d+(?:\.\d+)?)\s*"
    r"(fl\.?\s*oz|fluid\s+ounces?|oz|ounce|ounces)\b",
    re.IGNORECASE,
)
_PER_LB = re.compile(r"\bper\s+lb\b|/\s*lb\b", re.IGNORECASE)
_INSTACART_DEFAULT_NET = re.compile(
    r"^\s*1(?:\.0+)?\s*(?:lbs?|pounds?)\.?\s*$", re.IGNORECASE
)
# A household serving: whole, decimal, fraction, or mixed number followed by a
# cup/spoon/each word. Ranges ("1-2 cups") are rejected by _complete_number.
_HOUSEHOLD = re.compile(
    _BOUNDARY + r"(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*"
    r"(cups?|tbsp|tablespoons?|tsp|teaspoons?|each)\b",
    re.IGNORECASE,
)
_HOUSEHOLD_UNIT = {
    "cup": "cup",
    "cups": "cup",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "each": "each",
}
HOUSEHOLD_PARSER = "household-v1"
# Receipt-printed identifiers: Costco item numbers lead the line (optional tax
# flag letter), Vons prints the UPC digits, Target prints the DPCI undashed.
_LEADING_CODE = re.compile(r"^\s*(?:[A-Z]\s+)?(\d{4,13})\s+")
_TRAILING_CODE = re.compile(r"\s(\d{4,13})\s*$")
_DPCI = re.compile(r"^(\d{3})-?(\d{2})-?(\d{4})$")
MANUAL_EVIDENCE_ID = "manual"
_MULTIPACK = re.compile(r"\d+\s*[x×]\s*\d", re.IGNORECASE)
PILOT_OBSERVED_ON = date(2026, 9, 8)
_SIZE = re.compile(
    _BOUNDARY + r"(\d+(?:\.\d+)?)\s*"
    r"(fl\.?\s*oz|oz|ounce|ounces|lb|lbs|pound|pounds|kg|g|"
    r"ml|mL|l|liter|litre|gallon|gal|quart|qt|pint|pt)\b",
    re.IGNORECASE,
)
_COUNT = re.compile(_BOUNDARY + r"(\d+(?:\.\d+)?)")
# A digit joined to another digit by a slash, a dash, or "to", with any
# spacing, is a fraction or a range: never a complete number.
_FRACTION_OR_RANGE = re.compile(r"\d\s*(?:[/\-\u2013]|to)\s*\d", re.IGNORECASE)
_PARTIAL_BEFORE = re.compile(r"\d\s*(?:[/\-\u2013]|to)\s*$", re.IGNORECASE)


def _complete_number(match: re.Match[str], text: str) -> bool:
    """False when the matched number is the tail of a fraction or range."""
    return _PARTIAL_BEFORE.search(text[: match.start()]) is None


_UNIT_ALIAS = {
    "gram": "g",
    "grams": "g",
    "ounce": "oz",
    "ounces": "oz",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "liter": "l",
    "litre": "l",
    "fluidounce": "fl oz",
    "fluidounces": "fl oz",
}
_VOLUME_TO_ML = {
    "gallon": "3785.411784",
    "gal": "3785.411784",
    "quart": "946.352946",
    "qt": "946.352946",
    "pint": "473.176473",
    "pt": "473.176473",
}


def _amount(value: str, unit: str) -> Amount | None:
    unit = unit.lower().replace(".", "").replace(" ", "")
    unit = "fl oz" if unit == "floz" else _UNIT_ALIAS.get(unit, unit)
    if unit in _VOLUME_TO_ML:
        return Amount(
            value=str(Decimal(value) * Decimal(_VOLUME_TO_ML[unit])),
            unit="ml",
        )
    try:
        return normalized_amount(value, unit)
    except (ValueError, KeyError):
        return None


def parse_serving(text: Any) -> Amount | None:
    """Only an explicit mass or volume counts; household text is kept aside.

    Metric text anywhere on the label (parenthesised first) beats oz/fl oz, so
    "6.75 oz/191g" stores the printed 191 g rather than a converted value.
    """
    if text is None:
        return None
    text = str(text)
    inside = re.findall(r"\(([^)]*)\)", text)
    for pattern in (_MASS, _CUSTOMARY):
        for candidate in inside + [text]:
            match = pattern.search(candidate)
            if match and _complete_number(match, candidate):
                return _amount(match.group(1), match.group(2))
    return None


def _exact_decimal(value: Fraction) -> Decimal | None:
    """A fraction becomes a Decimal only when nothing is rounded away."""
    with localcontext() as context:
        context.prec = 50
        result = Decimal(value.numerator) / Decimal(value.denominator)
    return result if Fraction(result) == value else None


def parse_household(text: Any) -> tuple[HouseholdEquivalence | None, str]:
    """Return (equivalence, reason); reason is "ok" or why nothing parsed."""
    if text is None or not str(text).strip():
        return None, "household_absent"
    text = str(text)
    match = _HOUSEHOLD.search(text)
    if match is None:
        return None, "household_unparsed"
    if not _complete_number(match, text):
        return None, "household_unparsed"
    number = match.group(1)
    parts = number.replace("/", " ").split()
    try:
        if len(parts) == 3:
            value = Fraction(int(parts[0])) + Fraction(
                int(parts[1]), int(parts[2])
            )
        elif len(parts) == 2:
            value = Fraction(int(parts[0]), int(parts[1]))
        else:
            value = Fraction(Decimal(parts[0]))
    except (ZeroDivisionError, ValueError):
        return None, "household_unparsed"
    if value <= 0:
        return None, "household_unparsed"
    exact = _exact_decimal(value)
    if exact is None:
        return None, "household_inexact_fraction"
    return (
        HouseholdEquivalence(
            value=exact,
            unit=_HOUSEHOLD_UNIT[match.group(2).lower()],
            source_ref="src",
            parser=HOUSEHOLD_PARSER,
            raw=text[:2048],
        ),
        "ok",
    )


def sold_by_weight(record: dict[str, Any]) -> bool:
    """Per-pound price or size text, or the storefront's weighed flag."""
    if record.get("weighed") is True:
        return True
    texts = (
        record.get("example_price"),
        record.get("price_text"),
        record.get("size"),
        record.get("product_name"),
    )
    return any(
        isinstance(text, str) and _PER_LB.search(text) for text in texts
    )


def item_identifier(record: dict[str, Any]) -> str | None:
    """The receipt-printed identifier an ``ITEM#`` alias is keyed on.

    Costco: the item number as printed on the receipt line (digits verbatim),
    else the lane's ``item_number``/``source_id``. Target: the DPCI, stored
    dashed as ``NNN-NN-NNNN`` (receipts print it undashed). Vons: the UPC
    digits the receipt printed, exactly as printed (no zero stripping). Other
    lanes have no receipt identifier and get no ITEM alias.
    """
    lane = record.get("lane")
    line_text = str(record.get("line_text") or "")
    if lane == "costco":
        printed = _LEADING_CODE.match(line_text)
        raw = (
            printed.group(1)
            if printed
            else record.get("item_number") or record.get("source_id")
        )
        raw = str(raw or "").strip()
        return raw if raw.isdigit() else None
    if lane == "target":
        raw = str(record.get("dpci") or "").strip()
        match = _DPCI.match(raw)
        if match is None:
            return None
        return "-".join(match.groups())
    if lane == "fallback" and record.get("status_in_lane") == "matched_upc":
        raw = str(record.get("code") or "").strip()
        if not raw.isdigit():
            printed = _LEADING_CODE.match(line_text) or _TRAILING_CODE.search(
                line_text
            )
            raw = printed.group(1) if printed else ""
        return raw or None
    return None


def parse_size(text: Any) -> Amount | None:
    """Net contents for single packages; multipacks and pure counts abstain."""
    if text is None:
        return None
    text = str(text)
    if _MULTIPACK.search(text):
        return None
    if re.search(r"\b\d+\s*(?:ct|count|pk|pack)\b", text, re.IGNORECASE) and (
        "," in text or "x" in text.lower()
    ):
        return None
    match = _SIZE.search(text)
    if match is None or not _complete_number(match, text):
        return None
    return _amount(match.group(1), match.group(2))


def parse_servings(text: Any) -> str | None:
    """One complete number; fractions and multiple numbers abstain."""
    if text is None:
        return None
    text = str(text)
    numbers = _COUNT.findall(text)
    if len(numbers) != 1 or _FRACTION_OR_RANGE.search(text):
        return None
    value = Decimal(numbers[0])
    return str(value) if value > 0 else None


def _nutrients(
    raw: dict[str, Any], basis: str, source_ref: str
) -> list[NutrientFact]:
    facts: list[NutrientFact] = []
    for nbr, entry in (raw or {}).items():
        if nbr not in NUTRIENT_UNITS:
            continue
        value = entry.get("value") if isinstance(entry, dict) else entry
        unit = (entry.get("unit") if isinstance(entry, dict) else None) or ""
        if value is None:
            continue
        try:
            amount = Decimal(str(value))
            if not amount.is_finite():
                continue
            amount = amount.quantize(Decimal("1e-9"))
        except InvalidOperation:
            continue
        canonical = NUTRIENT_UNITS[nbr]
        unit = unit.lower()
        if unit and unit != canonical:
            # "cal" is ambiguous between small calories and kcal; drop it.
            if unit == "g" and canonical == "mg":
                amount *= 1000
            elif unit == "mg" and canonical == "g":
                amount /= 1000
            else:
                continue
        if amount < 0:
            continue
        # Binary-float noise such as 0.30000000000000004 was quantised above;
        # anything the model still rejects is skipped.
        text = format(amount, "f")
        text = text.rstrip("0").rstrip(".") if "." in text else text
        try:
            facts.append(
                NutrientFact(
                    nutrient_id=nbr,
                    amount=text or "0",
                    unit=canonical,
                    basis=basis,
                    source_ref=source_ref,
                )
            )
        except ValueError:
            continue
    return facts


def build_product(
    record: dict[str, Any], drops: Counter | None = None
) -> tuple[Product | None, str]:
    """Return (product, note). None means identity-only or nothing to store.

    ``drops`` collects one reason per field that was present but not stored.
    """
    drops = Counter() if drops is None else drops
    lane = record.get("lane")
    if record.get("class") in ("not_food", "no_source", "ambiguous"):
        return None, record["class"]
    if lane == "fallback":
        source, license_text, public = FALLBACK_SOURCE.get(
            (record.get("source") or "fdc").lower(), FALLBACK_SOURCE["fdc"]
        )
        basis = (
            "100ml"
            if (record.get("serving_unit") or "").lower() == "ml"
            else "100g"
        )
    else:
        source, license_text, public = LANE_SOURCE[lane]
        basis = "serving"
    source_id = str(record.get("source_id") or "").strip()
    if not source_id:
        return None, "no_source_id"
    product_id = f"{source}:{source_id}"
    observed = record.get("fetched_at") or ""
    try:
        observed_on = datetime.fromisoformat(
            str(observed).replace("Z", "+00:00")
        ).date()
    except ValueError:
        # A stable fallback keeps the evidence hash, and so the product
        # revision, identical across re-runs.
        observed_on = PILOT_OBSERVED_ON
    evidence = SourceEvidence(
        evidence_id="src",
        source=source,
        record_id=source_id,
        reference=str(record.get("source_url") or f"{source}:{source_id}"),
        observed_on=observed_on,
        verification="source_record",
        license=license_text,
        public_allowed=public,
    )
    serving = parse_serving(record.get("serving_size"))
    if (
        basis != "serving"
        and record.get("serving_size")
        and record.get("serving_unit")
    ):
        try:
            serving = normalized_amount(
                str(record["serving_size"]), str(record["serving_unit"])
            )
        except (ValueError, KeyError):
            serving = None
    raw_facts = record.get("nutrients") or {}
    facts = _nutrients(raw_facts, basis, "src")
    if len(facts) < len(raw_facts):
        drops["nutrient_unparsed"] += len(raw_facts) - len(facts)
    note = "ok"
    if basis == "serving" and serving is None and facts:
        facts, note = [], "serving_unparsed_identity_only"
        drops[note] += 1
    elif serving is None and record.get("serving_size"):
        drops["serving_unparsed"] += 1
    weight = sold_by_weight(record)
    size_text = record.get("size")
    net = parse_size(size_text)
    if (
        source == "instacart"
        and isinstance(size_text, str)
        and _INSTACART_DEFAULT_NET.match(size_text)
    ):
        # Instacart shows "1 lb" for by-weight items as the price basis, not
        # a package; a default is never stored, so the net stays unknown.
        net = None
        drops["instacart_default_net"] += 1
    elif net is None and size_text:
        drops["size_unparsed"] += 1
    household, household_note = parse_household(record.get("serving_size"))
    if household_note != "ok" and record.get("serving_size"):
        drops[household_note] += 1
    servings_per_container = None
    if record.get("servings_per_container"):
        if serving is None:
            drops["servings_per_container_without_serving"] += 1
        else:
            servings_per_container = parse_servings(
                record["servings_per_container"]
            )
            if servings_per_container is None:
                drops["servings_per_container_unparsed"] += 1
    product = Product(
        product_id=product_id,
        name=str(
            record.get("product_name")
            or record.get("normalized")
            or product_id
        )[:2048],
        brand=str(record.get("brand") or ""),
        identity_kind=(
            "generic"
            if record.get("status_in_lane") == "matched_generic"
            else "exact"
        ),
        evidence=(evidence,),
        nutrients=tuple(facts),
        net_amount=net,
        serving=serving,
        serving_household=(
            str(record["serving_size"])[:2048]
            if record.get("serving_size")
            else None
        ),
        servings_per_container=servings_per_container,
        package_source_ref="src" if (net or serving) else None,
        sold_by="weight" if weight else None,
        sold_by_source_ref="src" if weight else None,
        household=household,
    )
    return product, note


class ManualServing(FrozenModel):
    amount: Positive
    unit: Literal[tuple(UNIT_FACTORS)]  # type: ignore[valid-type]


class ManualNutrient(FrozenModel):
    amount: Nonnegative
    unit: Literal["g", "mg", "ug", "kcal"]


class ManualHousehold(FrozenModel):
    value: Positive
    unit: Literal["tsp", "tbsp", "cup", "each"]


class ManualEvidence(FrozenModel):
    """One owner-typed label. Numbers are strings or integers, never floats."""

    product_id: Text
    observed_on: date
    reference: Text
    serving: ManualServing
    servings_per_container: Positive | None = None
    household: ManualHousehold | None = None
    nutrients: dict[
        Literal[
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
        ],
        ManualNutrient,
    ] = Field(min_length=1)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_units(self) -> Self:
        """A wrong unit fails at load time, before any write happens."""
        for nutrient_id, fact in self.nutrients.items():
            if fact.unit != NUTRIENT_UNITS[nutrient_id]:
                raise ValueError(
                    f"nutrient {nutrient_id} must be in "
                    f"{NUTRIENT_UNITS[nutrient_id]}, not {fact.unit}"
                )
        return self


def load_manual_evidence(payload: Any) -> list[ManualEvidence]:
    if not isinstance(payload, list):
        raise ValueError("manual evidence must be a JSON list")
    entries = [ManualEvidence.model_validate(entry) for entry in payload]
    ids = [entry.product_id for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("manual evidence lists a product twice")
    return entries


def manual_product(
    base: Product, entry: ManualEvidence, drops: Counter | None = None
) -> Product:
    """Mint the owner's label as a new revision of an already seeded product.

    Identity, net contents, and sold-by keep their storefront evidence;
    serving and facts come from the typed label under a ``manual`` evidence
    record, so the storefront panel is superseded but never rewritten. The
    storefront household equivalence describes the storefront serving, so it
    is cleared (and counted) when the label's serving differs, unless the
    label supplies its own.
    """
    drops = Counter() if drops is None else drops
    if entry.product_id != base.product_id:
        raise ValueError("manual evidence names a different product")
    evidence = SourceEvidence(
        evidence_id=MANUAL_EVIDENCE_ID,
        source="manual",
        record_id=entry.product_id,
        reference=entry.reference,
        observed_on=entry.observed_on,
        verification="user",
        license="owner transcription of the printed label; private",
        public_allowed=False,
        payload_sha256=nutrition_hash(entry.model_dump(mode="python")),
    )
    serving = normalized_amount(entry.serving.amount, entry.serving.unit)
    facts = tuple(
        NutrientFact(
            nutrient_id=nutrient_id,
            amount=Decimal(fact.amount),
            unit=fact.unit,
            basis="serving",
            source_ref=MANUAL_EVIDENCE_ID,
        )
        for nutrient_id, fact in entry.nutrients.items()
    )
    kept = base.model_dump(mode="python")
    for field in (
        "evidence",
        "nutrients",
        "serving",
        "servings_per_container",
        "package_source_ref",
        "household",
    ):
        kept.pop(field)
    household = base.household
    if entry.household is not None:
        household = HouseholdEquivalence(
            value=entry.household.value,
            unit=entry.household.unit,
            source_ref=MANUAL_EVIDENCE_ID,
            parser=HOUSEHOLD_PARSER,
            raw=f"{entry.household.value} {entry.household.unit}",
        )
    elif household is not None and serving != base.serving:
        household = None
        drops["manual_household_cleared"] += 1
    return Product(
        **kept,
        household=household,
        evidence=tuple(
            source
            for source in base.evidence
            if source.evidence_id != MANUAL_EVIDENCE_ID
        )
        + (evidence,),
        nutrients=facts,
        serving=serving,
        servings_per_container=entry.servings_per_container,
        package_source_ref=MANUAL_EVIDENCE_ID,
    )


def _stringify_numbers(value: Any) -> Any:
    """Decision payloads are stored canonically; floats become strings."""
    if (
        isinstance(value, bool)
        or value is None
        or isinstance(value, (str, int))
    ):
        return value
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _stringify_numbers(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify_numbers(v) for v in value]
    return str(value)


def alias_for(
    record: dict[str, Any],
    product_id: str | None,
    product_revision: str | None,
    revision: int,
    now: datetime,
    *,
    kind: str = "TEXT",
    identifier: str | None = None,
) -> ProductAlias:
    """The TEXT alias for a record, or its ITEM twin when ``identifier`` is set."""
    cls = record["class"]
    status = {
        "panel": "matched",
        "identity": "matched",
        "ambiguous": "pending",
        "no_source": "no_match",
        "not_food": "not_food",
    }[cls]
    if status == "matched" and product_id is None:
        status = "pending"
    lane = record.get("lane")
    method = (
        "identifier"
        if lane in ("target", "costco")
        or (
            lane == "fallback"
            and record.get("status_in_lane") == "matched_upc"
        )
        else "lexical"
    )
    ttl_days = MATCHED_TTL_DAYS if status == "matched" else PENDING_TTL_DAYS
    text = normalize_product_text(record["normalized"])
    candidates = record.get("candidates") or []
    if isinstance(candidates, list):
        candidates = candidates[:3]
    decision = {
        "lane": lane,
        "confidence": record.get("confidence"),
        "status_in_lane": record.get("status_in_lane"),
        "notes": record.get("notes"),
        "candidates": candidates,
        "seed": "pilot-2026-09-09",
    }
    if kind == "ITEM":
        if not identifier:
            raise ValueError("ITEM aliases need an identifier")
        method = "identifier"
    scope: dict[str, Any] = {
        "merchant": record["merchant"],
        "text": text,
        "size": record.get("size"),
    }
    if kind == "ITEM":
        scope["identifier"] = identifier
    return ProductAlias(
        merchant_slug=slugify_merchant(record["merchant"]),
        kind=kind,
        text=identifier if kind == "ITEM" else text,
        revision=revision,
        status=status,
        method=method,
        changed_at=now.isoformat(timespec="milliseconds"),
        applicability_json=json.dumps(_stringify_numbers(scope)),
        decision_json=json.dumps(_stringify_numbers(decision), default=str),
        product_id=product_id if status == "matched" else None,
        product_revision=product_revision if status == "matched" else None,
        confirmed_by_user=False,
        expires_at=int((now + timedelta(days=ttl_days)).timestamp()),
    )


ITEM_LANES = ("costco", "target", "fallback")


def _alias_matches(existing: ProductAlias, alias: ProductAlias) -> bool:
    return (
        existing.status == alias.status
        and existing.product_id == alias.product_id
        and existing.product_revision == alias.product_revision
        and existing.decision_json == alias.decision_json
    )


def seed(
    records: list[dict[str, Any]],
    client: DynamoClient | None,
    table: str | None,
    *,
    apply: bool = False,
    manual: list[ManualEvidence] | None = None,
) -> Counter:
    """Plan (and with ``apply`` perform) the writes one input implies.

    Without a client nothing is read or written (offline parse counts). With a
    client the catalog is read and every proposed write is counted under
    ``products_new`` / ``alias_new`` / ``alias_repin``; kept rows count under
    ``products_existing`` / ``alias_unchanged`` / ``alias_kept_user``. Only
    ``apply`` writes. Drop reasons are returned under ``drop:<reason>``.
    """
    if apply and client is None:
        raise ValueError("apply requires a client")
    counts: Counter = Counter()
    drops: Counter = Counter()
    now = datetime.now(timezone.utc)
    seen_keys: set[tuple[str, str, str]] = set()
    proposed_products: set[tuple[str, str]] = set()
    manual_by_id = {entry.product_id: entry for entry in manual or []}
    manual_used: set[str] = set()

    def put_product(product: Product) -> tuple[str, str]:
        stored = product_record(product)
        key = (stored.product_id, stored.revision)
        if client is not None:
            assert table is not None
            # A revision already proposed by an earlier row of this run is
            # one write, so the plan and the apply agree.
            if key in proposed_products or client.get_food_product(*key):
                counts["products_existing"] += 1
            else:
                counts["products_new"] += 1
                if apply:
                    client.add_food_product(stored, expected_table_name=table)
                    counts["products_written"] += 1
        proposed_products.add(key)
        return key

    def put_alias(
        record: dict[str, Any],
        kind: str,
        key_text: str,
        product_id: str | None,
        product_revision: str | None,
        identifier: str | None = None,
    ) -> None:
        merchant = slugify_merchant(record["merchant"])
        existing = (
            client.get_product_alias(merchant, kind, key_text)
            if client
            else None
        )
        if existing is not None and existing.confirmed_by_user:
            counts["alias_kept_user"] += 1
            counts[f"alias_kept_user:{kind}"] += 1
            return
        alias = alias_for(
            record,
            product_id,
            product_revision,
            (existing.revision if existing else 0) + 1,
            now,
            kind=kind,
            identifier=identifier,
        )
        if existing is not None and _alias_matches(existing, alias):
            counts["alias_unchanged"] += 1
            counts[f"alias_unchanged:{kind}"] += 1
            return
        category = "alias_new" if existing is None else "alias_repin"
        counts[category] += 1
        counts[f"{category}:{kind}"] += 1
        counts[f"alias:{alias.status}"] += 1
        if apply:
            assert client is not None and table is not None
            client.save_product_alias(
                alias,
                expected_revision=existing.revision if existing else 0,
                expected_table_name=table,
            )
            counts["alias_written"] += 1

    for record in records:
        if not record.get("normalized"):
            counts["skipped_blank"] += 1
            continue
        merchant = slugify_merchant(record["merchant"])
        text = normalize_product_text(record["normalized"])
        # Two input rows collapsing to one alias key would bump the alias
        # revision on every run; the first row owns the TEXT alias and the
        # rest are reported, but their products and ITEM aliases still land.
        text_owner = (merchant, "TEXT", text) not in seen_keys
        seen_keys.add((merchant, "TEXT", text))
        if not text_owner:
            counts["alias_duplicate_key_skipped"] += 1
        try:
            product, note = build_product(record, drops)
        except (ValueError, EntityValidationError) as error:
            counts["product_invalid"] += 1
            counts[f"invalid:{type(error).__name__}"] += 1
            continue
        counts[f"product:{note}"] += 1
        product_id = product_revision = None
        try:
            if product is not None:
                product_id, product_revision = put_product(product)
                entry = manual_by_id.get(product.product_id)
                if entry is not None:
                    # The storefront revision stays (append-only); the alias
                    # pins the owner's label.
                    minted = manual_product(product, entry, drops)
                    product_id, product_revision = put_product(minted)
                    if product.product_id not in manual_used:
                        counts["manual_revision"] += 1
                    manual_used.add(product.product_id)
            if text_owner:
                put_alias(record, "TEXT", text, product_id, product_revision)
            identifier = (
                item_identifier(record)
                if record.get("lane") in ITEM_LANES
                else None
            )
            if identifier is None:
                if record.get("lane") in ("costco", "target"):
                    drops["item_alias_no_identifier"] += 1
            elif (merchant, "ITEM", identifier) in seen_keys:
                counts["item_alias_duplicate_key_skipped"] += 1
            else:
                seen_keys.add((merchant, "ITEM", identifier))
                put_alias(
                    record,
                    "ITEM",
                    identifier,
                    product_id,
                    product_revision,
                    identifier=identifier,
                )
        except NutritionConflictError:
            counts["alias_conflict"] += 1
        except (ValueError, EntityValidationError) as error:
            counts["alias_invalid"] += 1
            counts[f"invalid:{type(error).__name__}"] += 1
    for product_id in manual_by_id:
        if product_id not in manual_used:
            drops["manual_evidence_unknown_product"] += 1
    counts["writes_proposed"] = (
        counts["products_new"] + counts["alias_new"] + counts["alias_repin"]
    )
    for reason, value in drops.items():
        counts[f"drop:{reason}"] = value
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "merged", type=Path, help="merged_products.json from the pilot"
    )
    parser.add_argument(
        "--table", help="dev table name; required with --plan or --apply"
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="read the catalog and print proposed writes without writing",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write (default: dry run)"
    )
    parser.add_argument(
        "--manual-evidence",
        type=Path,
        help="JSON list of owner-typed labels; each mints a manual revision",
    )
    args = parser.parse_args(argv)
    # Refuse prod before any input file is opened.
    if args.table and any(
        fragment in args.table for fragment in PROD_TABLE_FRAGMENTS
    ):
        parser.error("refusing to seed the prod table")
    if args.plan and args.apply:
        parser.error("--plan and --apply are exclusive")
    if (args.plan or args.apply) and not args.table:
        parser.error("--plan and --apply require --table")
    records = json.loads(args.merged.read_text())
    manual: list[ManualEvidence] = []
    if args.manual_evidence is not None:
        try:
            manual = load_manual_evidence(
                json.loads(args.manual_evidence.read_text())
            )
        except (ValueError, ValidationError) as error:
            parser.error(f"invalid manual evidence: {error}")
    client = DynamoClient(args.table) if args.plan or args.apply else None
    counts = seed(records, client, args.table, apply=args.apply, manual=manual)
    mode = "APPLIED" if args.apply else "PLAN" if args.plan else "DRY RUN"
    print(f"{mode}: {len(records)} records, {len(manual)} manual labels")
    for key, value in sorted(counts.items()):
        if not key.startswith("drop:"):
            print(f"  {key:40s} {value}")
    print("Dropped or unparsed fields (reason: count):")
    dropped = {k[5:]: v for k, v in counts.items() if k.startswith("drop:")}
    if not dropped:
        print("  none")
    for key, value in sorted(dropped.items()):
        print(f"  {key:40s} {value}")
    if client is not None:
        verb = "written" if args.apply else "proposed"
        print(f"{counts['writes_proposed']} writes {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
