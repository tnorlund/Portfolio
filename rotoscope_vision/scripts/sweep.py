#!/usr/bin/env python3
"""Coordinate-descent parameter sweep for rotoscope-vision.

Each trial runs the binary on a short excerpt with one parameter changed,
reads the objective from summary.json, and keeps the change if it improved
without crossing a red line against the starting point. Deterministic: no
randomness, candidates are fixed per key, order is the declared order.

  python3 scripts/sweep.py IMG_0974.mov --frames 60 --out sweep/ \
      --keys diffCenter,diffWidth,priorWeight,structWeight,smoothRadius

Writes sweep/leaderboard.csv (one row per trial) and sweep/best.json.
"""
import argparse
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BINARY = os.path.join(HERE, "..", ".build", "release", "rotoscope-vision")

# Candidate values per key. Kept small so a full pass is minutes, not hours.
CANDIDATES = {
    "diffCenter": [28, 34, 40, 48, 56],
    "diffWidth": [6, 10, 14],
    "priorWeight": [0.3, 0.6, 0.8],
    "priorDecay": [0.85, 0.92, 0.97],
    "structWeight": [0.4, 0.75, 1.0],
    "barHalfWidth": [6, 9, 12],
    "shadowStrength": [0.6, 0.85, 1.0],
    "shadowChromaTolerance": [18, 26, 34],
    "smoothRadius": [0, 1, 2, 3],
    "decisionThreshold": [0.4, 0.5, 0.6],
    "plateTolerance": [3, 4, 6],
    "headExclusion": [0.08, 0.125, 0.18],
    "markerBudget": [900, 1200, 1600],
    "spacingBody": [3, 4, 6],
}


def run(clip, params, out_dir, frames, subject):
    os.makedirs(out_dir, exist_ok=True)
    params_path = os.path.join(out_dir, "params.json")
    with open(params_path, "w") as f:
        json.dump(params, f, indent=2, sort_keys=True)
    cmd = [BINARY, clip, "--subject", subject, "--params", params_path, "--metrics", out_dir,
           "--out-dir", out_dir, "--no-mov", "--no-audio", "--max-frames", str(frames)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        return None
    with open(os.path.join(out_dir, "summary.json")) as f:
        return json.load(f)


def load_objective(path):
    with open(path) as f:
        return json.load(f)


def stat(summary, metric, which):
    s = summary["stats"].get(metric)
    if not s:
        return None
    return s[which]


def score(summary, objective):
    total = 0.0
    for term in objective["terms"]:
        value = stat(summary, term["metric"], term["stat"])
        if value is None:
            continue
        if term["higherIsBetter"]:
            normalized = 1 - max(0.0, min(1.0, (term["target"] - value) / term["scale"]))
        else:
            normalized = 1 - max(0.0, min(1.0, (value - term["target"]) / term["scale"]))
        total += term["weight"] * normalized
    return total


def violations(candidate, baseline, objective):
    out = []
    for line in objective["redLines"]:
        c = stat(candidate, line["metric"], line["stat"])
        b = stat(baseline, line["metric"], line["stat"])
        if c is None or b is None:
            continue
        if c - b > line["maxIncrease"]:
            out.append(f"{line['metric']} {b:.4f}->{c:.4f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--out", default="sweep")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--subject", default="held")
    ap.add_argument("--keys", default=",".join(CANDIDATES.keys()))
    ap.add_argument("--params", help="starting params JSON (default: binary defaults)")
    ap.add_argument("--objective", default=os.path.join(HERE, "..", "bench", "objective.json"))
    ap.add_argument("--passes", type=int, default=1)
    args = ap.parse_args()

    objective = load_objective(args.objective)
    if args.params:
        with open(args.params) as f:
            params = json.load(f)
    else:
        params = json.loads(subprocess.check_output([BINARY, "--dump-params"], text=True))

    os.makedirs(args.out, exist_ok=True)
    leaderboard = open(os.path.join(args.out, "leaderboard.csv"), "w", newline="")
    writer = csv.writer(leaderboard)
    writer.writerow(["trial", "key", "value", "objective", "accepted", "violations"])

    print("baseline …", flush=True)
    base = run(args.clip, params, os.path.join(args.out, "trial-000"), args.frames, args.subject)
    if base is None:
        sys.exit("baseline run failed")
    best_score = score(base, objective)
    best = base
    writer.writerow([0, "", "", f"{best_score:.4f}", 1, ""])
    print(f"baseline objective {best_score:.3f}", flush=True)

    trial = 0
    for _ in range(args.passes):
        for key in args.keys.split(","):
            key = key.strip()
            if key not in CANDIDATES:
                print(f"skip unknown key {key}")
                continue
            for value in CANDIDATES[key]:
                if params.get(key) == value:
                    continue
                trial += 1
                candidate = dict(params)
                candidate[key] = value
                summary = run(args.clip, candidate, os.path.join(args.out, f"trial-{trial:03d}"), args.frames, args.subject)
                if summary is None:
                    writer.writerow([trial, key, value, "", 0, "run failed"])
                    continue
                s = score(summary, objective)
                bad = violations(summary, best, objective)
                accepted = s > best_score and not bad
                writer.writerow([trial, key, value, f"{s:.4f}", int(accepted), "; ".join(bad)])
                leaderboard.flush()
                print(f"trial {trial:3d} {key}={value}: {s:.3f} {'ACCEPT' if accepted else ''} {'; '.join(bad)}", flush=True)
                if accepted:
                    params = candidate
                    best_score = s
                    best = summary
    with open(os.path.join(args.out, "best.json"), "w") as f:
        json.dump(params, f, indent=2, sort_keys=True)
    print(f"best objective {best_score:.3f} → {os.path.join(args.out, 'best.json')}")


if __name__ == "__main__":
    main()
