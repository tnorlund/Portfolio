#!/usr/bin/env python3
"""Export real-receipt decoder walkthrough data for the portfolio site.

Builds ``portfolio/public/line-item-demo/receipts.json``: a static,
self-contained payload the LineItemDecoderVisualization figure replays in
the browser. Per receipt it carries the decoder's actual intermediate
stages — visual bands, guard rejections (with which guard fired), role
assignment, absorption, the decoded items with word-level provenance, the
summary-figure filter's drops, and the reconciliation verdict — plus full
word bounding boxes and CDN image keys so the stages can be drawn over the
receipt photo.

Decoding runs over the committed golden OCR fixture
(``receipt_upload/tests/fixtures/line_items_golden_ocr.json``) so the
exported stages are byte-identical to what the CI parity gate pins; the
live dev table contributes only what the fixture lacks: full word
geometry (the fixture stores x/y_mid/h without width) and the receipt's
CDN image keys. Receipts whose fixture words no longer join against the
live table, or whose images are missing from either CDN, are dropped.

Usage:
    python3 scripts/export_line_item_viz_data.py            # write JSON
    python3 scripts/export_line_item_viz_data.py --analyze  # survey only
    python3 scripts/export_line_item_viz_data.py --all      # export all
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "receipt_dynamo"))
sys.path.insert(0, str(REPO_ROOT / "receipt_upload"))

OCR_FIXTURE = (
    REPO_ROOT / "receipt_upload/tests/fixtures/line_items_golden_ocr.json"
)
GOLDEN_FIXTURE = (
    REPO_ROOT / "receipt_upload/tests/fixtures/line_items_golden.json"
)
DEFAULT_OUTPUT = REPO_ROOT / "portfolio/public/line-item-demo/receipts.json"

DEV_CDN = "https://dev.tylernorlund.com"
PROD_CDN = "https://www.tylernorlund.com"

# Curated showcase set: stage-story diversity over merchant diversity.
# Each entry is (image_id_prefix, receipt_id, why it earns the slot).
SHOWCASE: list[tuple[str, int, str]] = [
    ("f1844265", 1, "Trader Joe's — settlement guards, 4 qty items, match"),
    ("cf8e3a6e", 1, "Sprouts — 14 items, 5 weighted quantities, match"),
    ("2360d36e", 1, "In-N-Out — summary-figure filter drop, qty math"),
    ("8d089201", 1, "Gelson's — 4 printed quantities, clean match"),
    ("63243f38", 2, "Sprouts — sale-price guard + discount, near"),
    ("9ed1103e", 1, "Wild Fork — SKU rows, stacked name stealing"),
    ("1bfb07b7", 1, "Home Depot — 17 items, discounts, priors, near"),
    ("ccfbeaca", 1, "Home Depot — honest mismatch on a long receipt"),
]


def _money(text: Any) -> Optional[float]:
    if text is None:
        return None
    cleaned = str(text).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def build_summary(golden: Optional[dict]) -> Optional[dict]:
    """Same shape generate_line_items_parity.py builds: no printed tax."""
    if not golden:
        return None
    subtotal = _money(golden.get("printed_subtotal"))
    grand = _money(golden.get("printed_total"))
    if subtotal is None and grand is None:
        return None
    return {"subtotal": subtotal, "tax": None, "grand_total": grand}


def annotate_bands(ocr_receipt: dict, priors: dict) -> list[dict]:
    """_zone_bands plus the exact guard/role loop from decode_band_blocks,
    instrumented to record WHICH guard or prior produced each verdict.

    Guard order matters and must mirror blocks.decode_band_blocks: the
    first guard to fire is the recorded reason.
    """
    from receipt_upload.line_items.blocks import _zone_bands
    from receipt_upload.line_items.geometry import (
        NON_PRODUCT_NOTE_RE,
        SALE_PRICE_RE,
        WAS_PRICE_RE,
        is_settlement_row,
        is_unit_rate_row,
    )

    bands = _zone_bands(ocr_receipt)
    out = []
    for i, b in enumerate(bands):
        bare = re.sub(r"\$?\d[\d.,]*", " ", b["text"]).strip()
        guard = None
        if is_settlement_row(bare):
            guard = "settlement"
        elif WAS_PRICE_RE.search(b["text"]):
            guard = "was_price"
        elif SALE_PRICE_RE.search(b["text"]):
            guard = "sale_price"
        elif NON_PRODUCT_NOTE_RE.search(b["text"]):
            guard = "non_product_note"
        elif is_unit_rate_row(b["text"], len(b["amounts"])):
            guard = "unit_rate"

        prior = priors.get(b["template"])
        prior_used = (
            prior is not None
            and prior["purity"] >= 0.75
            and prior["support"] >= 2
        )
        if guard:
            role = "OUTSIDE"
        elif prior_used:
            role = prior["role"]
        elif b["amounts"]:
            role = "PRICE"
        elif re.search(r"[A-Za-z]{3,}", b["text"]):
            role = "MEMBER"
        else:
            role = "OUTSIDE"

        out.append(
            {
                "band_id": i,
                "line_ids": b["line_ids"],
                "word_refs": [
                    [w["line_id"], w["word_id"]] for w in b["words"]
                ],
                "text": b["text"],
                "y": round(b["y"], 5),
                "amounts": b["amounts"],
                "role": role,
                "guard": guard,
                "prior": (
                    {
                        "role": prior["role"],
                        "support": prior["support"],
                        "purity": round(prior["purity"], 3),
                    }
                    if prior_used
                    else None
                ),
            }
        )
    return out


def _wref(w: Optional[dict]) -> Optional[list[int]]:
    if not w:
        return None
    return [w["line_id"], w["word_id"]]


def dump_item(item: dict, y_lookup: dict) -> dict:
    """Block-decoder items carry no y; anchor display order on the price
    word's y_mid (fallback: any name word)."""
    y = None
    for ref in [item.get("price_word_id"), *item.get("name_word_ids", [])]:
        if ref and (ref["line_id"], ref["word_id"]) in y_lookup:
            y = round(y_lookup[(ref["line_id"], ref["word_id"])], 5)
            break
    return {
        "y": y,
        "name": item.get("name"),
        "price": item.get("price"),
        "quantity": item.get("quantity"),
        "unit_price": item.get("unit_price"),
        "is_discount": bool(item.get("is_discount")),
        "stacked": bool(item.get("stacked")),
        "name_quality": item.get("name_quality"),
        "line_ids": sorted(item.get("line_ids", [])),
        "name_word_ids": [
            _wref(w) for w in item.get("name_word_ids", []) if w
        ],
        "price_word_id": _wref(item.get("price_word_id")),
        "qty_word_ids": [
            _wref(w) for w in item.get("qty_word_ids", []) if w
        ],
    }


def find_figure_words(
    export_words: list[dict], zone_bottom_y: float, value: Optional[float]
) -> list[list[int]]:
    """Word ref(s) for a printed summary figure, so the viz can point at
    the number the reconciliation compares against.

    Value-matches word text (tax flags and currency stripped) and picks
    the candidate nearest the bottom edge of the items zone — totals
    print just below the items on every layout in the golden set.
    """
    if value is None:
        return []
    candidates = []
    for w in export_words:
        text = re.sub(r"[A-Za-z]+$", "", w["text"].replace("$", "").replace(",", "")).strip()
        try:
            parsed = float(text)
        except ValueError:
            continue
        if abs(parsed - value) >= 0.005:
            continue
        y_mid = w["bbox"]["y"] + w["bbox"]["height"] / 2
        # Prefer candidates strictly below the zone, then out-of-zone:
        # settlement rows inside the zone ("Balance to pay $43.94") echo
        # the total, but the figure the reader should see is the printed
        # one in the summary block below the items.
        candidates.append(
            (
                y_mid >= zone_bottom_y,
                w["in_zone"],
                abs(y_mid - zone_bottom_y),
                [w["line_id"], w["word_id"]],
            )
        )
    if not candidates:
        return []
    return [min(candidates)[3]]


def item_key(item: dict) -> tuple:
    pw = item.get("price_word_id") or {}
    return (
        item.get("price"),
        pw.get("line_id"),
        pw.get("word_id"),
        tuple(sorted(item.get("line_ids", []))),
    )


def check_cdn(key: str) -> bool:
    """True when the key serves a real image on both CDNs.

    Uses curl (system python often lacks CA certs on macOS) and checks
    the content type: CloudFront answers unknown paths with the SPA
    fallback page, so a 200 alone proves nothing.
    """
    for base in (DEV_CDN, PROD_CDN):
        result = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null",
                "-w", "%{http_code} %{content_type}",
                "--max-time", "15",
                f"{base}/{key}",
            ],
            capture_output=True,
            text=True,
        )
        parts = result.stdout.split(None, 1)
        if len(parts) != 2 or parts[0] != "200" or not parts[1].startswith(
            "image/"
        ):
            return False
    return True


def export_receipt(
    fixture_receipt: dict,
    golden: Optional[dict],
    client,
    priors: dict,
) -> Optional[dict]:
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
    from receipt_upload.line_items.geometry import (
        extract_items,
        reconcile_detailed,
    )

    image_id = fixture_receipt["image_id"]
    receipt_id = fixture_receipt["receipt_id"]
    merchant = fixture_receipt["merchant"]
    words = fixture_receipt["words"]
    zone = set(fixture_receipt["items_line_ids"])
    summary = build_summary(golden)

    # ── decode (fixture words: byte-identical to the CI parity gate) ──
    ocr_receipt = {"words": words, "items_line_ids": sorted(zone)}
    bands = annotate_bands(ocr_receipt, priors)
    items_raw, _ = extract_items(words, zone)
    items_filtered, _ = extract_items(words, zone, summary=summary)
    kept_keys = {item_key(i) for i in items_filtered}
    dropped = [i for i in items_raw if item_key(i) not in kept_keys]
    reconcile = reconcile_detailed(
        [i for i in items_filtered if not i.get("is_discount")], summary
    )

    # ── live join: full geometry + CDN keys ──
    try:
        details = client.get_receipt_details(image_id, receipt_id)
    except EntityNotFoundError:
        print(f"  SKIP {merchant}: receipt gone from live table")
        return None
    receipt = details.receipt
    live_words = {(w.line_id, w.word_id): w for w in details.words}

    referenced: set[tuple[int, int]] = set()
    for b in bands:
        referenced.update((l, w) for l, w in b["word_refs"])
    missing = [k for k in referenced if k not in live_words]
    if missing:
        print(f"  SKIP {merchant}: {len(missing)} zone words lost live join")
        return None

    receipt_dict = dict(receipt)
    image = {
        k: receipt_dict[k]
        for k in receipt_dict
        if k.startswith("cdn_") and k.endswith("_key") and receipt_dict[k]
    }
    image["width"] = receipt.width
    image["height"] = receipt.height
    if not check_cdn(image.get("cdn_webp_s3_key") or image["cdn_s3_key"]):
        print(f"  SKIP {merchant}: image missing from a CDN")
        return None

    export_words = [
        {
            "line_id": w.line_id,
            "word_id": w.word_id,
            "text": w.text,
            "bbox": {
                "x": round(w.bounding_box["x"], 5),
                "y": round(w.bounding_box["y"], 5),
                "width": round(w.bounding_box["width"], 5),
                "height": round(w.bounding_box["height"], 5),
            },
            "in_zone": w.line_id in zone,
        }
        for w in details.words
    ]

    # ── absorption/unclaimed detection for PRICE bands ──
    y_lookup = {(w["line_id"], w["word_id"]): w["y_mid"] for w in words}
    item_price_refs = {
        tuple(dump_item(i, y_lookup)["price_word_id"] or ())
        for i in items_raw
    }
    item_by_idx = [dump_item(i, y_lookup) for i in items_filtered]
    for b in bands:
        if b["role"] != "PRICE":
            continue
        band_refs = {tuple(r) for r in b["word_refs"]}
        if any(tuple(r or ()) in band_refs for r in item_price_refs if r):
            b["outcome"] = "item"
            continue
        absorbed_into = None
        outcome = None
        for idx, it in enumerate(item_by_idx):
            qty_refs = {tuple(r) for r in it["qty_word_ids"] if r}
            if band_refs & qty_refs or set(b["line_ids"]) <= set(
                it["line_ids"]
            ):
                absorbed_into = idx
                outcome = "absorbed"
                break
        if absorbed_into is None:
            # Quantity-donor bands ("2 @ $0.49", bare divisor amounts):
            # attach_printed_quantities takes quantity/unit_price from a
            # neighbor band without recording qty_word_ids, so recover
            # the link arithmetically — the donor's amount is the item's
            # unit price and the accepted pair satisfied qty*unit=price.
            # Nearest-in-y wins when several items share a unit price.
            def _explains(amount: float, it: dict) -> bool:
                unit = it["unit_price"]
                if unit is None or it["quantity"] is None:
                    return False
                if abs(amount - unit) < 0.005:
                    return True
                # "2 @ 2 FOR 3.00": the band amount is the FOR-bundle
                # price, an integer multiple of the accepted unit price.
                if unit > 0:
                    ratio = amount / unit
                    return (
                        1 <= round(ratio) <= 12
                        and abs(ratio - round(ratio)) < 0.01
                    )
                return False

            candidates = [
                (abs(b["y"] - (it["y"] or 0)), idx)
                for idx, it in enumerate(item_by_idx)
                if b["amounts"] and _explains(b["amounts"][-1], it)
            ]
            if candidates:
                absorbed_into = min(candidates)[1]
                outcome = "quantity_donor"
        b["outcome"] = outcome or "unclaimed"
        if absorbed_into is not None:
            b["absorbed_into"] = absorbed_into

    zone_bottom_y = min(
        w["bbox"]["y"] for w in export_words if w["in_zone"]
    )
    printed_word_refs = {
        fig: find_figure_words(
            export_words, zone_bottom_y, (summary or {}).get(fig)
        )
        for fig in ("subtotal", "grand_total")
    }

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant": merchant,
        "image": image,
        "words": export_words,
        "items_line_ids": sorted(zone),
        "printed_word_refs": printed_word_refs,
        "bands": bands,
        "items": item_by_idx,
        "dropped_items": [
            {**dump_item(i, y_lookup), "reason": "summary_figure"}
            for i in dropped
        ],
        "summary": summary,
        "reconcile": {
            "status": reconcile.status,
            "item_sum": reconcile.item_sum,
            "baseline": reconcile.baseline,
            "baseline_source": reconcile.baseline_source,
            "baseline_figures_agreeing": (
                reconcile.baseline_figures_agreeing
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default="ReceiptsTable-dc5be22")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="survey all fixture receipts' stage stories; write nothing",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="export every surviving receipt instead of the showcase set",
    )
    args = parser.parse_args()

    from receipt_dynamo import DynamoClient
    from receipt_upload.line_items.blocks import load_default_priors

    fixture = json.loads(OCR_FIXTURE.read_text())
    golden_by_key = {
        (g["image_id"], g["receipt_id"]): g
        for g in json.loads(GOLDEN_FIXTURE.read_text())["receipts"]
    }
    priors = load_default_priors()
    client = DynamoClient(args.table)

    if args.analyze:
        from receipt_upload.line_items.geometry import (
            extract_items,
            reconcile_detailed,
        )

        for r in fixture["receipts"]:
            zone = set(r["items_line_ids"])
            summary = build_summary(
                golden_by_key.get((r["image_id"], r["receipt_id"]))
            )
            bands = annotate_bands(
                {"words": r["words"], "items_line_ids": sorted(zone)},
                priors,
            )
            items_raw, _ = extract_items(r["words"], zone)
            items, _ = extract_items(r["words"], zone, summary=summary)
            rec = reconcile_detailed(
                [i for i in items if not i.get("is_discount")], summary
            )
            guards = [b["guard"] for b in bands if b["guard"]]
            print(
                f"{r['image_id'][:8]} r{r['receipt_id']:<2} "
                f"{r['merchant'][:26]:<26} items={len(items):<2} "
                f"qty={sum(1 for i in items if i.get('quantity')):<2} "
                f"disc={sum(1 for i in items if i.get('is_discount')):<2} "
                f"stacked={sum(1 for i in items if i.get('stacked')):<2} "
                f"dropped={len(items_raw) - len(items):<2} "
                f"priors={sum(1 for b in bands if b['prior']):<2} "
                f"{rec.status:<11} guards={guards}"
            )
        return 0

    if args.all:
        selection = [
            (r["image_id"], r["receipt_id"]) for r in fixture["receipts"]
        ]
    else:
        selection = []
        for prefix, rid, _why in SHOWCASE:
            match = [
                r
                for r in fixture["receipts"]
                if r["image_id"].startswith(prefix)
                and r["receipt_id"] == rid
            ]
            if not match:
                print(f"  showcase entry {prefix} r{rid} not in fixture")
                continue
            selection.append((match[0]["image_id"], rid))

    receipts_out = []
    for image_id, receipt_id in selection:
        r = next(
            r
            for r in fixture["receipts"]
            if r["image_id"] == image_id and r["receipt_id"] == receipt_id
        )
        print(f"{r['merchant']} ({image_id[:8]} r{receipt_id})")
        exported = export_receipt(
            r, golden_by_key.get((image_id, receipt_id)), client, priors
        )
        if exported:
            receipts_out.append(exported)
            print(
                f"  ok: {len(exported['items'])} items, "
                f"{len(exported['bands'])} bands, "
                f"{exported['reconcile']['status']}"
            )

    payload = {
        "source": "line_items_golden_ocr.json + live geometry join",
        "table": args.table,
        "decoder": "line-items-blocks-v2",
        "receipts": receipts_out,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n"
    )
    size_kb = args.out.stat().st_size / 1024
    print(f"\nwrote {args.out} ({size_kb:.0f} KB, {len(receipts_out)} receipts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
