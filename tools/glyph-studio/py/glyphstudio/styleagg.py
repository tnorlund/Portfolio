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
