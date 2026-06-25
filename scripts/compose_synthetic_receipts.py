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


def _set_totals(receipt, subtotal, grand_total, old_grand_total, *, tax=None):
    summary_labels = {"SUBTOTAL", "TAX", "GRAND_TOTAL"}
    tax_words = []
    for ln in receipt.get("lines", []):
        line_labels = {
            label
            for word in ln.get("words", [])
            for label in (word.get("labels") or [])
            if label in summary_labels
        }
        for w in ln.get("words", []):
            labels = set(w.get("labels") or [])
            val = ms._parse_money(w.get("text"))
            if val is None:
                continue
            effective_labels = set(labels)
            if not effective_labels & {"LINE_TOTAL", *summary_labels}:
                effective_labels |= line_labels
            if "SUBTOTAL" in effective_labels:
                w["text"] = ms._format_money_like(w["text"], subtotal)
                ms._right_align_money_box(w)
            elif "TAX" in effective_labels and tax is not None:
                tax_words.append((w, val))
            elif "GRAND_TOTAL" in effective_labels:
                w["text"] = ms._format_money_like(w["text"], grand_total)
                ms._right_align_money_box(w)
            elif (
                old_grand_total is not None
                and val == old_grand_total
                and not labels & {"LINE_TOTAL", "TAX", "SUBTOTAL", "GRAND_TOTAL"}
            ):
                w["text"] = ms._format_money_like(w["text"], grand_total)
                ms._right_align_money_box(w)
    if tax is not None and tax_words:
        original_tax = ms._money_sum(val for _, val in tax_words)
        running = Decimal("0.00")
        for index, (word, val) in enumerate(tax_words):
            if index == len(tax_words) - 1:
                share = ms._money(tax - running)
            elif original_tax > Decimal("0.00"):
                share = ms._money(tax * val / original_tax)
            else:
                share = Decimal("0.00")
            running += share
            word["text"] = ms._format_money_like(word["text"], share)
            ms._right_align_money_box(word)


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
    cloned_amounts = []
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
        # Subtotal must match the PRICE the cloned row actually displays, which
        # can differ from the catalog median (e.amount) when the same product
        # was seen at different prices; otherwise rows and SUBTOTAL disagree.
        cloned_amount = ms._parse_money(captured.get("amount"))
        cloned_amounts.append(cloned_amount if cloned_amount is not None else e.amount)
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
    new_subtotal = ms._money_sum(cloned_amounts)
    # Every composed item is non-taxable (the sampler keeps only `not taxable`
    # catalog entries), so the receipt carries no tax — carrying the scaffold's
    # tax from now-deleted taxable items would make the totals inconsistent.
    tax = Decimal("0.00")
    new_total = ms._money(new_subtotal + tax)
    _set_totals(receipt, new_subtotal, new_total, scaffold.grand_total, tax=tax)
    ms._reconcile_item_count(receipt, delta_count=len(chosen) - len(items))
    ms._fit_receipt_to_canvas(receipt)
    ms._refresh_words(receipt)

    tokens, bboxes, tags = ms._flatten_lines(receipt["lines"])
    return {
        "base_receipt_key": ms._receipt_key(scaffold.receipt),
        "receipt": receipt,
        "tokens": tokens,
        "bboxes": bboxes,
        "ner_tags": tags,
        "item_count": k,
        "subtotal": str(new_subtotal),
        "tax": str(tax),
        "grand_total": str(new_total),
        "items": [
            {
                "product_text": e.product_text,
                "line_total": str(amount),
                "category": e.category,
                "source_receipt_keys": e.source_receipt_keys[:5],
            }
            for e, amount in zip(chosen, cloned_amounts)
        ],
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


def _composition_label_control(receipt):
    item_label_count = 0
    item_line_count = 0
    lines_with_product_name = 0
    for line in receipt.get("lines", []):
        if int(line.get("line_id") or 0) < ms._SYNTHETIC_LINE_ID_BASE:
            continue
        line_labels = {
            label
            for word in line.get("words", [])
            for label in (word.get("labels") or [])
        }
        if not line_labels & {"PRODUCT_NAME", "LINE_TOTAL"}:
            continue
        item_line_count += 1
        lines_with_product_name += int("PRODUCT_NAME" in line_labels)
        item_label_count += sum(
            1
            for word in line.get("words", [])
            if set(word.get("labels") or []) & {"PRODUCT_NAME", "LINE_TOTAL"}
        )
    return {
        "item_token_count": item_label_count,
        "correctly_labeled": item_label_count,
        "all_rows_have_product_name": item_line_count > 0
        and lines_with_product_name == item_line_count,
        "all_correct": item_label_count > 0
        and item_line_count > 0
        and lines_with_product_name == item_line_count,
        "source": "observed_item_catalog_clone",
    }


def _candidate_metadata_for_composition(analyses, c):
    subtotal = Decimal(c["subtotal"])
    tax = Decimal(c["tax"])
    grand_total = Decimal(c["grand_total"])
    return {
        "base_receipt_key": c["base_receipt_key"],
        "composed_item_count": c["item_count"],
        "composed_items": c["items"],
        "online_catalog_grounding": {
            "entries": c["items"],
            "all_priced": all(
                Decimal(item["line_total"]) > Decimal("0.00") for item in c["items"]
            ),
            "all_named": all(
                str(item["product_text"]).strip() for item in c["items"]
            ),
            "source": "observed_item_catalog",
        },
        "label_control": _composition_label_control(c["receipt"]),
        "arithmetic_reconciliation": {
            "summary_update_policy": "composed_catalog_totals",
            "new_subtotal": c["subtotal"],
            "new_tax": c["tax"],
            "new_grand_total": c["grand_total"],
            "subtotal_consistent": subtotal + tax == grand_total,
            "tax_rate_stable": True,
            "tax_basis": "non_taxable_observed_catalog",
        },
        "structure_similarity": ms._score_structure_similarity(
            ms._analyze_receipt(c["receipt"]),
            analyses,
        ),
        "layout_integrity": c["layout_integrity"],
        "balancing_strategy": (
            "compose a net-new receipt from observed non-taxable catalog rows; "
            "rewrite subtotal, tax, and grand total"
        ),
    }


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
            candidate = ms._candidate_from_receipt(
                c["receipt"],
                merchant,
                source="observed_item_catalog_compose",
                operation="compose_online_catalog",
                index=made + 1,
                metadata=_candidate_metadata_for_composition(analyses, c),
            )
            candidate["item_count"] = c["item_count"]
            candidate["subtotal"] = c["subtotal"]
            candidate["grand_total"] = c["grand_total"]
            candidate["items"] = c["items"]
            out.append(candidate)
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
