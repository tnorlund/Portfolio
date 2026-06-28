#!/usr/bin/env python3
"""Hill-climb bookkeeping — HONEST signal only.

Reads this round's judge output `state/reviews/round-N.json` written by judge_round.sh
in the fixed contract shape:
    {"run_id","round","candidates":[{"candidate_id","operation",
        "texture_realism","structural_plausibility","status"}], "top_fixes":[...]}

Round score = mean of THIS round's candidate `texture_realism` (the objective the cached
render can actually move). There is NO fallback to any cumulative/aggregate average — if
this round produced no scored candidates, the score is None and the round is treated as a
FAILED round (never a "new best"). best.json is scoped to the current run_id so a fresh run
does not inherit a stale best.
"""
from __future__ import annotations
import argparse, json, pathlib


def _load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _round_score(review: dict):
    """Mean texture_realism over THIS round's candidates, or None if none."""
    cands = review.get("candidates")
    if not isinstance(cands, list):
        return None
    vals = [c.get("texture_realism") for c in cands]
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def _struct_score(review: dict):
    cands = review.get("candidates") or []
    vals = [c.get("structural_plausibility") for c in cands]
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--check-improved", action="store_true",
                    help="exit 0 iff this round strictly improved the run's best texture score")
    a = ap.parse_args()
    state = pathlib.Path(a.state)
    review = _load(state / "reviews" / f"round-{a.round}.json")
    score = _round_score(review)

    best = _load(state / "best.json")
    # a best from a different run does not count
    best_score = best.get("score") if best.get("run_id") == a.run_id else None

    if a.check_improved:
        # a failed round (no scored candidates) never counts as improvement
        if score is None:
            return 1
        return 0 if (best_score is None or score > best_score) else 1

    improved = score is not None and (best_score is None or score > best_score)
    if improved:
        (state / "best.json").write_text(json.dumps({
            "run_id": a.run_id, "round": a.round, "score": score,
            "structural": _struct_score(review),
            "params": _load(state / "params.json"),
        }, indent=2))

    s = "FAILED (no candidates)" if score is None else f"{score:.3f}"
    line = (f"- round {a.round}: texture={s}"
            f"  struct={_struct_score(review) if review.get('candidates') else 'n/a'}"
            f"  best={best_score if best_score is not None else 'n/a'}"
            f"  {'NEW BEST' if improved else ''}\n")
    status = state / "STATUS.md"
    if not status.exists():
        status.write_text(f"# Synthesis hill-climb status (run {a.run_id})\n\n")
    with status.open("a") as f:
        f.write(line)
    print(line.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
