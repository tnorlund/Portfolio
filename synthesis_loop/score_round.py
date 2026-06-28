#!/usr/bin/env python3
"""Hill-climb bookkeeping: read a round's Claude review JSON, update best.json and
STATUS.md, and (with --check-improved) exit 0 iff the round's aggregate realism
score is >= the previous best.

The review JSON is the final line emitted by judge_round.sh, i.e. the output of the
`summarize_synthetic_receipt_visual_reviews` MCP tool. We read its aggregate score
defensively because the exact field name may evolve — adjust SCORE_KEYS as needed.
"""
from __future__ import annotations
import argparse, json, pathlib, sys

SCORE_KEYS = ("mean_score", "average_score", "avg_score", "aggregate_score", "score")


def _load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _score(review: dict):
    # Objective = THIS round's mean realism ("looks real to Claude"). The summarize JSON shape is
    # {"this_round":[{realism_score,...}], "avg_scores":{realism_score,...}, ...}.
    rows = review.get("this_round") or review.get("reviews") or review.get("items") or []
    vals = [r.get("realism_score", r.get("texture_realism", r.get("score"))) for r in rows]
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    if vals:
        return sum(vals) / len(vals)
    agg = review.get("avg_scores") or {}
    if isinstance(agg.get("realism_score"), (int, float)):
        return float(agg["realism_score"])
    for k in SCORE_KEYS:
        v = review.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--check-improved", action="store_true")
    a = ap.parse_args()
    state = pathlib.Path(a.state)
    review = _load(state / "reviews" / f"round-{a.round}.json")
    score = _score(review)
    best = _load(state / "best.json")
    best_score = best.get("score")

    if a.check_improved:
        if score is None or best_score is None:
            return 0  # be lenient when we can't parse — don't false-stop the loop
        return 0 if score >= best_score else 1

    improved = score is not None and (best_score is None or score > best_score)
    if improved:
        params = _load(state / "params.json")
        (state / "best.json").write_text(json.dumps(
            {"round": a.round, "score": score, "params": params}, indent=2))

    line = (f"- round {a.round}: score={score if score is not None else 'n/a'}"
            f"  best={best_score if best_score is not None else 'n/a'}"
            f"  {'NEW BEST' if improved else ''}\n")
    status = state / "STATUS.md"
    header = "" if status.exists() else "# Synthesis hill-climb status\n\n"
    with status.open("a") as f:
        if header:
            f.write(header)
        f.write(line)
    print(line.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
