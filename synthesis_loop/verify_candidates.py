#!/usr/bin/env python3
"""Objective, reproducible verification of synthetic receipt candidates.

NOT a subjective "looks real" judge — these are programmatic checks on the structured
data (tokens / bboxes / ner_tags) that anyone can re-run and that directly map to either
training-data quality or a visible fake-tell. "Better" = the pass rate goes up.

Checks per candidate:
  bbox_valid     every box has x0<x1, y0<y1, in [0,1000], non-degenerate (poisons LayoutLM geometry)
  no_overlap     no two words on a line overlap in x (the footer "squish" tell)
  single_header  exactly one MERCHANT_NAME header block (catches duplicated receipts)
  no_garble      no OCR junk: currency-with-letters ($2b0), Contactless misspellings, etc.
  arithmetic     labeled line totals (+ up to ~12% tax) reconcile to the grand total

Usage:
  verify_candidates.py --candidates-dir DIR        # dir of <id>.json (cached examples)
  verify_candidates.py --bundle bundle.json        # bundle.json -> synthetic_training_examples[]
  verify_candidates.py --bundle b.json --md OUT.md  # also write a markdown scorecard
"""
from __future__ import annotations
import argparse, glob, json, os, re, statistics
from collections import defaultdict

CURRENCY = re.compile(r"^[-+]?\$?\d{1,4}(?:,\d{3})*\.\d{2}[-+]?$")
GARBLE_CURRENCY = re.compile(r"\$\d*[a-zA-Z]\d*")          # $2b0
GARBLE_WORDS = re.compile(r"\b(c?ntctless|ontctless|cntotless)\b", re.I)


def _label(tag: str) -> str:
    return (tag or "O").split("-", 1)[-1] if tag and tag != "O" else "O"


def _val(tok: str):
    m = re.search(r"-?\$?\d{1,4}(?:,\d{3})*\.\d{2}", tok or "")
    return float(m.group(0).replace("$", "").replace(",", "")) if m else None


# --- label-correctness vocab/patterns (find #4: mislabeled entities poison LayoutLM) ---
_TENDER = {"DEBIT", "CREDIT", "VISA", "MASTERCARD", "MC", "AMEX", "DISCOVER", "CASH",
           "US", "CHECK", "EBT", "CONTACTLESS", "CHIP", "SWIPE", "CARD", "TENDER", "PIN", "ATM"}
_PHONE = re.compile(r"\(\d{3}\)\s?\d{3}[- ]?\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b|\b\d{3}[-.]\d{4}\b")
_DOMAIN = re.compile(r"[A-Za-z0-9-]+\.(?:com|net|org|co|us|gov)\b", re.I)
_CURRENCYISH = re.compile(r"-?\$?\d+\.\d{2}-?$")


_MONEY = {"LINE_TOTAL", "GRAND_TOTAL", "SUBTOTAL", "TAX", "UNIT_PRICE", "BALANCE_DUE"}


def _entity_runs(tags, n):
    """Contiguous BIO spans -> (label, start, end). Validate entities, not tokens."""
    runs, i = [], 0
    while i < n:
        lab = _label(tags[i])
        if lab == "O":
            i += 1; continue
        j = i
        while j + 1 < n and _label(tags[j + 1]) == lab and not str(tags[j + 1]).startswith("B-"):
            j += 1
        runs.append((lab, i, j)); i = j + 1
    return runs


def _label_violations(toks, tags, n):
    """ENTITY runs whose joined content can't match their label (clear mislabels only)."""
    viol = []
    for lab, i, j in _entity_runs(tags, n):
        text = " ".join((toks[k] or "") for k in range(i, j + 1)).strip()
        joined = text.replace(" ", "")
        if not text:
            continue
        if lab in _MONEY:
            if not _CURRENCYISH.match(joined.replace(",", "")):       # money field that isn't currency
                viol.append((lab, text))
        elif lab == "PAYMENT_METHOD":
            up = text.upper()
            if not (any(w in up for w in _TENDER) or re.search(r"[X*]{3,}\w", joined)):  # tender word or masked card
                viol.append((lab, text))
        elif lab == "PHONE_NUMBER":
            if not _PHONE.search(text) and not _PHONE.search(joined):  # e.g. EMV "0000000000"
                viol.append((lab, text))
        elif lab == "WEBSITE":
            if not _DOMAIN.search(joined) and not re.search(r"[A-Za-z]{3,}", text):  # e.g. "******"
                viol.append((lab, text))
    return viol


def _lines(boxes):
    """Group word indices into visual lines by y-center proximity."""
    centers = [((b[1] + b[3]) / 2, i) for i, b in enumerate(boxes) if _box_ok(b)]
    centers.sort(reverse=True)  # high y = top
    lines, cur, last = [], [], None
    for cy, i in centers:
        if last is None or abs(cy - last) <= 8:
            cur.append(i)
        else:
            lines.append(cur); cur = [i]
        last = cy
    if cur:
        lines.append(cur)
    return lines


def _box_ok(b):
    return isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)


def check(cand: dict) -> dict:
    toks = cand.get("tokens") or []
    boxes = cand.get("bboxes") or []
    tags = cand.get("ner_tags") or []
    n = min(len(toks), len(boxes), len(tags))
    res = {}

    # bbox_valid
    bad = []
    for i in range(len(boxes)):
        b = boxes[i]
        if not _box_ok(b):
            bad.append(i); continue
        x0, y0, x1, y1 = b
        if not (x0 < x1 and y0 < y1 and 0 <= x0 and x1 <= 1000 and 0 <= y0 and y1 <= 1000):
            bad.append(i)
    res["bbox_valid"] = {"pass": not bad, "bad": len(bad)}

    # no_overlap (footer squish): adjacent words on a line overlapping in x
    overlaps = 0
    for line in _lines(boxes):
        ordered = sorted(line, key=lambda i: boxes[i][0])
        for a, c in zip(ordered, ordered[1:]):
            if boxes[c][0] < boxes[a][2] - 2:  # next starts left of prev's right edge
                overlaps += 1
    res["no_overlap"] = {"pass": overlaps == 0, "overlap_pairs": overlaps}

    # word_spacing: adjacent words on a line need a REAL gap, not just non-overlap.
    # "Not overlapping" != "properly spaced" — the footer squish has words TOUCHING
    # (gap ~0). Flag pairs whose gap is below a word-space (~a third of line height).
    jammed = 0
    for line in _lines(boxes):
        ordered = sorted(line, key=lambda i: boxes[i][0])
        for p, q in zip(ordered, ordered[1:]):
            gap = boxes[q][0] - boxes[p][2]
            h = max(boxes[p][3] - boxes[p][1], 6)
            if 0 <= gap < 0.30 * h:      # touching: smaller than a normal word-space
                jammed += 1
    res["word_spacing"] = {"pass": jammed == 0, "jammed_pairs": jammed}

    # single_header: one MERCHANT_NAME block
    merch_blocks = sum(1 for i in range(n) if tags[i] == "B-MERCHANT_NAME")
    res["single_header"] = {"pass": merch_blocks == 1, "merchant_blocks": merch_blocks}

    # single_payment_block: exactly one card/payment section (catches the injected dup block)
    card_tails = set()
    for t in toks:
        if re.search(r"[X*xX]{4,}", t or ""):     # masked card number, e.g. XXXXXXXX8712
            card_tails.add((t or "")[-4:])
    res["single_payment_block"] = {"pass": len(card_tails) <= 1, "cards": sorted(card_tails)}

    # no_garble
    garble = [toks[i] for i in range(len(toks))
              if GARBLE_CURRENCY.search(toks[i] or "") or GARBLE_WORDS.search(toks[i] or "")]
    res["no_garble"] = {"pass": not garble, "garbled": garble[:6]}

    # arithmetic: sum(LINE_TOTAL) (+<=12% tax) ~ GRAND_TOTAL
    line_totals = [v for i in range(n) if _label(tags[i]) == "LINE_TOTAL" and (v := _val(toks[i])) is not None]
    grands = [v for i in range(n) if _label(tags[i]) == "GRAND_TOTAL" and (v := _val(toks[i])) is not None]
    if line_totals and grands:
        s = sum(line_totals); g = max(grands)
        ok = s - 0.02 <= g <= s * 1.12 + 0.02
        res["arithmetic"] = {"pass": ok, "sum_items": round(s, 2), "grand_total": round(g, 2)}
    else:
        res["arithmetic"] = {"pass": None, "note": "no labeled totals"}

    # labels_valid: entity labels whose token content can't be that entity (training poison)
    viol = _label_violations(toks, tags, n)
    res["labels_valid"] = {"pass": not viol, "violations": len(viol),
                           "examples": [f"{l}:{t}" for l, t in viol[:6]]}

    checks = [v["pass"] for v in res.values() if v["pass"] is not None]
    res["_score"] = round(sum(checks) / len(checks), 3) if checks else None
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates-dir")
    ap.add_argument("--bundle")
    ap.add_argument("--md")
    a = ap.parse_args()

    cands = []
    if a.candidates_dir:
        for p in sorted(glob.glob(os.path.join(a.candidates_dir, "*.json"))):
            try:
                cands.append(json.load(open(p)))
            except Exception:
                pass
    if a.bundle:
        b = json.load(open(a.bundle))
        cands += b.get("synthetic_training_examples", []) or []

    rows = []
    agg = defaultdict(list)
    for c in cands:
        r = check(c)
        cid = c.get("candidate_id", "?")
        rows.append((cid, r))
        for k, v in r.items():
            if k != "_score" and v.get("pass") is not None:
                agg[k].append(1 if v["pass"] else 0)
        if r["_score"] is not None:
            agg["_score"].append(r["_score"])

    print(f"== verified {len(rows)} candidates ==")
    for k in ["bbox_valid", "no_overlap", "word_spacing", "single_header", "single_payment_block", "no_garble", "arithmetic", "labels_valid"]:
        if agg[k]:
            print(f"  {k:20} {sum(agg[k])}/{len(agg[k])} pass")
    if agg["_score"]:
        print(f"  {'OVERALL':14} mean check pass-rate {statistics.mean(agg['_score']):.3f}")

    out = {"n": len(rows),
           "aggregate": {k: f"{sum(v)}/{len(v)}" for k, v in agg.items() if k != "_score" and v},
           "mean_score": round(statistics.mean(agg["_score"]), 3) if agg["_score"] else None,
           "candidates": [{"candidate_id": cid, **r} for cid, r in rows]}
    print(json.dumps(out["aggregate"]))

    if a.md:
        lines = ["# Synthetic candidate verification (objective, reproducible)\n",
                 f"Verified **{len(rows)}** candidates. Mean check pass-rate: **{out['mean_score']}**\n",
                 "| candidate | score | bbox | overlap | spacing | header | 1pay | garble | arith | labels |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        def cell(v):
            if v.get("pass") is None: return "—"
            return "✅" if v["pass"] else "❌"
        for cid, r in rows:
            lines.append(f"| `{cid[:40]}` | {r['_score']} | {cell(r['bbox_valid'])} | "
                         f"{cell(r['no_overlap'])} | {cell(r['word_spacing'])} | {cell(r['single_header'])} | "
                         f"{cell(r['single_payment_block'])} | {cell(r['no_garble'])} | {cell(r['arithmetic'])} | "
                         f"{cell(r['labels_valid'])} |")
        lines += ["", "## Aggregate", "```", json.dumps(out["aggregate"], indent=2), "```"]
        open(a.md, "w").write("\n".join(lines) + "\n")
        print("wrote", a.md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
