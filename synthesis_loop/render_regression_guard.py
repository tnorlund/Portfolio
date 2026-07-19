#!/usr/bin/env python3.12
"""Byte-identical re-render gate for shipped merchants.

Renders each pinned shipped receipt through the exact ``glyph_review.py
receipt`` recipe and hashes the SYNTH panel PNG. ``capture`` writes a baseline
manifest; ``compare`` re-renders and fails loudly on any hash change, reporting
the pixel MAD of each mismatch so a reviewer can judge the drift. The paper
texture is content-seeded, so an unchanged code path re-renders byte-identical
(MAD 0.0000).

The pinned set covers the shipped-atlas merchants a systemic renderer change
must not disturb. Receipt keys in the dev table are NOT stable across re-OCR
(the Vons golden moved from #1 to #2 once already), so a lookup failure or an
obviously-wrong merchant render means the pin needs re-verifying, not that the
renderer regressed.

Usage:
  render_regression_guard.py capture <out_dir>
  render_regression_guard.py compare <baseline_dir> <out_dir>
  render_regression_guard.py check <out_dir>

``check`` compares against the COMMITTED baseline manifest
(``render_regression_baseline.json`` beside this script) so a fresh checkout
verifies the pinned hashes without first trusting a locally-captured
baseline. Re-capture (and commit) that manifest only for an intended,
reviewed render change.

Env: same as glyph_review receipt mode (DYNAMODB_TABLE_NAME, AWS_REGION,
BITMATRIX_DIR, AWS creds with read access to the dev table).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for path in (HERE, os.path.join(os.path.dirname(HERE), "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

# (merchant profile key, image_id, receipt_id, slug)
PINNED = [
    (
        "Costco Wholesale",
        "57cb7f2c-7dcc-4974-9ef8-a460232f3b1d",
        1,
        "costco_golden",
    ),
    (
        "Costco Wholesale",
        "0324604e-e1f7-4021-b887-7ef7e012c563",
        1,
        "costco_new",
    ),
    (
        "Vons",
        "678a7c94-4948-4ebf-b8e9-9a17c13051ec",
        2,
        "vons_golden",
    ),
    (
        "Sprouts Farmers Market",
        "00ded398-af6f-4a49-86f7-c79ccb554e48",
        1,
        "sprouts",
    ),
]


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_all(out_dir: str) -> dict[str, str]:
    import glyph_review

    os.makedirs(out_dir, exist_ok=True)
    manifest: dict[str, str] = {}
    for merchant, image_id, receipt_id, slug in PINNED:
        out_png = os.path.join(out_dir, f"{slug}.png")
        glyph_review.receipt(merchant, image_id, receipt_id, out_png)
        # glyph_review saves the raw synthetic render (the panel under test,
        # before the REAL|SYNTH montage) alongside the montage.
        manifest[slug] = _sha256(f"{out_png}.syn.png")
    return manifest


def _pixel_mad(path_a: str, path_b: str) -> float:
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(path_a).convert("RGB"), dtype=np.float64)
    b = np.asarray(Image.open(path_b).convert("RGB"), dtype=np.float64)
    if a.shape != b.shape:
        return float("inf")
    return float(np.abs(a - b).mean())


def capture(out_dir: str) -> int:
    manifest = _render_all(out_dir)
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"baseline captured: {manifest_path}")
    return 0


def compare(baseline_dir: str, out_dir: str) -> int:
    with open(
        os.path.join(baseline_dir, "manifest.json"), encoding="utf-8"
    ) as fh:
        baseline = json.load(fh)
    manifest = _render_all(out_dir)
    failures = []
    for slug, sha in sorted(manifest.items()):
        base_sha = baseline.get(slug)
        if base_sha == sha:
            print(f"{slug}: byte-identical (MAD 0.0000)")
            continue
        mad = _pixel_mad(
            os.path.join(baseline_dir, f"{slug}.png.syn.png"),
            os.path.join(out_dir, f"{slug}.png.syn.png"),
        )
        print(f"{slug}: CHANGED (MAD {mad:.4f})")
        failures.append(slug)
    if failures:
        print(f"REGRESSION: {len(failures)} render(s) changed: {failures}")
        return 1
    print("all pinned renders byte-identical")
    return 0


COMMITTED_BASELINE = os.path.join(HERE, "render_regression_baseline.json")


def check(out_dir: str) -> int:
    """Compare fresh renders against the COMMITTED baseline hashes."""
    with open(COMMITTED_BASELINE, encoding="utf-8") as fh:
        baseline = json.load(fh)
    manifest = _render_all(out_dir)
    failures = [
        slug
        for slug, sha in sorted(manifest.items())
        if baseline.get(slug) != sha
    ]
    for slug in sorted(manifest):
        state = "CHANGED" if slug in failures else "byte-identical"
        print(f"{slug}: {state}")
    if failures:
        print(f"REGRESSION vs committed baseline: {failures}")
        return 1
    print("all pinned renders match the committed baseline")
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "capture":
        return capture(sys.argv[2])
    if len(sys.argv) >= 4 and sys.argv[1] == "compare":
        return compare(sys.argv[2], sys.argv[3])
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        return check(sys.argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
