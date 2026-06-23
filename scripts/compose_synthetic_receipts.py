#!/usr/bin/env python3.12
"""Compose whole synthetic receipts from a merchant's learned grammar.

PROTOTYPE. Unlike the edit path (mutate one real receipt), this COMPOSES a new
receipt in order — header -> sampled cloned item rows grouped by category ->
recomputed totals -> payment/footer — reusing the clone + reflow/canvas-fit +
arithmetic primitives from ``merchant_synthesis``. Because it builds in order,
the "item inserted after SUBTOTAL" class of defect is structurally impossible.

Subcommands:
  capacity  --receipt-dir DIR
      Report, per merchant, how many distinct receipts it can plausibly make
      (catalog size / scaffold count / item-count range) and the layout pass
      rate over a sample of compositions.
  generate  --receipt-dir DIR --output FILE [--merchant M] [--count N]
      Write N composed receipts per qualifying merchant (tokens/bboxes/ner_tags
      + evidence) to FILE for downstream evaluation.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
from decimal import Decimal
from pathlib import Path

from receipt_agent.agents.label_evaluator import merchant_synthesis as ms

from verify_synthetic_replay import (  # type: ignore  # noqa: E402
    load_local_receipt_groups,
)


def _words(lines):
    return [w for ln in lines for w in ln.get("words", []) if w.get("bbox")]


def _shift_lines(lines, dy):
    for ln in lines:
        for w in ln.get("words", []):
            if w.get("bbox"):
                w["bbox"][1] += dy
                w["bbox"][3] += dy


def _set_totals(receipt, subtotal, grand_total, old_grand_total):
    for ln in receipt.get("lines", []):
        for w in ln.get("words", []):
            labels = set(w.get("labels") or [])
            val = ms._parse_money(w.get("text"))
            if val is None:
                continue
            if "SUBTOTAL" in labels:
                w["text"] = ms._format_money_like(w["text"], subtotal)
                ms._right_align_money_box(w)
            elif "GRAND_TOTAL" in labels:
                w["text"] = ms._format_money_like(w["text"], grand_total)
                ms._right_align_money_box(w)
            elif (
                old_grand_total is not None
                and val == old_grand_total
                and not labels & {"LINE_TOTAL", "TAX", "SUBTOTAL", "GRAND_TOTAL"}
            ):
                w["text"] = ms._format_money_like(w["text"], grand_total)
                ms._right_align_money_box(w)


def compose(scaffold, catalog, *, item_count, rng):
    """Compose one receipt from a scaffold's header/totals/footer + fresh items."""
    receipt = copy.deepcopy(scaffold.receipt)
    lines = receipt["lines"]
    items = scaffold.line_items
    if not items:
        return None
    band_idx = sorted({i for it in items for i in it.band_line_indices})
    first_i, last_i = min(band_idx), max(band_idx)
    header, summary = lines[:first_i], lines[last_i + 1 :]

    non_taxable = [e for e in catalog if not e.taxable and e.source_rows]
    if len(non_taxable) < 2:
        return None
    k = min(item_count, len(non_taxable))
    chosen = rng.sample(non_taxable, k)
    seq = scaffold.category_sequence or []
    chosen.sort(key=lambda e: (seq.index(e.category) if e.category in seq else 99,))

    pitch = ms._line_step(items)
    gap = max(6, pitch // 3)
    cursor = min((w["bbox"][1] for w in _words(header)), default=900) - pitch
    composed = []
    last_cat = None
    for e in chosen:
        if e.category != last_cat and e.category != ms.UNKNOWN_CATEGORY:
            heading = ms._build_line(
                e.category.split(), [], x0=60, y0=max(0, int(cursor) - 12)
            )
            composed.append(heading)
            cursor = min(w["bbox"][1] for w in heading["words"] if w.get("bbox")) - gap
            last_cat = e.category
        captured = next(iter(e.source_rows.values()))
        band = ms._clone_row_group_lines(captured, y_center=max(12, int(cursor)))
        composed.extend(band)
        cursor = min((w["bbox"][1] for w in _words(band)), default=int(cursor)) - gap
    for offset, ln in enumerate(composed):
        ln["line_id"] = 20_000 + offset
        for w in ln.get("words", []):
            w["line_id"] = 20_000 + offset

    sf_top = max((w["bbox"][3] for w in _words(summary)), default=int(cursor))
    _shift_lines(summary, int(cursor) - sf_top)

    receipt["lines"] = header + composed + summary
    new_subtotal = ms._money_sum(e.amount for e in chosen)
    tax = scaffold.tax_total or Decimal("0.00")
    new_total = ms._money(new_subtotal + tax)
    _set_totals(receipt, new_subtotal, new_total, scaffold.grand_total)
    ms._reconcile_item_count(receipt, delta_count=len(chosen) - len(items))
    ms._fit_receipt_to_canvas(receipt)
    ms._refresh_words(receipt)

    tokens, bboxes, tags = ms._flatten_lines(receipt["lines"])
    return {
        "receipt": receipt,
        "tokens": tokens,
        "bboxes": bboxes,
        "ner_tags": tags,
        "item_count": k,
        "subtotal": str(new_subtotal),
        "grand_total": str(new_total),
        "items": [e.product_text for e in chosen],
        "layout_integrity": ms.build_layout_integrity_evidence(receipt),
    }


def _merchant_models(groups):
    out = {}
    for g in groups:
        analyses = [
            ms._analyze_receipt(r)
            for r in (ms._normalize_receipt(x) for x in g["receipts"])
            if r.get("words")
        ]
        analyses = [a for a in analyses if a.line_items and a.grand_total]
        if not analyses:
            continue
        catalog = ms._build_item_catalog(analyses)
        out[g["merchant_name"]] = (analyses, catalog)
    return out


def cmd_capacity(args):
    groups = load_local_receipt_groups(receipt_dirs=[args.receipt_dir])
    rng = random.Random(7)
    rows = []
    for merchant, (analyses, catalog) in _merchant_models(groups).items():
        nt = [e for e in catalog if not e.taxable and e.source_rows]
        counts = [len(a.line_items) for a in analyses]
        clean = 0
        sample = max(args.sample, 1)
        for _ in range(sample):
            c = compose(rng.choice(analyses), catalog, item_count=rng.choice(counts), rng=rng)
            if c and c["layout_integrity"]["score"] >= 1.0:
                clean += 1
        rows.append({
            "merchant": merchant,
            "scaffolds": len(analyses),
            "non_taxable_catalog": len(nt),
            "item_count_range": [min(counts), max(counts)] if counts else [0, 0],
            "layout_pass_rate": round(clean / sample, 2),
        })
    rows.sort(key=lambda r: (r["layout_pass_rate"], r["non_taxable_catalog"]), reverse=True)
    print(json.dumps(rows, indent=2))


def cmd_generate(args):
    groups = load_local_receipt_groups(receipt_dirs=[args.receipt_dir])
    rng = random.Random(13)
    out = []
    for merchant, (analyses, catalog) in _merchant_models(groups).items():
        if args.merchant and merchant != args.merchant:
            continue
        counts = [len(a.line_items) for a in analyses]
        made = 0
        attempts = 0
        while made < args.count and attempts < args.count * 6:
            attempts += 1
            c = compose(rng.choice(analyses), catalog, item_count=rng.choice(counts), rng=rng)
            if not c or c["layout_integrity"]["score"] < 1.0:
                continue
            out.append({
                "merchant": merchant,
                "item_count": c["item_count"],
                "subtotal": c["subtotal"],
                "grand_total": c["grand_total"],
                "items": c["items"],
                "tokens": c["tokens"],
                "bboxes": c["bboxes"],
                "ner_tags": c["ner_tags"],
            })
            made += 1
        print(f"{merchant}: composed {made} clean receipts")
    Path(args.output).write_text(json.dumps(out))
    print(f"wrote {len(out)} composed receipts -> {args.output}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capacity")
    cap.add_argument("--receipt-dir", required=True)
    cap.add_argument("--sample", type=int, default=20)
    gen = sub.add_parser("generate")
    gen.add_argument("--receipt-dir", required=True)
    gen.add_argument("--output", required=True)
    gen.add_argument("--merchant")
    gen.add_argument("--count", type=int, default=20)
    args = ap.parse_args()
    {"capacity": cmd_capacity, "generate": cmd_generate}[args.command](args)


if __name__ == "__main__":
    main()
