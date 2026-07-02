#!/usr/bin/env python3
"""Ingest the per-merchant item catalog into DynamoDB (MerchantCatalogItem).

Builds each merchant's catalog once and persists it so the add-line-item
augmentation queries Dynamo instead of re-deriving the catalog from curated JSON
+ a full receipt scan on every run.

Two sources, merged by normalized product name:
  * curated  -- brand-correct products from scripts/data/online_catalogs/*.json
               (source="online_catalog")
  * observed -- items mined from the merchant's real receipts: any line with a
               PRODUCT_NAME word + a LINE_TOTAL price (source="observed"),
               counted across receipts, category from the nearest section header.
Items present both ways become source="merged" (curated price/taxable/upc win,
observed count + provenance kept).

Idempotent: --apply deletes the merchant's existing catalog then writes fresh.
Dry-run by default. Dev/prod via DYNAMODB_TABLE_NAME or PORTFOLIO_ENV.

Usage:
  DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 \
  python scripts/ingest_merchant_catalog.py [--merchant "Sprouts Farmers Market"] \
      [--apply] [--limit-receipts N]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

from receipt_dynamo import DynamoClient, MerchantCatalogItem
from receipt_dynamo.entities.merchant_catalog_item import (
    normalize_product_text,
    slugify_merchant,
)

# Canonical per-merchant extractors (the SAME code the synthesis/augmentation
# engine uses). When importable, a parameterized merchant (Sprouts) is mined via
# its own catalog builder so the persisted catalog IS the engine's catalog --
# real per-item taxable + parsed section categories -- instead of the generic
# label miner below. Falls back to the generic miner if receipt_agent's
# parameterization isn't on the path.
try:
    from receipt_agent.agents.label_evaluator.pattern_discovery import (
        build_receipt_structure,
    )
    from receipt_agent.agents.label_evaluator.sprouts_parameterization import (
        _analyze_arithmetic_receipt,
        _build_item_catalog,
        _normalize_receipt,
        is_sprouts_merchant,
    )

    _HAS_PARAMETERIZATION = True
except ImportError:
    _HAS_PARAMETERIZATION = False

    def is_sprouts_merchant(_name: str | None) -> bool:  # type: ignore
        return False


REGION = os.environ.get("AWS_REGION", "us-east-1")
CATALOG_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "online_catalogs"
)
# Curated-JSON slug -> canonical merchant_name (as stored on receipts).
MERCHANT_NAMES = {
    "amazon_fresh": "Amazon Fresh",
    "costco_wholesale": "Costco Wholesale",
    "sprouts_farmers_market": "Sprouts Farmers Market",
    "target": "Target",
}
SECTION_KEYWORDS = {
    "PRODUCE",
    "DAIRY",
    "GROCERY",
    "BAKERY",
    "MEAT",
    "SEAFOOD",
    "FROZEN",
    "DELI",
    "BULK",
    "VITAMINS",
    "BODY",
    "HOUSEHOLD",
    "BEVERAGES",
    "SNACKS",
    "PANTRY",
    "REFRIGERATED",
    "WELLNESS",
}
_PRICE_RE = re.compile(r"-?\d+\.\d{2}")


def resolve_table() -> str:
    t = os.environ.get("DYNAMODB_TABLE_NAME")
    if t:
        return t
    from receipt_dynamo.data._pulumi import load_env

    return load_env(env=os.environ.get("PORTFOLIO_ENV", "dev"))[
        "dynamodb_table_name"
    ]


def _price_str(text: str) -> str | None:
    m = _PRICE_RE.search(text.replace("$", ""))
    if not m:
        return None
    try:
        return str(Decimal(m.group(0)))
    except InvalidOperation:
        return None


def load_curated(merchant_name: str) -> list[dict]:
    slug = slugify_merchant(merchant_name)
    path = os.path.join(CATALOG_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("entries", [])


NAME_LABELS = {"PRODUCT_NAME", "ITEM_NAME"}
PRICE_LABELS = {"LINE_TOTAL", "ITEM_TOTAL"}


def _ycx(word) -> tuple[float, float, float]:
    """(y_center, x_center, height) from a word's normalized corners."""
    tl, br = word.top_left, word.bottom_right
    return (
        (tl["y"] + br["y"]) / 2.0,
        (tl["x"] + br["x"]) / 2.0,
        abs(br["y"] - tl["y"]) or 0.01,
    )


def mine_observed(
    client: DynamoClient, merchant_name: str, limit_receipts: int | None
) -> dict[str, dict]:
    """normalized_name -> {product_text, prices[], count, keys[], category}.

    The item name and its price sit on the same visual ROW but different OCR
    line_ids, so we match a priced word to the name word(s) in the same y-band
    to its left. Section (category) = nearest section-header row above the item.
    """
    places, _ = client.get_receipt_places_by_merchant(merchant_name)
    obs: dict[str, dict] = {}
    seen: set[tuple[str, int]] = set()
    for place in places:
        key = (str(place.image_id), int(place.receipt_id))
        if key in seen:
            continue
        seen.add(key)
        if limit_receipts and len(seen) > limit_receipts:
            break
        try:
            det = client.get_receipt_details(key[0], key[1])
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {key}: {type(exc).__name__}", file=sys.stderr)
            continue
        label = {(l.line_id, l.word_id): l.label for l in det.labels}
        names, prices, sections = [], [], []
        for w in det.words:
            lb = label.get((w.line_id, w.word_id))
            y, x, h = _ycx(w)
            if lb in NAME_LABELS:
                names.append((y, x, str(w.text)))
            elif lb in PRICE_LABELS:
                pr = _price_str(str(w.text))
                if pr is not None:
                    prices.append((y, x, h, pr))
            elif str(w.text).upper().strip(":") in SECTION_KEYWORDS:
                sections.append((y, str(w.text).upper().strip(":")))
        for py, px, ph, pr in prices:
            band = [
                n
                for n in names
                if abs(n[0] - py) < max(0.006, ph * 0.8) and n[1] < px
            ]
            if not band:
                continue
            band.sort(key=lambda n: n[1])
            product_text = " ".join(n[2] for n in band).strip()
            if not product_text or _PRICE_RE.search(product_text):
                continue  # skip rows where the "name" is itself numeric
            # section = the nearest section-header row (orientation-free: items
            # cluster right under their header, so nearest-by-y-distance is a
            # robust heuristic without knowing the y sign convention).
            cat = "UNCATEGORIZED"
            if sections:
                cat = min(sections, key=lambda s: abs(s[0] - py))[1]
            norm = normalize_product_text(product_text)
            rec = obs.setdefault(
                norm,
                {
                    "product_text": product_text,
                    "prices": [],
                    "count": 0,
                    "keys": [],
                    "category": cat,
                },
            )
            rec["prices"].append(pr)
            rec["count"] += 1
            rk = f"{key[0]}#{key[1]:05d}"
            if rk not in rec["keys"]:
                rec["keys"].append(rk)
            if rec["category"] == "UNCATEGORIZED" and cat != "UNCATEGORIZED":
                rec["category"] = cat
    return obs


def mine_observed_canonical(
    client: DynamoClient, merchant_name: str, limit_receipts: int | None
) -> dict[str, dict]:
    """Observed items via the engine's own catalog builder (_build_item_catalog).

    Uses the exact loader + analysis + item-catalog code the augmentation engine
    uses, so the persisted catalog matches what the engine would build: real
    per-item ``taxable`` and parsed section ``category``. Same return shape as
    mine_observed (with a ``taxable`` key).
    """
    receipts_data = build_receipt_structure(
        client, merchant_name, limit=limit_receipts or 200
    )
    analyses = [
        a
        for a in (
            _analyze_arithmetic_receipt(_normalize_receipt(r))
            for r in receipts_data
        )
        if a
    ]
    obs: dict[str, dict] = {}
    for entry in _build_item_catalog(analyses):
        d = entry.to_dict()
        text = str(d.get("product_text") or "").strip()
        price = _price_str(str(d.get("line_total") or ""))
        if not text or price is None:
            continue
        norm = normalize_product_text(text)
        obs[norm] = {
            "product_text": text,
            "prices": [price],
            "count": int(d.get("observed_count") or 0),
            "keys": list(d.get("source_receipt_keys") or [])[:5],
            "category": d.get("category") or "UNCATEGORIZED",
            "taxable": bool(d.get("taxable", False)),
        }
    return obs


def build_catalog(
    merchant_name: str, curated: list[dict], observed: dict[str, dict]
) -> list[MerchantCatalogItem]:
    merged: dict[str, MerchantCatalogItem] = {}
    # curated first
    for e in curated:
        name = str(e.get("name", "")).strip()
        price = e.get("price")
        if not name or price is None:
            continue
        norm = normalize_product_text(name)
        merged[norm] = MerchantCatalogItem(
            merchant_name=merchant_name,
            product_text=name,
            price=str(price),
            category="UNCATEGORIZED",
            taxable=bool(e.get("taxable", False)),
            source="online_catalog",
            observed_count=0,
            upc=(e.get("upc") or None),
        )
    # observed, merging by normalized name
    for norm, rec in observed.items():
        mode_price = collections.Counter(rec["prices"]).most_common(1)[0][0]
        if norm in merged:
            base = merged[norm]
            merged[norm] = MerchantCatalogItem(
                merchant_name=merchant_name,
                product_text=base.product_text,
                price=base.price,
                category=(
                    rec["category"]
                    if rec["category"] != "UNCATEGORIZED"
                    else base.category
                ),
                taxable=base.taxable,
                source="merged",
                observed_count=rec["count"],
                upc=base.upc,
                source_receipt_keys=rec["keys"][:5],
            )
        else:
            merged[norm] = MerchantCatalogItem(
                merchant_name=merchant_name,
                product_text=rec["product_text"],
                price=mode_price,
                category=rec["category"],
                taxable=bool(rec.get("taxable", False)),
                source="observed",
                observed_count=rec["count"],
                source_receipt_keys=rec["keys"][:5],
            )
    return list(merged.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merchant", help="One merchant (default: all curated)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit-receipts", type=int, default=None)
    args = ap.parse_args()

    table = resolve_table()
    client = DynamoClient(table_name=table, region=REGION)
    merchants = (
        [args.merchant] if args.merchant else list(MERCHANT_NAMES.values())
    )
    print(
        f"[{'APPLY' if args.apply else 'DRY-RUN'}] table={table} "
        f"merchants={merchants}"
    )

    grand = 0
    for merchant in merchants:
        curated = load_curated(merchant)
        # Parameterized merchants (Sprouts) mine via the engine's own catalog
        # builder so the persisted catalog == the engine's catalog; others use
        # the generic label miner.
        if _HAS_PARAMETERIZATION and is_sprouts_merchant(merchant):
            observed = mine_observed_canonical(
                client, merchant, args.limit_receipts
            )
            miner = "canonical(_build_item_catalog)"
        else:
            observed = mine_observed(client, merchant, args.limit_receipts)
            miner = "generic"
        items = build_catalog(merchant, curated, observed)
        by_src = collections.Counter(i.source for i in items)
        print(f"\n=== {merchant} ===")
        print(
            f"  miner={miner} curated={len(curated)} observed={len(observed)} "
            f"-> catalog={len(items)}  by_source={dict(by_src)}"
        )
        for it in sorted(items, key=lambda x: -x.observed_count)[:8]:
            print(
                f"    {it.category:12} {it.product_text[:34]:34} "
                f"${it.price:>7}  x{it.observed_count} [{it.source}]"
            )
        if args.apply:
            client.delete_merchant_catalog(merchant)
            client.add_merchant_catalog_items(items)
            print(f"  written: {len(items)} items")
        grand += len(items)

    print(
        f"\nTOTAL catalog items: {grand} "
        f"({'written' if args.apply else 'DRY-RUN'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
