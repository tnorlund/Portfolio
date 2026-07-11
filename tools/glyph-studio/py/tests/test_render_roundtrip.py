"""Unit tests for the render round-trip gate.

The reconciliation core (``composed_lines`` / ``reconcile``) is pure and always
runs. A final artifact test drives the full gate on the v1 Smith's render (via
the in-repo Apple-Vision OCR) and is skipped when those files or the OCR engine
are unavailable.
"""

import json
import os

import pytest

from glyphstudio.render_roundtrip import (
    Line,
    composed_lines,
    reconcile,
    roundtrip_gate,
)


def _cand(tokens_bboxes):
    toks = [t for t, _ in tokens_bboxes]
    bbs = [b for _, b in tokens_bboxes]
    return {"tokens": toks, "bboxes": bbs}


def test_composed_lines_group_by_row():
    # two rows: y=900 (top) and y=800; each has two tokens
    cand = _cand([
        ("Your", [10, 895, 60, 915]),
        ("cashier", [70, 895, 160, 915]),
        ("SB", [10, 795, 40, 815]),
        ("SPONGE", [50, 795, 140, 815]),
    ])
    lines = composed_lines(cand)
    assert [l.text for l in lines] == ["Your cashier", "SB SPONGE"]
    assert lines[0].y > lines[1].y  # top line first


def test_clean_render_passes():
    comp = [Line("MASTERCARD 15.27", 0.5), Line("BALANCE 15.27", 0.6)]
    ocr = [Line("MASTERCARD 15.27", 0.5), Line("BALANCE 15.27", 0.6)]
    rep = reconcile(comp, ocr)
    assert rep.coverage == 1.0
    assert rep.mean_cer == 0.0
    assert not rep.duplicate_roles and not rep.render_duplicates


def test_garbled_text_raises_cer():
    comp = [Line("VERIFIED BY PIN", 0.55)]
    ocr = [Line("LFRIFIFD BY WIN", 0.55)]  # broken V/E/etc
    rep = reconcile(comp, ocr)
    assert rep.mean_cer > 0.2


def test_missing_block_lowers_coverage():
    comp = [Line("MASTERCARD Purchase", 0.55), Line("REF# 025819", 0.53)]
    ocr = [Line("MASTERCARD Purchase", 0.55)]  # REF# dropped
    rep = reconcile(comp, ocr)
    assert any("REF" in m.text for m in rep.missing)
    assert rep.coverage == 0.5


def test_duplicate_cashier_role_flagged():
    comp = [
        Line("Your cashier was Chec Sul", 0.82),
        Line("SB SPONGE 6.99", 0.80),
        Line("Your cashier was SANJA", 0.78),  # second cashier, mid-items
    ]
    ocr = [Line("Your cashier was Chec Sul", 0.82), Line("SB SPONGE 6.99", 0.80)]
    rep = reconcile(comp, ocr)
    assert rep.duplicate_roles == ["cashierx2"]


def test_render_duplicate_tagline_flagged():
    comp = [Line("FRESH", 0.98)]
    ocr = [Line("FRESH", 0.98), Line("FRESH", 0.975)]  # printed twice
    rep = reconcile(comp, ocr)
    assert "fresh" in rep.render_duplicates


def test_merge_tolerant_match():
    # OCR fuses two composed lines into one; both should still count as found
    comp = [Line("MASTERCARD", 0.55), Line("PURCHASE", 0.545)]
    ocr = [Line("MASTERCARD PURCHASE", 0.548)]
    rep = reconcile(comp, ocr)
    assert rep.coverage == 1.0


def test_punctuation_only_lines_ignored():
    comp = [Line("***", 0.33), Line("TAX 0.00", 0.30)]
    ocr = [Line("TAX 0.00", 0.30)]
    rep = reconcile(comp, ocr)
    # the asterisk row is not counted as a missing readable line
    assert rep.coverage == 1.0
    assert not rep.render_duplicates


# --- real v1 artifact (skipped when absent) --------------------------------

_V1_PNG = "/private/tmp/m6_work/out/synth_smiths_cand4_borrowed.png"
_V1_COMPOSED = "/private/tmp/m6_work/composed.json"


def test_v1_render_fails_citing_doubled_cashier():
    if not (os.path.exists(_V1_PNG) and os.path.exists(_V1_COMPOSED)):
        pytest.skip("v1 artifacts not present")
    import platform

    if platform.system() != "Darwin":
        pytest.skip("Apple Vision OCR requires macOS")
    cand = json.load(open(_V1_COMPOSED))[4]
    rep = roundtrip_gate(_V1_PNG, cand)
    assert not rep.passed
    assert "cashierx2" in rep.duplicate_roles  # seed defect #1
    assert any("cashier" in r for r in rep.reasons)
