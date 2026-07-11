"""Unit tests for the absolute per-glyph quality floor.

Two layers:
  * synthetic glyphs (deterministic, always run) exercise each check in
    isolation -- a clean form passes, its named breakage fails with the right
    ``kind``;
  * a real-atlas calibration test (skipped when the atlases are not present)
    asserts the four known-broken Smith's cold-start natives FAIL and the nine
    fleet atlases overwhelmingly PASS.
"""

import glob
import os

import numpy as np
import pytest

from glyphstudio.glyph_floor import audit_atlas, audit_glyph

H, W = 40, 30


def _kinds(defects):
    return {d.kind for d in defects}


# --- synthetic glyph builders ---------------------------------------------


def good_e():
    """Closed bowl with an enclosed counter."""
    m = np.zeros((H, W), bool)
    m[6:34, 6:24] = True
    m[12:28, 12:20] = False  # counter
    return m


def broken_e():
    """Bowl collapsed to a solid blob -- no counter."""
    m = np.zeros((H, W), bool)
    m[6:34, 6:24] = True
    return m


def good_u():
    """Two stems joined by a bowl across the base."""
    m = np.zeros((H, W), bool)
    m[4:36, 6:12] = True   # left stem
    m[4:36, 18:24] = True  # right stem
    m[30:36, 6:24] = True  # bowl joins them at the base
    return m


def broken_u():
    """Two stems that never meet at the base -- reads as two bars."""
    m = np.zeros((H, W), bool)
    m[4:36, 6:12] = True
    m[4:36, 18:24] = True
    return m


def good_E():
    m = np.zeros((H, W), bool)
    m[4:36, 4:10] = True   # left stem
    m[4:9, 4:26] = True    # top bar
    m[18:23, 4:22] = True  # middle bar
    m[31:36, 4:26] = True  # bottom bar
    return m


def broken_E():
    """Bottom bar dropped -> looks like an 'F'."""
    m = np.zeros((H, W), bool)
    m[4:36, 4:10] = True
    m[4:9, 4:26] = True
    m[18:23, 4:22] = True
    return m


def good_V():
    """Two diagonals converging to a downward apex."""
    m = np.zeros((H, W), bool)
    for i, r in enumerate(range(4, 36)):
        c = 4 + (i * 11) // 32
        m[r, c : c + 4] = True          # left diagonal moving right
        m[r, W - 4 - c - 3 : W - 4 - c + 1] = True  # right diagonal moving left
    return m


def broken_V():
    """Vertical stem + bottom foot -- an 'L' wearing a V label."""
    m = np.zeros((H, W), bool)
    m[4:36, 4:10] = True
    m[31:36, 4:26] = True
    return m


def speck():
    m = np.zeros((H, W), bool)
    m[19:21, 14:16] = True
    return m


# --- per-check synthetic tests --------------------------------------------


def test_good_glyphs_pass():
    assert audit_glyph("e", good_e()) == []
    assert audit_glyph("u", good_u()) == []
    assert audit_glyph("E", good_E()) == []
    assert audit_glyph("V", good_V()) == []


def test_no_counter_fails_e():
    assert "NO_COUNTER" in _kinds(audit_glyph("e", broken_e()))


def test_open_base_fails_u():
    assert "OPEN_BASE" in _kinds(audit_glyph("u", broken_u()))
    # a sound 'U'-topology must not trip it
    assert "OPEN_BASE" not in _kinds(audit_glyph("u", good_u()))


def test_missing_bar_fails_E():
    assert "MISSING_BAR" in _kinds(audit_glyph("E", broken_E()))


def test_no_converge_fails_V():
    assert "NO_CONVERGE" in _kinds(audit_glyph("V", broken_V()))


def test_ink_floor_fails_speck():
    assert "INK_FLOOR" in _kinds(audit_glyph("e", speck()))


def test_empty_glyph_flags():
    assert "INK_FLOOR" in _kinds(audit_glyph("o", np.zeros((H, W), bool)))


def test_checks_are_class_scoped():
    # a bar shaped like 'l' is not a closed char, so no NO_COUNTER
    bar = np.zeros((H, W), bool)
    bar[4:36, 13:17] = True
    assert "NO_COUNTER" not in _kinds(audit_glyph("l", bar))
    # ... but the same bitmap labelled 'e' must fail (it IS a closed char)
    assert "NO_COUNTER" in _kinds(audit_glyph("e", bar))


# --- real-atlas calibration (skipped when atlases absent) ------------------


def _load_npz(path):
    z = np.load(path)
    return {int(k[1:]): z[k].astype(bool) for k in z.files if k.startswith("c")}


def _find_smiths():
    for p in (
        "/private/tmp/m6_work/bitmatrix/smiths.glyphs.npz",
        "/private/tmp/m6_work/font/smiths.glyphs.npz",
    ):
        if os.path.exists(p):
            return p
    return None


def _find_fleet():
    dirs = [
        "/private/tmp/showcase_repass_overlay",
        "/Users/tnorlund/Portfolio_glyph_logo_mcp/tools/glyph-studio/.out/bitmatrix-overlay",
    ]
    out = {}
    for d in dirs:
        for p in glob.glob(os.path.join(d, "*.glyphs.npz")):
            name = os.path.basename(p)
            if "heavy" in name or "smiths" in name:
                continue
            if os.path.exists(p):  # resolves symlinks
                out.setdefault(name, p)
    return out


def test_smiths_broken_natives_fail():
    path = _find_smiths()
    if path is None:
        pytest.skip("smiths atlas not present")
    sm = _load_npz(path)
    for ch in "euEV":
        defects = audit_glyph(ch, sm[ord(ch)])
        assert defects, f"{ch!r} should FAIL the floor but passed"


def test_fleet_false_positive_rate():
    fleet = _find_fleet()
    if len(fleet) < 6:
        pytest.skip("fleet atlases not present")
    total = fp = 0
    for path in fleet.values():
        alnum = {
            cp: b for cp, b in _load_npz(path).items() if chr(cp).isalnum()
        }
        total += len(alnum)
        fp += len(audit_atlas(alnum))
    # Overwhelmingly-pass: the only real-atlas flags are genuinely counter-less
    # outlier glyphs (Home Depot 'b'/'p', Trader Joe's 'P'); keep < 2%.
    assert total > 100
    assert fp / total < 0.02, f"fleet false-positive rate {fp}/{total} too high"
