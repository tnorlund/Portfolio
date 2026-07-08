"""Regression lock for the synthetic-render merchant-profile contract.

The font-calibration epic (tools/glyph-studio/DERIVATION_EPIC.md) closed with
every bitmap-font merchant's ``bitmap_thin`` PINNED in
``scripts/merchant_profiles.json``. An unpinned value silently reverts the
renderer to deriving it via a full-receipt render bisection on every cold
render (~10 min for Home Depot) — a regression that is invisible until someone
waits on it. This contract test makes that failure loud.

It lives in receipt_dynamo's suite (rather than tools/glyph-studio) because CI
runs only the receipt_* package suites; the profile file is a repo-level render
contract, so the test skips gracefully when the repo root is not present
(package-only installs).
"""

import json
import os

import pytest

_PROFILES_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "scripts",
        "merchant_profiles.json",
    )
)

pytestmark = pytest.mark.skipif(
    not os.path.exists(_PROFILES_PATH),
    reason="repo-level scripts/merchant_profiles.json not present "
    "(package-only install)",
)


def _profiles():
    with open(_PROFILES_PATH) as f:
        return json.load(f)["profiles"]


def test_profiles_json_parses_with_profiles_map():
    profiles = _profiles()
    assert isinstance(profiles, dict) and profiles, "profiles map missing"


def test_every_bitmap_font_merchant_pins_bitmap_thin():
    """No bitmap-font merchant may leave bitmap_thin unpinned (slow re-derive)."""
    unpinned = []
    for name, prof in _profiles().items():
        typo = prof.get("typography", {})
        if "bitmap_font" in typo and "bitmap_thin" not in typo:
            unpinned.append(name)
    assert not unpinned, (
        f"bitmap_thin unpinned for {unpinned}: rendering will re-derive it "
        "via full-receipt bisection on every cold render. Pin the value "
        "(see tools/glyph-studio/DERIVATION_EPIC.md M0)."
    )


def test_pinned_bitmap_thin_values_are_in_range():
    """thin_ink_mask erosion saturates by 0.4-0.5; the derive path caps at 0.6.

    A pin outside [0, 0.6] is either a typo or an attempt to erode past what
    the renderer can express (VALIDATION.md: all real atlases saturate at 0.40).
    """
    bad = {}
    for name, prof in _profiles().items():
        typo = prof.get("typography", {})
        if "bitmap_thin" in typo:
            thin = typo["bitmap_thin"]
            if not isinstance(thin, (int, float)) or not 0.0 <= thin <= 0.6:
                bad[name] = thin
    assert not bad, f"bitmap_thin out of [0, 0.6]: {bad}"
