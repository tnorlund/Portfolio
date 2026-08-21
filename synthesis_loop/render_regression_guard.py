#!/usr/bin/env python3.13
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
BITMATRIX_DIR, AWS creds with read access to the dev table). Merchant truth
resolves through the MerchantTruthLoader (MERCHANT_TRUTH_MODE, default
online-active); the committed baseline is captured with loader-driven
renders, so the guard now pins the LOADER path, not the retired
merchant_profiles.json path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for path in (HERE, os.path.join(REPO, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)


@dataclass(frozen=True)
class PinnedReceipt:
    merchant: str
    image_id: str
    receipt_id: int
    slug: str
    truth_fixture: str | None = None


PINNED = [
    PinnedReceipt(
        "Costco Wholesale",
        "57cb7f2c-7dcc-4974-9ef8-a460232f3b1d",
        1,
        "costco_golden",
    ),
    PinnedReceipt(
        "Costco Wholesale",
        "0324604e-e1f7-4021-b887-7ef7e012c563",
        1,
        "costco_new",
    ),
    PinnedReceipt(
        "Vons",
        "678a7c94-4948-4ebf-b8e9-9a17c13051ec",
        2,
        "vons_golden",
    ),
    PinnedReceipt(
        "Sprouts Farmers Market",
        "00ded398-af6f-4a49-86f7-c79ccb554e48",
        1,
        "sprouts",
    ),
    PinnedReceipt(
        "Gelson's Westlake Village",
        "223c03e2-9f7e-481d-bc33-2b0631fbaaf9",
        1,
        "gelsons",
    ),
    PinnedReceipt(
        "Trader Joe's",
        "4c262079-4fec-4724-a8e1-2886f38ea454",
        1,
        "trader_joes",
    ),
    PinnedReceipt(
        "Wild Fork",
        "f008ea77-272b-4554-b8a6-e1676ec6a088",
        2,
        "wild_fork",
    ),
    PinnedReceipt(
        "The Stand - American Classics Redefined",
        "016bda87-f2a9-4dfc-85cc-e2b672ccb27a",
        1,
        "the_stand",
    ),
    PinnedReceipt(
        "Dollar Tree",
        "0944ee8d-4a0d-464e-8109-de35a9ba379a",
        1,
        "dollar_tree_fixture",
        truth_fixture="legacy-profile-without-logo",
    ),
]


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install_dollar_tree_fixture() -> None:
    """Install the explicit Dollar Tree render fixture for this process.

    Dollar Tree has no ACTIVE merchant-truth pointer, so the production
    online-active path correctly refuses it. The guard still protects its
    committed compose renderer using the retired profile plus the committed
    ROM atlas. The fixture intentionally omits the unpublished logo and is
    named as a fixture in the baseline slug; it never masquerades as ACTIVE.
    """
    import render_synthetic_receipts as rsr

    from receipt_dynamo.merchant_truth_loader import (
        MerchantTruthArtifact,
        TruthResolutionMode,
    )

    profile_path = os.path.join(REPO, "scripts", "merchant_profiles.json")
    with open(profile_path, encoding="utf-8") as handle:
        profile = copy.deepcopy(json.load(handle)["profiles"]["Dollar Tree"])
    profile.pop("_comment", None)
    profile.pop("logo", None)
    profile.pop("logo_anchor", None)

    font_filename = "dollartree.glyphs.npz"
    source_font = os.path.join(
        HERE,
        "rom_font_atlases",
        font_filename,
    )
    os.makedirs(rsr._BITMATRIX_DIR, exist_ok=True)
    cached_font = os.path.join(rsr._BITMATRIX_DIR, font_filename)
    if not os.path.exists(cached_font) or _sha256(cached_font) != _sha256(
        source_font
    ):
        shutil.copyfile(source_font, cached_font)
    font_hash = _sha256(source_font)

    components = {
        "identity": {
            "aliases": ["DOLLAR TREE"],
            "merchant_name": "Dollar Tree",
            "normalized_aliases": ["dollar tree"],
            "slug": "dollar_tree",
        },
        "typography": {
            "section_scale": profile.get("section_scale") or {},
            "typography": profile.get("typography") or {},
        },
        "flags": {
            key: copy.deepcopy(profile[key])
            for key in (
                "compose",
                "graphics",
                "header",
                "logo_reserve_subtitle",
                "logo_subtitle",
            )
            if key in profile
        },
        "layout": {
            "available": "layout_template" in profile,
            "template": profile.get("layout_template"),
        },
        "assets": {
            "fonts": {
                face: {
                    "cache_filename": font_filename,
                    "content_hash": font_hash,
                }
                for face in ("heavy", "regular")
            },
            "profile": {},
        },
        "stylemap": {"available": False, "document": None},
    }
    fixture_hash = hashlib.sha256(
        json.dumps(
            components,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    artifact = MerchantTruthArtifact(
        slug="dollar_tree",
        version=0,
        bundle_hash=fixture_hash,
        expected_bundle_hash=fixture_hash,
        components=components,
        mode=TruthResolutionMode.FIXTURE,
    )
    rsr._MERCHANT_TRUTH_REGISTRY = rsr._MerchantTruthRegistry(
        TruthResolutionMode.FIXTURE.value,
        {"dollar_tree": artifact},
        [],
    )


def _render_all(out_dir: str) -> dict[str, str]:
    import glyph_review

    os.makedirs(out_dir, exist_ok=True)
    manifest: dict[str, str] = {}
    for pin in PINNED:
        if pin.truth_fixture == "legacy-profile-without-logo":
            _install_dollar_tree_fixture()
        try:
            out_png = os.path.join(out_dir, f"{pin.slug}.png")
            glyph_review.receipt(
                pin.merchant,
                pin.image_id,
                pin.receipt_id,
                out_png,
            )
            # glyph_review saves the raw synthetic render (the panel under
            # test, before the REAL|SYNTH montage) alongside the montage.
            manifest[pin.slug] = _sha256(f"{out_png}.syn.png")
        finally:
            if pin.truth_fixture:
                import render_synthetic_receipts as rsr

                rsr._reset_merchant_truth_registry()
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


def _manifest_keys_match(
    baseline: dict[str, str], manifest: dict[str, str]
) -> bool:
    """Report whether the rendered and baseline slug sets are identical."""
    baseline_slugs = set(baseline)
    rendered_slugs = set(manifest)
    missing = sorted(baseline_slugs - rendered_slugs)
    extra = sorted(rendered_slugs - baseline_slugs)
    if not missing and not extra:
        return True

    print("render manifest key-set mismatch")
    print(f"missing slugs: {missing}")
    print(f"extra slugs: {extra}")
    return False


def compare(baseline_dir: str, out_dir: str) -> int:
    with open(
        os.path.join(baseline_dir, "manifest.json"), encoding="utf-8"
    ) as fh:
        baseline = json.load(fh)
    manifest = _render_all(out_dir)
    if not _manifest_keys_match(baseline, manifest):
        print("REGRESSION: rendered slug set differs from baseline")
        return 1

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
    if not _manifest_keys_match(baseline, manifest):
        print("REGRESSION: rendered slug set differs from committed baseline")
        return 1

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
