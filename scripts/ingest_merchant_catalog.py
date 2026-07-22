#!/usr/bin/env python3
"""Mine a merchant's observed item catalog into DynamoDB (MerchantCatalogItem).

OBSERVED MINER ONLY (owner decision): every catalog item is mined from the
merchant's own labeled receipts — there is no curated/online-catalog source.
Items are priced words (LINE_TOTAL / ITEM_TOTAL) matched to the
PRODUCT_NAME / ITEM_NAME words in the same y-band to their left; the
category comes from the nearest section-header row; a NON_PRODUCT stoplist
keeps tax/totals/tender/coupon rows out. Every mined item records the
receipt keys it was observed on (``source_receipt_keys``).

ATTRIBUTION SPOT-CHECK (before any mining): each receipt's merchant
attribution (the ReceiptPlace merchant) is cross-checked against brand
signals in its OCR text and label-reasoning text. A receipt whose signals
conflict — e.g. a costco-attributed receipt whose words/reasoning reference
BJ's — is QUARANTINED into the attribution report and excluded from mining.
Conflicted receipts are never silently mined.

DRY-RUN by default: prints the attribution report and the would-be catalog,
performs zero writes. ``--apply`` (owner-gated) deletes the merchant's
existing catalog partition and writes the mined items fresh — to the dev
table only. Safety matches the sibling scripts exactly: the dev table
(ReceiptsTable-dc5be22) is the default via DYNAMODB_TABLE_NAME; the prod
table (ReceiptsTable-d7ff76a, or any name carrying the d7ff76a marker) is
refused unconditionally BEFORE any client construction.

Usage:
  python scripts/ingest_merchant_catalog.py --merchant "Costco Wholesale" \
      [--apply] [--limit-receipts N] [--dump items.json] \
      [--attribution-report attribution.json] [--table TABLE]

Exit codes: 0 success (including dry-run); 1 data error (e.g. nothing
mined); 2 refused configuration.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Iterable, Iterator

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (REPO, os.path.join(REPO, "receipt_dynamo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_dynamo.entities.merchant_catalog_item import (  # noqa: E402
    MerchantCatalogItem,
    normalize_product_text,
)

# Table pinning is the diff tool's: import, don't copy.
from scripts.merchant_truth_diff import (  # noqa: E402
    DEV_TABLE_NAME,
    PROD_TABLE_MARKER,
    PROD_TABLE_NAME,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo.entities.receipt_details import ReceiptDetails

REGION = os.environ.get("AWS_REGION", "us-east-1")

NAME_LABELS = {"PRODUCT_NAME", "ITEM_NAME"}
PRICE_LABELS = {"LINE_TOTAL", "ITEM_TOTAL"}
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

# Receipt lines that carry a price but are NOT products — payments, tax,
# totals, coupons/credits, tender. The y-band miner is naive, so it needs
# this stoplist or it injects fake products (e.g. "CREDIT", "TAX") into the
# catalog. Matched on letters-only uppercase.
NON_PRODUCT = {
    "CREDIT",
    "CREDITCARD",
    "DEBIT",
    "DEBITCARD",
    "CASH",
    "CHANGE",
    "CHANGEDUE",
    "TENDER",
    "TENDERED",
    "VISA",
    "MASTERCARD",
    "MC",
    "AMEX",
    "DISCOVER",
    "PAYMENT",
    "TIP",
    "TAX",
    "SALESTAX",
    "TX",
    "STATETAX",
    "SUBTOTAL",
    "SUBTTL",
    "TOTAL",
    "TOTALSALE",
    "GRANDTOTAL",
    "NETSALES",
    "BALANCE",
    "BALANCEDUE",
    "AMOUNT",
    "AMOUNTDUE",
    "AMTDUE",
    "DUE",
    "COUPON",
    "COUPONS",
    "SAVINGS",
    "TOTALSAVINGS",
    "YOUSAVED",
    "SAVED",
    "DISCOUNT",
    "MEMBER",
    "LOYALTY",
    "REWARD",
    "REWARDS",
    "REFUND",
    "RETURN",
    "VOID",
    "ROUNDING",
    "GROSS",
    "SUBTOT",
    "PURCHASE",
    "CRV",
    "DEPOSIT",
    "BOTTLEDEPOSIT",
    "BAGFEE",
    "BAG",
    "ITEMS",
    "ITEMSSOLD",
}

# ---------------------------------------------------------------------------
# Attribution spot-check
# ---------------------------------------------------------------------------

# Brand signatures for the attribution cross-check. Aliases are uppercase
# regexes matched against the receipt's OCR text and label-reasoning text.
# House brands count as supporting evidence (KIRKLAND -> Costco, WELLSLEY
# FARMS / BERKLEY JENSEN -> BJ's).
BRAND_SIGNALS: dict[str, tuple[str, ...]] = {
    "costco": (r"\bCOSTCO\b", r"\bKIRKLAND\b"),
    "bjs_wholesale": (
        r"\bBJ\W?S\b",
        r"\bWELLSLEY\s+FARMS?\b",
        r"\bBERKLEY\s+JENSEN\b",
    ),
    "sams_club": (r"\bSAM\W?S\s+CLUB\b", r"\bMEMBER\W?S\s+MARK\b"),
    "sprouts": (r"\bSPROUTS\b",),
    "target": (r"\bTARGET\b",),
    "walmart": (r"\bWAL\W?MART\b", r"\bGREAT\s+VALUE\b"),
    "trader_joes": (r"\bTRADER\s+JOE\W?S?\b",),
    "whole_foods": (r"\bWHOLE\s+FOODS\b",),
    "safeway": (r"\bSAFEWAY\b",),
    "albertsons": (r"\bALBERTSONS?\b",),
    "vons": (r"\bVONS\b",),
    "kroger": (r"\bKROGER\b",),
    "smiths": (r"\bSMITH\W?S\s+(?:FOOD|MARKETPLACE)\b",),
    "in_n_out": (r"\bIN\W?N\W?OUT\b",),
    "dollar_tree": (r"\bDOLLAR\s+TREE\b",),
    "cvs": (r"\bCVS\b",),
    "walgreens": (r"\bWALGREENS\b",),
    "home_depot": (r"\bHOME\s+DEPOT\b",),
    "lowes": (r"\bLOWE\W?S\b",),
}
_BRAND_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    brand: tuple(re.compile(alias) for alias in aliases)
    for brand, aliases in BRAND_SIGNALS.items()
}

# Brands whose aliases are generic English words are only trusted in the
# receipt's OCR text, never in label-reasoning prose: LLM reasoning says
# "the TARGET line" constantly (measured: 12/39 dev Costco receipts would
# false-quarantine on it).
OCR_ONLY_BRANDS = frozenset({"target"})


@dataclass
class AttributionCheck:
    """Cross-check result for one receipt's merchant attribution."""

    receipt_key: str
    merchant_name: str
    attributed_brand: str | None
    verdict: str  # OK | QUARANTINED | NO_BASELINE | NO_SIGNAL
    supporting: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _snippet(corpus: str, match: re.Match[str], radius: int = 28) -> str:
    start = max(0, match.start() - radius)
    end = min(len(corpus), match.end() + radius)
    return " ".join(corpus[start:end].split())


def _brand_for_merchant(merchant_name: str) -> str | None:
    upper = merchant_name.upper()
    for brand, patterns in _BRAND_PATTERNS.items():
        if any(pattern.search(upper) for pattern in patterns):
            return brand
    return None


def _find_evidence(
    corpus: str, source: str, brands: Iterable[str]
) -> dict[str, list[str]]:
    """brand -> ["<source>: ...snippet..." ...] for every alias hit."""
    hits: dict[str, list[str]] = {}
    for brand in brands:
        for pattern in _BRAND_PATTERNS[brand]:
            for match in pattern.finditer(corpus):
                hits.setdefault(brand, []).append(
                    f"{source}: ...{_snippet(corpus, match)}..."
                )
    return hits


def check_attribution(
    merchant_name: str, receipt_key: str, details: "ReceiptDetails"
) -> AttributionCheck:
    """Cross-check one receipt's attribution against its content signals.

    Signals: brand-alias matches in the OCR word text and in the label
    reasoning text. Any foreign-brand evidence quarantines the receipt —
    with or without supporting evidence for the attributed brand — because
    a conflicted receipt must never be silently mined.
    """
    ocr_corpus = " ".join(
        str(word.text) for word in details.words if word.text
    ).upper()
    reasoning_corpus = " ".join(
        label.reasoning for label in details.labels if label.reasoning
    ).upper()

    attributed = _brand_for_merchant(merchant_name)
    if attributed is None:
        return AttributionCheck(
            receipt_key=receipt_key,
            merchant_name=merchant_name,
            attributed_brand=None,
            verdict="NO_BASELINE",
            reasons=[
                "no brand signature for this merchant; attribution not "
                "cross-checkable (mined as-is)"
            ],
        )

    supporting: list[str] = []
    conflicting: list[str] = []
    foreign = [brand for brand in _BRAND_PATTERNS if brand != attributed]
    for source, corpus in (
        ("ocr", ocr_corpus),
        ("reasoning", reasoning_corpus),
    ):
        attributed_here = (
            [attributed]
            if source == "ocr" or attributed not in OCR_ONLY_BRANDS
            else []
        )
        foreign_here = (
            foreign
            if source == "ocr"
            else [b for b in foreign if b not in OCR_ONLY_BRANDS]
        )
        for evidence in _find_evidence(
            corpus, source, attributed_here
        ).values():
            supporting.extend(evidence)
        for brand, evidence in _find_evidence(
            corpus, source, foreign_here
        ).items():
            conflicting.extend(f"[{brand}] {entry}" for entry in evidence)

    if conflicting:
        reasons = [
            f"foreign-brand evidence found ({len(conflicting)} hit(s)) "
            f"for a {attributed}-attributed receipt"
        ]
        reasons.append(
            "no supporting evidence for the attributed brand"
            if not supporting
            else "ambiguous: supporting evidence also present"
        )
        return AttributionCheck(
            receipt_key=receipt_key,
            merchant_name=merchant_name,
            attributed_brand=attributed,
            verdict="QUARANTINED",
            supporting=supporting,
            conflicting=conflicting,
            reasons=reasons,
        )
    if not supporting:
        return AttributionCheck(
            receipt_key=receipt_key,
            merchant_name=merchant_name,
            attributed_brand=attributed,
            verdict="NO_SIGNAL",
            reasons=[
                "no brand evidence either way (mined; flagged for review)"
            ],
        )
    return AttributionCheck(
        receipt_key=receipt_key,
        merchant_name=merchant_name,
        attributed_brand=attributed,
        verdict="OK",
        supporting=supporting,
    )


# ---------------------------------------------------------------------------
# Observed miner
# ---------------------------------------------------------------------------


def _is_non_product(text: str) -> bool:
    """True for priced-but-not-a-product lines (tax, totals, tender)."""
    key = re.sub(r"[^A-Z]", "", text.upper())
    return len(key) < 2 or key in NON_PRODUCT


def _price_str(text: str) -> str | None:
    match = _PRICE_RE.search(text.replace("$", ""))
    if not match:
        return None
    try:
        return str(Decimal(match.group(0)))
    except InvalidOperation:
        return None


def _ycx(word: Any) -> tuple[float, float, float]:
    """(y_center, x_center, height) from a word's normalized corners."""
    top_left, bottom_right = word.top_left, word.bottom_right
    return (
        (top_left["y"] + bottom_right["y"]) / 2.0,
        (top_left["x"] + bottom_right["x"]) / 2.0,
        abs(bottom_right["y"] - top_left["y"]) or 0.01,
    )


def mine_receipt(receipt_key: str, details: "ReceiptDetails") -> list[dict]:
    """Observed product rows for one receipt.

    The item name and its price sit on the same visual ROW but different
    OCR line_ids, so each priced word (LINE_TOTAL/ITEM_TOTAL) is matched to
    the name word(s) (PRODUCT_NAME/ITEM_NAME) in the same y-band to its
    LEFT. Category = nearest section-header row by y-distance
    (orientation-free: items cluster right under their header).
    """
    label_by_word = {
        (label.line_id, label.word_id): label.label for label in details.labels
    }
    names: list[tuple[float, float, str]] = []
    prices: list[tuple[float, float, float, str]] = []
    sections: list[tuple[float, str]] = []
    for word in details.words:
        word_label = label_by_word.get((word.line_id, word.word_id))
        y_center, x_center, height = _ycx(word)
        text = str(word.text)
        if word_label in NAME_LABELS:
            names.append((y_center, x_center, text))
        elif word_label in PRICE_LABELS:
            price = _price_str(text)
            # negative line totals are markdowns/credits, not products
            if price is not None and not price.startswith("-"):
                prices.append((y_center, x_center, height, price))
        elif text.upper().strip(":") in SECTION_KEYWORDS:
            sections.append((y_center, text.upper().strip(":")))

    rows: list[dict] = []
    for price_y, price_x, price_h, price in prices:
        band = [
            name
            for name in names
            if abs(name[0] - price_y) < max(0.006, price_h * 0.8)
            and name[1] < price_x
        ]
        if not band:
            continue
        band.sort(key=lambda name: name[1])
        product_text = " ".join(name[2] for name in band).strip()
        if not product_text or _PRICE_RE.search(product_text):
            continue  # skip rows where the "name" is itself numeric
        if _is_non_product(product_text):
            continue  # payments, tax, totals, coupons — not products
        category = "UNCATEGORIZED"
        if sections:
            category = min(
                sections, key=lambda section: abs(section[0] - price_y)
            )[1]
        rows.append(
            {
                "product_text": product_text,
                "price": price,
                "category": category,
                "receipt_key": receipt_key,
            }
        )
    return rows


def aggregate_observations(
    mined_rows: Iterable[dict],
) -> dict[str, dict]:
    """normalized_name -> {product_text, prices[], count, keys[], category}."""
    observations: dict[str, dict] = {}
    for row in mined_rows:
        norm = normalize_product_text(row["product_text"])
        record = observations.setdefault(
            norm,
            {
                "product_text": row["product_text"],
                "prices": [],
                "count": 0,
                "keys": [],
                "category": row["category"],
            },
        )
        record["prices"].append(row["price"])
        record["count"] += 1
        if row["receipt_key"] not in record["keys"]:
            record["keys"].append(row["receipt_key"])
        if (
            record["category"] == "UNCATEGORIZED"
            and row["category"] != "UNCATEGORIZED"
        ):
            record["category"] = row["category"]
    return observations


def build_catalog(
    merchant_name: str, observations: dict[str, dict]
) -> list[MerchantCatalogItem]:
    """Aggregated observations -> MerchantCatalogItem rows (mined-only)."""
    items: list[MerchantCatalogItem] = []
    for record in observations.values():
        mode_price = collections.Counter(record["prices"]).most_common(1)[0][0]
        items.append(
            MerchantCatalogItem(
                merchant_name=merchant_name,
                product_text=record["product_text"],
                price=mode_price,
                category=record["category"],
                taxable=False,
                source="observed",
                observed_count=record["count"],
                source_receipt_keys=list(record["keys"]),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Table pinning / CLI
# ---------------------------------------------------------------------------


def resolve_table(cli_table: str | None) -> str:
    """Resolve the target table and refuse prod unconditionally.

    Same stance as the sibling merchant-truth scripts: default dev via
    DYNAMODB_TABLE_NAME, and the prod table name or any name carrying the
    prod marker substring is refused before any DynamoDB client exists.
    """
    table = (
        cli_table or os.environ.get("DYNAMODB_TABLE_NAME") or DEV_TABLE_NAME
    )
    if table == PROD_TABLE_NAME or PROD_TABLE_MARKER in table:
        raise ValueError(
            f"ingest_merchant_catalog never touches prod; refusing table "
            f"{table!r} (prod = {PROD_TABLE_NAME!r})"
        )
    return table


def fetch_receipts(
    client: "DynamoClient",
    merchant_name: str,
    limit_receipts: int | None,
) -> Iterator[tuple[str, "ReceiptDetails"]]:
    """Yield (receipt_key, details) per unique receipt for the merchant."""
    places, _ = client.get_receipt_places_by_merchant(merchant_name)
    seen: set[tuple[str, int]] = set()
    for place in places:
        key = (str(place.image_id), int(place.receipt_id))
        if key in seen:
            continue
        seen.add(key)
        if limit_receipts and len(seen) > limit_receipts:
            break
        try:
            details = client.get_receipt_details(key[0], key[1])
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {key}: {type(exc).__name__}", file=sys.stderr)
            continue
        yield f"{key[0]}#{key[1]:05d}", details


def attribution_report_document(
    merchant_name: str,
    table: str,
    checks: list[AttributionCheck],
) -> dict[str, Any]:
    """The attribution report: every check, quarantine list first."""
    by_verdict = collections.Counter(check.verdict for check in checks)
    return {
        "merchant": merchant_name,
        "table": table,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "receipts_checked": len(checks),
        "verdicts": dict(by_verdict),
        "quarantined": [
            asdict(check) for check in checks if check.verdict == "QUARANTINED"
        ],
        "checks": [asdict(check) for check in checks],
    }


def main(
    argv: list[str] | None = None,
    *,
    client: "DynamoClient | None" = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--merchant",
        required=True,
        help='canonical merchant name, e.g. "Costco Wholesale"',
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "actually write the mined catalog (delete + fresh write of the "
            "merchant's partition); default is DRY-RUN with zero writes"
        ),
    )
    parser.add_argument("--limit-receipts", type=int, default=None)
    parser.add_argument(
        "--dump",
        metavar="PATH",
        help="write every mined catalog item (JSON) here for inspection",
    )
    parser.add_argument(
        "--attribution-report",
        metavar="PATH",
        help="write the attribution spot-check report (JSON) here",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="DynamoDB table (default: DYNAMODB_TABLE_NAME env or "
        f"{DEV_TABLE_NAME}); prod is refused unconditionally",
    )
    args = parser.parse_args(argv)

    try:
        # Prod refusal happens here — before any client construction.
        table = resolve_table(args.table)
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    if client is None:
        from receipt_dynamo.data.dynamo_client import (  # noqa: PLC0415
            DynamoClient,
        )

        client = DynamoClient(table_name=table, region=REGION)

    merchant = args.merchant
    print(
        f"[{'APPLY' if args.apply else 'DRY-RUN'}] table={table} "
        f"merchant={merchant!r}"
    )

    # ---- Pass 1: attribution spot-check (before ANY mining) --------------
    checks: list[AttributionCheck] = []
    minable: list[tuple[str, "ReceiptDetails"]] = []
    for receipt_key, details in fetch_receipts(
        client, merchant, args.limit_receipts
    ):
        check = check_attribution(merchant, receipt_key, details)
        checks.append(check)
        if check.verdict != "QUARANTINED":
            minable.append((receipt_key, details))

    quarantined = [c for c in checks if c.verdict == "QUARANTINED"]
    print(
        f"\nattribution spot-check: {len(checks)} receipt(s) — "
        + ", ".join(
            f"{verdict}={count}"
            for verdict, count in sorted(
                collections.Counter(c.verdict for c in checks).items()
            )
        )
    )
    for check in quarantined:
        print(f"  QUARANTINED {check.receipt_key} ({check.merchant_name})")
        for reason in check.reasons:
            print(f"    - {reason}")
        for evidence in check.conflicting[:4]:
            print(f"    * {evidence}")
    if args.attribution_report:
        document = attribution_report_document(merchant, table, checks)
        with open(args.attribution_report, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
        print(f"attribution report -> {args.attribution_report}")

    # ---- Pass 2: mine the non-quarantined receipts -----------------------
    mined_rows: list[dict] = []
    for receipt_key, details in minable:
        mined_rows.extend(mine_receipt(receipt_key, details))
    observations = aggregate_observations(mined_rows)
    items = build_catalog(merchant, observations)

    by_category = collections.Counter(item.category for item in items)
    print(
        f"\n=== {merchant} ===\n"
        f"  receipts mined={len(minable)} (quarantined="
        f"{len(quarantined)})  observed rows={len(mined_rows)} "
        f"-> catalog items={len(items)}"
    )
    print(
        "  by_category="
        + json.dumps(dict(sorted(by_category.items())), sort_keys=True)
    )
    for item in sorted(items, key=lambda x: -x.observed_count)[:10]:
        keys_preview = ",".join(item.source_receipt_keys[:2])
        more = len(item.source_receipt_keys) - 2
        if more > 0:
            keys_preview += f",+{more}"
        print(
            f"    {item.category:14} {item.product_text[:34]:34} "
            f"${item.price:>7}  x{item.observed_count}  [{keys_preview}]"
        )

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {
                        "product_text": item.product_text,
                        "price": item.price,
                        "category": item.category,
                        "taxable": item.taxable,
                        "source": item.source,
                        "observed_count": item.observed_count,
                        "source_receipt_keys": item.source_receipt_keys,
                    }
                    for item in sorted(
                        items,
                        key=lambda x: (x.category, -x.observed_count),
                    )
                ],
                handle,
                indent=2,
            )
        print(f"  dumped {len(items)} items -> {args.dump}")

    if not args.apply:
        print(
            f"\nDRY-RUN: no writes performed ({len(items)} item(s) staged; "
            "pass --apply to write)"
        )
        return 0

    if not items:
        print(
            "REFUSED: nothing mined; not clearing the existing catalog",
            file=sys.stderr,
        )
        return 1
    banner = "=" * 72
    print(
        f"\n{banner}\nAPPLY: rewriting catalog partition for {merchant!r} "
        f"on table {table}\n{banner}"
    )
    client.delete_merchant_catalog(merchant)
    client.add_merchant_catalog_items(items)
    print(f"  written: {len(items)} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
