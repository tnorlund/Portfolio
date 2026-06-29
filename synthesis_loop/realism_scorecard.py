#!/usr/bin/env python3
"""FAST deterministic realism scorecard for the synthesis hill-climb ratchet.

NO rendering, NO OCR. Pure token/bbox-level metrics over the structured candidates
so the loop can run this every round in seconds. All metrics are reproducible and
map to a concrete fake-tell or a training-data quality signal.

Metrics (per-merchant + aggregate):
  verifier_score           mean of verify_candidates.check(cand)["_score"]   (higher=better)
  garbled_token_rate       fraction of tokens matching OCR-noise regex set    (lower=better)
  price_decimal_x_stddev   per-receipt stddev of price-token right edges,
                           mean across receipts (column alignment)            (lower=better)
  line_word_gap_p90        p90 inter-word gap / median glyph width            (lower=better)
  item_count_consistency   frac of candidates where printed item count
                           == number of LINE_TOTAL rows                       (higher=better)
  vertical_overlap_rate    frac of candidates with any same-column token
                           pair whose bboxes vertically overlap (collision)   (lower=better)

composite: a weighted blend in [0,1] (higher=better). See COMPOSITE_WEIGHTS below; the
"lower=better" metrics are mapped to a [0,1] goodness first (see _goodness()).

Usage:
  realism_scorecard.py <matrix_dir> [out.json]
    scans <matrix_dir>/*/bundle.json (each -> synthetic_training_examples[])
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys
from collections import defaultdict

# Reuse the objective verifier that already lives next to us.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_candidates as vc  # noqa: E402


# --------------------------------------------------------------------------- #
# garbled-token regex set (curated). A token is "garbled" if ANY of these hit. #
# --------------------------------------------------------------------------- #
_GARBLE_PATTERNS = [
    re.compile(r"::"),                          # double-colon OCR noise
    re.compile(r"[!|]:"),                       # !: or |:
    re.compile(r":[!|]"),                       # :! or :|
    re.compile(r"[A-Za-z][!|][A-Za-z]"),        # letter | / ! letter  (mid-word pipe/bang)
    re.compile(r"[A-Za-z0-9][!|]$"),            # trailing bang/pipe glued to a word
    re.compile(r"[:;!?]{2,}"),                  # doubled punctuation
    re.compile(r",{2,}"),                       # doubled comma
    vc.GARBLE_CURRENCY,                         # $2b0  (currency with letters)
    vc.GARBLE_WORDS,                            # cntctless / ontctless ...
    # "amasen"-style: amazon/known-merchant OCR mangles -> curated literals
    re.compile(r"\b(amasen|amaz0n|wlamart|walrnart|cosrco|targ3t|kr0ger)\b", re.I),
]


def _is_garbled(tok: str) -> bool:
    if not tok:
        return False
    return any(p.search(tok) for p in _GARBLE_PATTERNS)


def _label(tag: str) -> str:
    return vc._label(tag)


def _box_ok(b) -> bool:
    return vc._box_ok(b)


_PRICE_LABELS = {"LINE_TOTAL", "UNIT_PRICE"}

# printed item-count phrases -> capture the count
_ITEM_COUNT_RE = re.compile(
    r"(?:NUMBER\s+OF\s+ITEMS\s+SOLD|ITEMS?\s+SOLD|ITEM\s+COUNT|"
    r"#\s*OF\s*ITEMS|TOTAL\s+(?:NUMBER\s+OF\s+)?ITEMS)\D{0,8}(\d{1,3})\b",
    re.I,
)


# --------------------------------------------------------------------------- #
# per-candidate metric helpers                                                 #
# --------------------------------------------------------------------------- #
def _price_x_stddev(toks, boxes, tags, n):
    """Column-alignment tightness of price tokens, in coord units (lower=tighter).

    UNIT_PRICE and LINE_TOTAL live in DIFFERENT columns, so we group by label,
    take the stddev of right edges (x1) WITHIN each column (>=2 tokens), and
    average those per-column stddevs. Pooling columns would falsely inflate it.
    None if no column has >=2 price tokens."""
    by_label = defaultdict(list)
    for i in range(n):
        lab = _label(tags[i])
        if lab in _PRICE_LABELS and _box_ok(boxes[i]):
            by_label[lab].append(boxes[i][2])
    sds = [statistics.pstdev(xs) for xs in by_label.values() if len(xs) >= 2]
    if not sds:
        return None
    return statistics.mean(sds)


def _glyph_width(toks, boxes, n):
    widths = []
    for i in range(n):
        b = boxes[i]
        t = toks[i] or ""
        if _box_ok(b) and len(t) >= 1:
            w = (b[2] - b[0]) / len(t)
            if w > 0:
                widths.append(w)
    return statistics.median(widths) if widths else None


def _percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _line_word_gap_p90(toks, boxes, n):
    """p90 of inter-word gaps (pooled across visual lines), normalized by
    median glyph width. None if no multi-word line or no glyph scale."""
    gw = _glyph_width(toks, boxes, n)
    if not gw:
        return None
    gaps = []
    for line in vc._lines(boxes[:n]):
        ordered = sorted([i for i in line if i < n], key=lambda i: boxes[i][0])
        for p, q in zip(ordered, ordered[1:]):
            gap = boxes[q][0] - boxes[p][2]
            if gap >= 0:                      # ignore overlaps (separate tell)
                gaps.append(gap)
    if not gaps:
        return None
    return _percentile(gaps, 0.90) / gw


def _item_count_consistency(toks, tags, n):
    """(matches, has_printed_count) for this candidate.
    matches=True iff a printed item-count line equals #LINE_TOTAL rows."""
    text = " ".join((toks[i] or "") for i in range(n))
    m = _ITEM_COUNT_RE.search(text)
    if not m:
        return (None, False)
    printed = int(m.group(1))
    n_items = sum(1 for i in range(n) if _label(tags[i]) == "LINE_TOTAL")
    return (printed == n_items, True)


def _vertical_overlap(boxes, n, x_overlap_frac=0.5, y_pad=2):
    """True if any two DIFFERENT-line tokens share a column (x-overlap) AND
    overlap vertically (the totals-block collision tell)."""
    idx = [i for i in range(n) if _box_ok(boxes[i])]
    for a_pos in range(len(idx)):
        a = idx[a_pos]
        ax0, ay0, ax1, ay1 = boxes[a]
        aw = ax1 - ax0
        for b_pos in range(a_pos + 1, len(idx)):
            b = idx[b_pos]
            bx0, by0, bx1, by1 = boxes[b]
            # column overlap
            ox = min(ax1, bx1) - max(ax0, bx0)
            if ox <= 0:
                continue
            minw = max(min(aw, bx1 - bx0), 1)
            if ox / minw < x_overlap_frac:
                continue
            # vertical overlap (true collision, not just same line)
            oy = min(ay1, by1) - max(ay0, by0)
            if oy > y_pad:
                return True
    return False


# --------------------------------------------------------------------------- #
# scoring a whole merchant bundle                                              #
# --------------------------------------------------------------------------- #
def score_candidates(cands):
    """Return aggregated metric dict for a list of candidates."""
    verifier_scores = []
    garbled_tokens = 0
    total_tokens = 0
    price_stddevs = []          # one per receipt (with >=2 price tokens)
    gap_p90s = []               # one per candidate
    ic_matches = 0
    ic_has = 0
    vo_hits = 0
    vo_total = 0

    for c in cands:
        toks = c.get("tokens") or []
        boxes = c.get("bboxes") or []
        tags = c.get("ner_tags") or []
        n = min(len(toks), len(boxes), len(tags))

        # verifier
        r = vc.check(c)
        if r.get("_score") is not None:
            verifier_scores.append(r["_score"])

        # garbled tokens
        for t in toks:
            total_tokens += 1
            if _is_garbled(t):
                garbled_tokens += 1

        # price column tightness
        s = _price_x_stddev(toks, boxes, tags, n)
        if s is not None:
            price_stddevs.append(s)

        # word-gap p90
        g = _line_word_gap_p90(toks, boxes, n)
        if g is not None:
            gap_p90s.append(g)

        # item-count consistency
        match, has = _item_count_consistency(toks, tags, n)
        if has:
            ic_has += 1
            if match:
                ic_matches += 1

        # vertical overlap
        vo_total += 1
        if _vertical_overlap(boxes, n):
            vo_hits += 1

    def _mean(xs):
        return round(statistics.mean(xs), 4) if xs else None

    metrics = {
        "n_candidates": len(cands),
        "verifier_score": _mean(verifier_scores),
        "garbled_token_rate": round(garbled_tokens / total_tokens, 4) if total_tokens else None,
        "price_decimal_x_stddev": _mean(price_stddevs),
        "line_word_gap_p90": _mean(gap_p90s),
        "item_count_consistency": round(ic_matches / ic_has, 4) if ic_has else None,
        "item_count_n": ic_has,
        "vertical_overlap_rate": round(vo_hits / vo_total, 4) if vo_total else None,
        "_tokens": total_tokens,
    }
    metrics["composite"] = composite(metrics)
    return metrics


# --------------------------------------------------------------------------- #
# composite: weighted blend of [0,1] goodness scores                          #
# --------------------------------------------------------------------------- #
# Weights are documented here and renormalized over whatever metrics are
# present (some merchants lack price tokens / item-count lines).
COMPOSITE_WEIGHTS = {
    "verifier_score": 0.35,          # broadest objective pass-rate signal
    "garbled_token_rate": 0.15,      # the most jarring "obviously fake" tell
    "price_decimal_x_stddev": 0.15,  # right-edge price column alignment
    "line_word_gap_p90": 0.10,       # the "wide word-gap" tell
    "item_count_consistency": 0.10,  # internal arithmetic consistency
    "vertical_overlap_rate": 0.15,   # totals-block collision tell
}
# scales used to turn "lower=better" raw values into a [0,1] goodness.
_PRICE_STDDEV_FULL_BAD = 20.0   # stddev (coord units, 0-1000) at which goodness->0
_GAP_GOOD = 1.0                 # p90 gap <=1 glyph width is ideal
_GAP_FULL_BAD = 10.0            # p90 gap >=10 glyph widths is fully bad


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _goodness(metrics):
    """Map each raw metric to a [0,1] goodness (higher=better)."""
    g = {}
    if metrics.get("verifier_score") is not None:
        g["verifier_score"] = _clamp01(metrics["verifier_score"])
    if metrics.get("garbled_token_rate") is not None:
        g["garbled_token_rate"] = _clamp01(1.0 - metrics["garbled_token_rate"])
    if metrics.get("price_decimal_x_stddev") is not None:
        g["price_decimal_x_stddev"] = _clamp01(
            1.0 - metrics["price_decimal_x_stddev"] / _PRICE_STDDEV_FULL_BAD)
    if metrics.get("line_word_gap_p90") is not None:
        g["line_word_gap_p90"] = _clamp01(
            1.0 - (metrics["line_word_gap_p90"] - _GAP_GOOD) / (_GAP_FULL_BAD - _GAP_GOOD))
    if metrics.get("item_count_consistency") is not None:
        g["item_count_consistency"] = _clamp01(metrics["item_count_consistency"])
    if metrics.get("vertical_overlap_rate") is not None:
        g["vertical_overlap_rate"] = _clamp01(1.0 - metrics["vertical_overlap_rate"])
    return g


def composite(metrics):
    g = _goodness(metrics)
    if not g:
        return None
    num = sum(COMPOSITE_WEIGHTS[k] * v for k, v in g.items())
    den = sum(COMPOSITE_WEIGHTS[k] for k in g)
    return round(num / den, 4) if den else None


# --------------------------------------------------------------------------- #
# aggregation across merchants                                                 #
# --------------------------------------------------------------------------- #
def aggregate(per_merchant_cands):
    """per_merchant_cands: {merchant: [cands]} -> (per_merchant, aggregate)."""
    per = {m: score_candidates(c) for m, c in per_merchant_cands.items()}
    all_cands = [c for cs in per_merchant_cands.values() for c in cs]
    agg = score_candidates(all_cands)
    agg["n_merchants"] = len(per_merchant_cands)
    return per, agg


# --------------------------------------------------------------------------- #
# pretty table                                                                 #
# --------------------------------------------------------------------------- #
_COLS = [
    ("verifier_score", "verif"),
    ("garbled_token_rate", "garbl"),
    ("price_decimal_x_stddev", "px_sd"),
    ("line_word_gap_p90", "gapp90"),
    ("item_count_consistency", "itmcnt"),
    ("vertical_overlap_rate", "voverl"),
    ("composite", "COMPOS"),
]


def _fmt(v):
    return "  -  " if v is None else f"{v:6.3f}"


def print_table(per, agg):
    name_w = max([len("merchant")] + [len(m) for m in per])
    header = "  ".join([f"{'merchant':<{name_w}}", "n"] + [f"{lbl:>6}" for _, lbl in _COLS])
    print(header)
    print("-" * len(header))
    for m in sorted(per):
        mm = per[m]
        row = "  ".join(
            [f"{m:<{name_w}}", f"{mm['n_candidates']:>1}"]
            + [_fmt(mm.get(k)) for k, _ in _COLS]
        )
        print(row)
    print("-" * len(header))
    row = "  ".join(
        [f"{'AGGREGATE':<{name_w}}", f"{agg['n_candidates']:>1}"]
        + [_fmt(agg.get(k)) for k, _ in _COLS]
    )
    print(row)


# --------------------------------------------------------------------------- #
# I/O                                                                          #
# --------------------------------------------------------------------------- #
def load_matrix(matrix_dir):
    out = {}
    for bp in sorted(glob.glob(os.path.join(matrix_dir, "*", "bundle.json"))):
        merchant = os.path.basename(os.path.dirname(bp))
        try:
            b = json.load(open(bp))
        except Exception as e:  # noqa: BLE001
            print(f"!! skip {bp}: {e}", file=sys.stderr)
            continue
        cands = b.get("synthetic_training_examples") or []
        if cands:
            out[merchant] = cands
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    matrix_dir = argv[0]
    out_path = argv[1] if len(argv) > 1 else None

    per_cands = load_matrix(matrix_dir)
    if not per_cands:
        print(f"no bundles found under {matrix_dir}/*/bundle.json", file=sys.stderr)
        return 1

    per, agg = aggregate(per_cands)
    print_table(per, agg)
    print()
    print(f"composite weights: {json.dumps(COMPOSITE_WEIGHTS)}")
    print(f"COMPOSITE = {agg.get('composite')}")

    result = {
        "matrix_dir": os.path.abspath(matrix_dir),
        "composite_weights": COMPOSITE_WEIGHTS,
        "scales": {
            "price_decimal_x_stddev_full_bad": _PRICE_STDDEV_FULL_BAD,
            "line_word_gap_p90_good": _GAP_GOOD,
            "line_word_gap_p90_full_bad": _GAP_FULL_BAD,
        },
        "aggregate": agg,
        "per_merchant": per,
    }
    if out_path:
        json.dump(result, open(out_path, "w"), indent=2)
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
