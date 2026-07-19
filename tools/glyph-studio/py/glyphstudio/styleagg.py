"""Aggregate stylescan outputs -> fleet per-section / per-letter statistics.

Usage: python -m glyphstudio.styleagg <scan_dir> <out.json>

Every measurement is normalized per receipt against that receipt's own body
(median cap/stroke of item/other/footer/survey lines) before pooling, so
scanner exposure and resolution cancel out.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from statistics import median

try:
    from .sections import normalize_stylescan_section
except ImportError:  # bare-script invocation
    from sections import normalize_stylescan_section

# --- columnscan aggregation (#1188 P2) -------------------------------------
#
# Consumes the stylescan token-edge rider and emits the layout_template
# ``columns`` block: per canonical section, the measured column lanes
# {role, anchor, x, spread, support}. Right-anchored roles cluster on their
# right edge (amounts/qty share a right edge), left-anchored on their left.

_ROLE_ANCHOR = {
    "amount": "right",
    "qty": "right",
    "flag": "left",
    "desc": "left",
    "label": "left",
}
# per-receipt greedy 1-D cluster tolerance (fraction of paper width) -- the
# _price_column_x mechanics generalized to every role.
RECEIPT_CLUSTER_TOL = 0.04
# cross-receipt pooling: two receipt-level lanes are the same column when
# their x differ by at most this.
POOL_MATCH_TOL = 0.03


def _cluster_1d(values: list[float], tol: float) -> list[list[float]]:
    """Greedy 1-D clustering, scanning right-to-left (rightmost first)."""
    clusters: list[list[float]] = []
    for v in sorted(values, reverse=True):
        if clusters and abs(clusters[-1][0] - v) <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def _iqr(vals: list[float]) -> float:
    qs = sorted(vals)
    return qs[int(0.75 * (len(qs) - 1))] - qs[int(0.25 * (len(qs) - 1))]


def receipt_section_columns(
    lines: list[dict], *, tol: float = RECEIPT_CLUSTER_TOL
) -> dict[str, list[dict]]:
    """One receipt's measured columns per canonical section.

    ``lines`` are stylescan output lines carrying the ``tokens`` rider.
    Weight sublines carry no tokens by construction. desc/label lanes use
    only the FIRST such token of a line (the column START); amount/qty/flag
    lanes use every token of their role.
    """
    edges: dict[tuple[str, str], list[float]] = defaultdict(list)
    for line in lines:
        section = line.get("section_canonical") or normalize_stylescan_section(
            line.get("section")
        )
        if not section:
            continue
        seen_first: set[str] = set()
        for tok in line.get("tokens") or []:
            role = tok["role"]
            anchor = _ROLE_ANCHOR.get(role)
            if anchor is None:
                continue
            if role in ("desc", "label"):
                if role in seen_first:
                    continue
                seen_first.add(role)
            edges[(section, role)].append(
                tok["r"] if anchor == "right" else tok["l"]
            )
    out: dict[str, list[dict]] = defaultdict(list)
    for (section, role), vals in edges.items():
        for cluster in _cluster_1d(vals, tol):
            if len(cluster) < 2:
                continue
            out[section].append(
                {
                    "role": role,
                    "anchor": _ROLE_ANCHOR[role],
                    "x": round(median(cluster), 4),
                    "spread": round(_iqr(cluster), 4),
                    "support": len(cluster),
                }
            )
    for cols in out.values():
        cols.sort(key=lambda c: c["x"])
    return dict(out)


def pool_columns(
    per_receipt: list[dict[str, list[dict]]],
    *,
    tol: float = POOL_MATCH_TOL,
) -> dict[str, list[dict]]:
    """Pool receipt-level columns across receipts (nearest-x matching).

    A pooled column keeps a lane supported by >= max(2, n_receipts/2)
    receipts; ``support`` in the emitted block counts SUPPORTING RECEIPTS,
    ``spread`` is the IQR of the receipt-level lane positions.
    """
    n = len(per_receipt)
    # "at least half the receipts" means CEILING division: 3 of 5, not 2.
    need = max(2, -(-n // 2))
    pooled: dict[str, list[dict]] = {}
    sections = {s for cols in per_receipt for s in cols}
    for section in sorted(sections):
        # {"role", "xs": [...], "receipts": {idx...}} -- support counts
        # DISTINCT receipts, and one receipt contributes at most one lane to
        # a pool (two same-role lanes from one receipt are different columns
        # by construction: they were > RECEIPT_CLUSTER_TOL apart there).
        lanes: list[dict] = []
        for ridx, cols in enumerate(per_receipt):
            for col in cols.get(section, []):
                best = None
                for lane in lanes:
                    if lane["role"] != col["role"]:
                        continue
                    if ridx in lane["receipts"]:
                        continue
                    d = abs(median(lane["xs"]) - col["x"])
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, lane)
                if best:
                    best[1]["xs"].append(col["x"])
                    best[1]["receipts"].add(ridx)
                else:
                    lanes.append(
                        {
                            "role": col["role"],
                            "xs": [col["x"]],
                            "receipts": {ridx},
                        }
                    )
        kept = [
            {
                "role": lane["role"],
                "anchor": _ROLE_ANCHOR[lane["role"]],
                "x": round(median(lane["xs"]), 4),
                "spread": round(_iqr(lane["xs"]), 4),
                "support": len(lane["receipts"]),
            }
            for lane in lanes
            if len(lane["receipts"]) >= need
        ]
        if kept:
            kept.sort(key=lambda c: c["x"])
            pooled[section] = kept
    return pooled


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scan_dir")
    ap.add_argument("out")
    args = ap.parse_args(argv)

    files = [
        f
        for f in glob.glob(f"{args.scan_dir}/*.json")
        if not f.endswith(("receipts.json", "stylemap.json"))
    ]
    sections = defaultdict(
        lambda: {
            "lines": 0,
            "underlined": 0,
            "cap_rel": [],
            "stroke_rel": [],
            "density": [],
            "examples": [],
        }
    )
    letters = defaultdict(
        lambda: defaultdict(
            lambda: {"n": 0, "stroke_rel": [], "density": [], "h_rel": []}
        )
    )
    receipts_used = 0
    per_receipt_columns: list[dict[str, list[dict]]] = []

    for f in sorted(files):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        body_cap = d.get("body_cap_px")
        body_stroke = d.get("body_stroke_px")
        if not body_cap or not body_stroke:
            continue
        receipts_used += 1
        per_receipt_columns.append(receipt_section_columns(d.get("lines", [])))
        for line in d.get("lines", []):
            sec = line["section"]
            s = sections[sec]
            s["lines"] += 1
            s["underlined"] += int(bool(line.get("underline")))
            if line.get("cap_px"):
                s["cap_rel"].append(line["cap_px"] / body_cap)
            if line.get("stroke_med"):
                s["stroke_rel"].append(line["stroke_med"] / body_stroke)
            if line.get("density_med"):
                s["density"].append(line["density_med"])
            if len(s["examples"]) < 4 and line.get("text"):
                s["examples"].append(line["text"][:40])
            for ch in line.get("letters", []):
                if not ch.get("ch"):
                    continue
                rec = letters[sec][ch["ch"]]
                rec["n"] += 1
                if ch.get("stroke"):
                    rec["stroke_rel"].append(ch["stroke"] / body_stroke)
                if ch.get("density"):
                    rec["density"].append(ch["density"])
                if ch.get("h") and body_cap:
                    rec["h_rel"].append(ch["h"] / body_cap)

    def q(vals, p):
        if not vals:
            return None
        vals = sorted(vals)
        i = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        return round(vals[i], 3)

    out_sections = {}
    for sec, s in sorted(sections.items()):
        out_sections[sec] = {
            "lines": s["lines"],
            "underline_rate": round(s["underlined"] / max(1, s["lines"]), 3),
            "cap_rel": {
                "p25": q(s["cap_rel"], 0.25),
                "med": q(s["cap_rel"], 0.5),
                "p75": q(s["cap_rel"], 0.75),
            },
            "stroke_rel": {
                "p25": q(s["stroke_rel"], 0.25),
                "med": q(s["stroke_rel"], 0.5),
                "p75": q(s["stroke_rel"], 0.75),
            },
            "density_med": q(s["density"], 0.5),
            "examples": s["examples"],
        }

    out_letters = {}
    for sec, chars in letters.items():
        per = {}
        for ch, rec in chars.items():
            if rec["n"] < 8:
                continue
            per[ch] = {
                "n": rec["n"],
                "stroke_rel_med": q(rec["stroke_rel"], 0.5),
                "density_med": q(rec["density"], 0.5),
                "h_rel_med": q(rec["h_rel"], 0.5),
            }
        if per:
            out_letters[sec] = dict(sorted(per.items()))

    result = {
        "receipts_used": receipts_used,
        "files_scanned": len(files),
        "sections": out_sections,
        "letters_by_section": out_letters,
        # columnscan (#1188 P2): pooled per-section column lanes, the
        # layout_template ``columns`` block.
        "columns": pool_columns(per_receipt_columns),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print(f"aggregated {receipts_used} receipts -> {args.out}")
    for sec, s in out_sections.items():
        print(
            f"  {sec:16s} n={s['lines']:5d} ul={s['underline_rate']:.2f} "
            f"cap={s['cap_rel']['med']} stroke={s['stroke_rel']['med']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
