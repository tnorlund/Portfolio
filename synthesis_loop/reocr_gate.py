#!/usr/bin/env python3
"""reocr_gate.py — deterministic quarantine gate for the re-OCR pathway (review action #2).

Two gates, both automatable (no human/opus in the loop):
  PRE-OCR  (on the synthesized GT, before rendering even matters):
    - arithmetic_reconciles: SUBTOTAL + TAX ~= GRAND_TOTAL (when all present & parseable)
    - no_empty_value_fields: every value entity (LINE_TOTAL/SUBTOTAL/TAX/GRAND_TOTAL) has a non-empty token
  POST-OCR (after Apple Vision, before label propagation):
    - region_coverage: every REQUIRED GT entity region has nearby raw OCR coverage; a field OCR can't read
      is an unrecoverable label gap, so QUARANTINE the receipt instead of propagating a silent miss.

This replaces opus image-review as the recall gate (it scales). Usage:
  reocr_gate.py <bundle.json> <hybrid.png> [candidate_index]
"""
from __future__ import annotations
import json, re, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import re_ocr_align as RA  # reuse coord transforms + apple_vision_ocr  # noqa: E402

REQUIRED = {"MERCHANT_NAME", "SUBTOTAL", "TAX", "GRAND_TOTAL", "DATE", "LINE_TOTAL"}
VALUE_ENTS = {"LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"}
_CUR = re.compile(r"-?\$?\s*([0-9]+(?:\.[0-9]{2})?)")


def _money(tok):
    m = _CUR.search(tok.replace(",", "")) if tok else None
    return float(m.group(1)) * (-1 if "-" in tok else 1) if m else None


def _ent(t):
    return t[2:] if t and t[:2] in ("B-", "I-") else (None if t in (None, "O", "") else t)


def _inter_over(a, b):
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    return inter / max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))


def gate(bundle_p, hybrid_png, ci=0):
    ex = json.load(open(bundle_p))["synthetic_training_examples"][ci]
    toks, bbs, tags = ex["tokens"], ex["bboxes"], ex["ner_tags"]
    ents = [_ent(t) for t in tags]
    reasons = []

    # ---- PRE-OCR ----
    by_ent = {}
    for tok, e in zip(toks, ents):
        if e:
            by_ent.setdefault(e, []).append(tok)
    # empty value fields
    empties = [e for e in VALUE_ENTS if e in by_ent and not any(_money(t) is not None for t in by_ent[e])]
    if empties:
        reasons.append(f"empty/garbled value fields: {empties}")
    # arithmetic
    sub = next((_money(t) for t in by_ent.get("SUBTOTAL", []) if _money(t) is not None), None)
    tax = next((_money(t) for t in by_ent.get("TAX", []) if _money(t) is not None), None)
    tot = next((_money(t) for t in by_ent.get("GRAND_TOTAL", []) if _money(t) is not None), None)
    arith = None
    if sub is not None and tot is not None:
        tax = tax or 0.0
        arith = abs((sub + tax) - tot) <= 0.02
        if not arith:
            reasons.append(f"totals do not reconcile: subtotal {sub} + tax {tax} != grand_total {tot}")

    # ---- POST-OCR coverage ----
    res = list(RA.apple_vision_ocr([hybrid_png]).values())[0]
    ocr_px = [RA.ocr_to_px(w.bounding_box) for w in res[1]]
    coverage = {}
    for tok, e, bb in zip(toks, ents, bbs):
        if not e:
            continue
        gpx = RA.gt_to_px(bb)
        covered = any(_inter_over(o, gpx) >= 0.20 for o in ocr_px)
        coverage.setdefault(e, True)
        coverage[e] = coverage[e] and (covered or coverage[e] is None) if e in coverage else covered
        if not covered:
            coverage[e] = False
    uncovered_required = sorted(e for e in REQUIRED if e in coverage and coverage[e] is False)
    if uncovered_required:
        reasons.append(f"REQUIRED fields with no OCR coverage (unrecoverable): {uncovered_required}")

    passed = len(reasons) == 0
    return {
        "pass": passed,
        "candidate_id": ex.get("candidate_id"),
        "pre_ocr": {"arithmetic_reconciles": arith, "empty_value_fields": empties},
        "coverage": {e: coverage[e] for e in sorted(coverage)},
        "uncovered_required": uncovered_required,
        "quarantine_reasons": reasons,
    }


def main():
    out = gate(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
    print(json.dumps(out, indent=2))
    return 0 if out["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
