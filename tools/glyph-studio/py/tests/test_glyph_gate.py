"""Unit tests for the fleet-referenced glyph identity gate (synthetic)."""

import numpy as np

from glyphstudio.glyph_gate import Finding, audit_normalized, hole_count

S = 20


def ring():
    m = np.zeros((S, S), bool)
    m[2:18, 2:18] = True
    m[6:14, 6:14] = False
    return m


def bar():
    m = np.zeros((S, S), bool)
    m[2:18, 9:12] = True
    return m


def double_ring():
    m = np.zeros((S, S), bool)
    m[1:19, 4:16] = True
    m[4:8, 7:13] = False
    m[12:16, 7:13] = False
    return m


def test_hole_count():
    assert hole_count(ring()) == 1
    assert hole_count(bar()) == 0
    assert hole_count(double_ring()) == 2


def _fleet(broken_merchant_glyph=None):
    """5 merchants; 'O' = ring, 'l' = bar everywhere. Optionally break m0."""
    fleet = {}
    for i in range(5):
        g = {ord("O"): ring(), ord("l"): bar(), ord("5"): double_ring()}
        fleet[f"m{i}"] = g
    if broken_merchant_glyph:
        merchant, ch, glyph = broken_merchant_glyph
        fleet[merchant][ord(ch)] = glyph
    return fleet


def test_misrender_flags_bar_shaped_O():
    # m0's 'O' is actually a bar -> should read as fleet 'l' (holes match: 0)
    fleet = _fleet(("m0", "O", bar()))
    findings = audit_normalized(fleet, chars="Ol5")
    mis = [f for f in findings if f.kind == "MISRENDER"]
    assert any(
        f.merchant == "m0" and f.char == "O" and "'l'" in f.detail for f in mis
    ), findings


def test_topology_suppresses_hole_incompatible_confusion():
    # m0's 'O' is a slightly odd ring (still 1 hole): must NOT be flagged as
    # reading like 'l' (0 holes) even if IoU to 'l' were high.
    odd = ring()
    odd[2:18, 2:5] = True  # thicken one side
    fleet = _fleet(("m0", "O", odd))
    findings = audit_normalized(fleet, chars="Ol5")
    assert not any(
        f.kind == "MISRENDER" and f.merchant == "m0" and f.char == "O" for f in findings
    ), findings


def test_case_pair_not_confused():
    # give everyone an 'o' identical to 'O': no O<->o misrender flags
    fleet = _fleet()
    for g in fleet.values():
        g[ord("o")] = ring()
    findings = audit_normalized(fleet, chars="Oo l5".replace(" ", ""))
    assert not any(f.kind == "MISRENDER" for f in findings), findings


def test_missing_flagged_with_fleet_support():
    fleet = _fleet()
    del fleet["m3"][ord("5")]
    findings = audit_normalized(fleet, chars="Ol5")
    missing = [f for f in findings if f.kind == "MISSING"]
    assert any(f.merchant == "m3" and f.char == "5" for f in missing), findings


def test_clean_fleet_no_findings():
    findings = audit_normalized(_fleet(), chars="Ol5")
    assert findings == [] or all(f.kind == "MISSING" for f in findings), findings
