#!/usr/bin/env python3
"""Score EVERY merchant's regenerated bundle with the objective verifier, emit per-merchant
mean + which checks still fail (the feedback the loop's codex brain reads), and an overall mean
across merchants (the honest hill-climb signal). Usage: score_all_merchants.py <mm_dir> <out.json>"""
from __future__ import annotations
import glob, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_candidates as V  # noqa: E402


def main() -> int:
    mm, out = sys.argv[1], sys.argv[2]
    rows = {}
    for bj in sorted(glob.glob(os.path.join(mm, "*", "bundle.json"))):
        slug = os.path.basename(os.path.dirname(bj))
        try:
            cands = json.load(open(bj)).get("synthetic_training_examples", []) or []
        except Exception:
            cands = []
        if not cands:
            continue
        agg, scores = {}, []
        for c in cands:
            r = V.check(c)
            for k, v in r.items():
                if k == "_score" or v.get("pass") is None:
                    continue
                agg.setdefault(k, [0, 0])
                agg[k][1] += 1
                agg[k][0] += 1 if v["pass"] else 0
            if r["_score"] is not None:
                scores.append(r["_score"])
        rows[slug] = {
            "mean": round(statistics.mean(scores), 3) if scores else None,
            "n": len(cands),
            "failing_checks": {k: f"{a[0]}/{a[1]}" for k, a in agg.items() if a[0] < a[1]},
        }
    means = [r["mean"] for r in rows.values() if r["mean"] is not None]
    overall = round(statistics.mean(means), 3) if means else None
    worst = round(min(means), 3) if means else None
    json.dump({"overall_mean": overall, "worst_merchant_mean": worst, "merchants": rows},
              open(out, "w"), indent=2)
    print(f"overall_mean={overall}  worst={worst}")
    for s, r in sorted(rows.items(), key=lambda x: x[1]["mean"] or 0):
        print(f"  {s}: {r['mean']}  failing: {r['failing_checks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
