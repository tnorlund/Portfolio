#!/usr/bin/env python3
"""Which frames are missing the band or a plate?

Reads metrics.jsonl (written by `rotoscope-vision --metrics DIR`) and prints,
per held object, the frames where the mask covers less than --recall of the
evaluation truth proxy (see Presence.swift), grouped into runs, plus a
one-line summary per object. Frames where the truth is too small to judge
(bandRecall / plateRecall null) are reported separately as "unjudged".

    python3 scripts/presence.py runs/x/metrics.jsonl [--recall 0.5]
"""
import argparse
import json
import sys


def runs(frames):
    out, start, prev = [], None, None
    for f in frames:
        if start is None:
            start = prev = f
        elif f == prev + 1:
            prev = f
        else:
            out.append((start, prev))
            start = prev = f
    if start is not None:
        out.append((start, prev))
    return out


def fmt(rs):
    return ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in rs) or "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics")
    ap.add_argument("--recall", type=float, default=0.5, help="below this the object counts as missing")
    args = ap.parse_args()
    rows = [json.loads(l) for l in open(args.metrics) if l.strip()]
    objects = [
        ("band", "bandRecall", "bandTruth"),
        ("left plate", "plateRecallLeft", "plateTruthLeft"),
        ("right plate", "plateRecallRight", "plateTruthRight"),
    ]
    total = len(rows)
    worst = 0.0
    for name, rk, tk in objects:
        judged = [r for r in rows if r.get(rk) is not None]
        missing = [r["frame"] for r in judged if r[rk] < args.recall]
        unjudged = [r["frame"] for r in rows if r.get(rk) is None]
        mean = sum(r[rk] for r in judged) / len(judged) if judged else float("nan")
        frac = len(missing) / len(judged) if judged else float("nan")
        worst = max(worst, frac if judged else 0)
        print(f"{name:12s} judged {len(judged):3d}/{total}  mean recall {mean:.3f}  "
              f"missing {len(missing):3d} ({frac:5.1%})")
        print(f"{'':12s} missing frames: {fmt(runs(missing))}")
        if unjudged:
            print(f"{'':12s} unjudged (truth too small / no pose): {fmt(runs(unjudged))}")
        # Worst frames with their recall, for going straight to the still.
        bad = sorted(judged, key=lambda r: r[rk])[:5]
        print(f"{'':12s} worst: " + ", ".join(f"{r['frame']} ({r[rk]:.2f}, truth {r[tk]}px)" for r in bad))
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
