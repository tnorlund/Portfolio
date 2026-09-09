"""Seed FoodProduct revisions and ProductAlias rows from a pilot lookup file.

Input is the merged pilot output (one record per merchant x normalized receipt
line) produced by the lookup lanes. Dry-run by default: pass ``--apply`` and
the dev table name to write. The prod table is refused unconditionally.

Rules: retailer panels and UPC matches become ``matched`` aliases at full
confidence; name proxies stay ``matched`` at their capped confidence with the
proxy note kept; ambiguous products become ``pending`` with their candidates;
non-food lines become ``not_food``; products whose serving size could not be
parsed are stored without per-serving facts (identity only) rather than
guessed. Existing user-confirmed aliases are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityValidationError,
    NutritionConflictError,
)
from receipt_dynamo.entities.product_alias import ProductAlias
from receipt_dynamo.entities.receipt_line_item import (
    normalize_product_text,
    slugify_merchant,
)

from receipt_nutrition.models import (
    NUTRIENT_UNITS,
    Amount,
    NutrientFact,
    Product,
    SourceEvidence,
)
from receipt_nutrition.persistence import product_record
from receipt_nutrition.units import normalized_amount

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
_MASS = re.compile(
    r"(?<![\d./])(\d+(?:\.\d+)?)\s*"
    r"(g|gram|grams|kg|ml|mL|milliliters?|l|liter|litre)\b",
    re.IGNORECASE,
)
_MULTIPACK = re.compile(r"\d+\s*[x×]\s*\d", re.IGNORECASE)
PILOT_OBSERVED_ON = date(2026, 9, 8)
_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|oz|ounce|ounces|lb|lbs|pound|pounds|kg|g|"
    r"ml|mL|l|liter|litre|gallon|gal|quart|qt|pint|pt)\b",
    re.IGNORECASE,
)
_COUNT = re.compile(r"(\d+(?:\.\d+)?)")
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
    "liter": "l",
    "litre": "l",
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
    """Only an explicit mass or volume counts; household text is kept aside."""
    if text is None:
        return None
    text = str(text)
    inside = re.findall(r"\(([^)]*)\)", text)
    for candidate in inside + [text]:
        match = _MASS.search(candidate)
        if match:
            return _amount(match.group(1), match.group(2))
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
    return _amount(match.group(1), match.group(2)) if match else None


def parse_servings(text: Any) -> str | None:
    if text is None:
        return None
    match = _COUNT.search(str(text))
    if not match:
        return None
    value = Decimal(match.group(1))
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
        except InvalidOperation:
            continue
        if not amount.is_finite():
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
        text = format(amount, "f")
        text = text.rstrip("0").rstrip(".") if "." in text else text
        facts.append(
            NutrientFact(
                nutrient_id=nbr,
                amount=text or "0",
                unit=canonical,
                basis=basis,
                source_ref=source_ref,
            )
        )
    return facts


def build_product(record: dict[str, Any]) -> tuple[Product | None, str]:
    """Return (product, note). None means identity-only or nothing to store."""
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
    facts = _nutrients(record.get("nutrients") or {}, basis, "src")
    note = "ok"
    if basis == "serving" and serving is None and facts:
        facts, note = [], "serving_unparsed_identity_only"
    net = parse_size(record.get("size"))
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
        servings_per_container=(
            parse_servings(record.get("servings_per_container"))
            if serving is not None
            else None
        ),
        package_source_ref="src" if (net or serving) else None,
    )
    return product, note


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
) -> ProductAlias:
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
    return ProductAlias(
        merchant_slug=slugify_merchant(record["merchant"]),
        kind="TEXT",
        text=text,
        revision=revision,
        status=status,
        method=method,
        changed_at=now.isoformat(timespec="milliseconds"),
        applicability_json=json.dumps(
            _stringify_numbers(
                {
                    "merchant": record["merchant"],
                    "text": text,
                    "size": record.get("size"),
                }
            )
        ),
        decision_json=json.dumps(_stringify_numbers(decision), default=str),
        product_id=product_id if status == "matched" else None,
        product_revision=product_revision if status == "matched" else None,
        confirmed_by_user=False,
        expires_at=int((now + timedelta(days=ttl_days)).timestamp()),
    )


def seed(
    records: list[dict[str, Any]],
    client: DynamoClient | None,
    table: str | None,
) -> Counter:
    counts: Counter = Counter()
    now = datetime.now(timezone.utc)
    for record in records:
        if not record.get("normalized"):
            counts["skipped_blank"] += 1
            continue
        try:
            product, note = build_product(record)
        except (ValueError, EntityValidationError) as error:
            counts["product_invalid"] += 1
            counts[f"invalid:{type(error).__name__}"] += 1
            continue
        counts[f"product:{note}"] += 1
        product_id = product_revision = None
        if product is not None:
            stored = product_record(product)
            product_id, product_revision = stored.product_id, stored.revision
            if client is not None:
                assert table is not None
                if (
                    client.get_food_product(product_id, product_revision)
                    is None
                ):
                    client.add_food_product(stored, expected_table_name=table)
                    counts["product_written"] += 1
                else:
                    counts["product_exists"] += 1
        try:
            merchant = slugify_merchant(record["merchant"])
            text = normalize_product_text(record["normalized"])
            existing = (
                client.get_product_alias(merchant, "TEXT", text)
                if client
                else None
            )
            if existing is not None and existing.confirmed_by_user:
                counts["alias_kept_user"] += 1
                continue
            alias = alias_for(
                record,
                product_id,
                product_revision,
                (existing.revision if existing else 0) + 1,
                now,
            )
            if (
                existing is not None
                and existing.status == alias.status
                and existing.product_id == alias.product_id
                and existing.product_revision == alias.product_revision
                and existing.decision_json == alias.decision_json
            ):
                counts["alias_unchanged"] += 1
                continue
            counts[f"alias:{alias.status}"] += 1
            if client is not None:
                assert table is not None
                client.save_product_alias(
                    alias,
                    expected_revision=existing.revision if existing else 0,
                    expected_table_name=table,
                )
                counts["alias_written"] += 1
        except NutritionConflictError:
            counts["alias_conflict"] += 1
        except (ValueError, EntityValidationError) as error:
            counts["alias_invalid"] += 1
            counts[f"invalid:{type(error).__name__}"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "merged", type=Path, help="merged_products.json from the pilot"
    )
    parser.add_argument(
        "--table", help="dev table name; required with --apply"
    )
    parser.add_argument(
        "--apply", action="store_true", help="write (default: dry run)"
    )
    args = parser.parse_args(argv)
    client = None
    if args.table and any(
        fragment in args.table for fragment in PROD_TABLE_FRAGMENTS
    ):
        parser.error("refusing to seed the prod table")
    if args.apply and not args.table:
        parser.error("--apply requires --table")
    records = json.loads(args.merged.read_text())
    if args.apply:
        client = DynamoClient(args.table)
    counts = seed(records, client, args.table)
    mode = "APPLIED" if client else "DRY RUN"
    print(f"{mode}: {len(records)} records")
    for key, value in sorted(counts.items()):
        print(f"  {key:40s} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
