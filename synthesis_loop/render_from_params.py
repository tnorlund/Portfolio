#!/usr/bin/env python3
"""Render this round's candidates from state/params.json (run by the SHELL, which has
DynamoDB/network for the glyph atlas — codex's sandbox does not). Cached mode for now:
re-renders the committed synthetic examples through the glyph renderer with the current
realism knobs. Bundle mode (full verify_synthetic_replay -> render) is a later upgrade for
multi-merchant rotation.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repo", required=True)
    a = ap.parse_args()
    p = json.load(open(a.params))
    cached_dir = os.path.join(a.repo, "screenshots", "synthetic_receipts")
    cmd = [
        sys.executable, os.path.join(a.repo, "scripts", "render_synthetic_receipts.py"),
        "--cached-synthetic-dir", cached_dir,
        "--merchant", a.merchant,
        "--out-dir", a.out_dir,
        # the glyph cached renderer is the one that honors these realism knobs (hybrid ignores them)
        "--cached-renderer", str(p.get("cached_renderer", "glyph")),
        "--noise", str(p.get("noise", 0.42)),
        "--blur", str(p.get("blur", 0.35)),
        "--paper-realism", str(p.get("paper_realism", 0.65)),
        "--seed", str(p.get("seed", 37)),
        "--glyph-max-receipts", str(p.get("glyph_max_receipts", 8)),
        "--profile-max-receipts", str(p.get("profile_max_receipts", 12)),
    ]
    print("render:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
