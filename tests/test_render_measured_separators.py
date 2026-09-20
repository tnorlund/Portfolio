"""Production plumbing tests for measured separator inventories."""

from __future__ import annotations

import pytest

from receipt_dynamo.data.shared_exceptions import MerchantTruthIntegrityError
from scripts import render_synthetic_receipts as renderer

# Canonical names the stub registry resolves, plus the Dynamo variants that
# resolve to them (the ACTIVE bundle's identity aliases in production).
_CANONICAL = {
    "Costco Wholesale",
    "Gelson's Westlake Village",
    "The Stand - American Classics Redefined",
    "Dollar Tree",
    "Sprouts Farmers Market",
}
_VARIANTS = {
    "COSTCO WHOLESALE #1187": "Costco Wholesale",
    "COSTCO": "Costco Wholesale",
}


def _stub_profile_key(merchant):
    name = merchant or ""
    if not name.strip():
        return None, {}
    if name in _CANONICAL:
        return name, {}
    if name in _VARIANTS:
        return _VARIANTS[name], {}
    raise MerchantTruthIntegrityError(f"no ACTIVE bundle for {name!r}")


@pytest.fixture(autouse=True)
def _stub_truth_registry(monkeypatch):
    """Resolve merchants without a live truth registry (no Dynamo)."""
    monkeypatch.setattr(
        renderer, "get_merchant_profile_key", _stub_profile_key
    )


def test_merchant_typography_passes_separator_inventory(monkeypatch):
    inventory = [{"char": "*", "pos_frac_med": 0.75, "support": 4}]
    monkeypatch.setattr(
        renderer,
        "get_merchant_profile",
        lambda _merchant: {"typography": {"separators": inventory}},
    )

    assert renderer.merchant_typography("Fixture Merchant")["separators"] == (
        inventory
    )


def test_costco_copies_layout_separators_when_opted_in():
    inventory = [{"char": "*", "pos_frac_med": 0.5, "support": 4}]
    layout = {"separators": inventory, "columns": {}}
    assert (
        renderer.hybrid_layout_separators("Costco Wholesale", layout, None)
        == inventory
    )
    assert (
        renderer.hybrid_layout_separators(
            "Costco Wholesale", layout, [{"char": "-"}]
        )
        == inventory
    )


def test_gelsons_stand_dollartree_keep_heuristic_separators():
    inventory = [{"char": "*", "pos_frac_med": 0.5, "support": 4}]
    layout = {"separators": inventory}
    for merchant in (
        "Gelson's Westlake Village",
        "The Stand - American Classics Redefined",
        "Dollar Tree",
    ):
        assert (
            renderer.hybrid_layout_separators(merchant, layout, None) is None
        )


def test_absent_vendor_flag_does_not_override_caller_separators():
    layout = {"separators": []}
    assert (
        renderer.hybrid_layout_separators(
            "Sprouts Farmers Market", layout, None
        )
        is None
    )


def test_costco_empty_layout_separators_suppress_heuristics():
    assert (
        renderer.hybrid_layout_separators(
            "Costco Wholesale", {"separators": []}, None
        )
        == []
    )


def test_missing_glyph_studio_degrades_to_no_vendor_pins(monkeypatch):
    """A checkout without tools/glyph-studio renders with heuristic rules."""
    monkeypatch.setattr(renderer, "_vendor_package", lambda: None)
    inventory = [{"char": "*", "pos_frac_med": 0.5, "support": 4}]
    layout = {"separators": inventory}
    assert renderer.vendor_uses_measured_separators("Costco Wholesale") is (
        False
    )
    assert (
        renderer.hybrid_layout_separators("Costco Wholesale", layout, None)
        is None
    )
    caller = [{"char": "-"}]
    assert (
        renderer.hybrid_layout_separators("Costco Wholesale", layout, caller)
        is caller
    )


def test_glyph_studio_path_is_not_prepended_at_import():
    studio = renderer._GLYPH_STUDIO_PY
    assert renderer.sys.path[0] != studio
    if studio in renderer.sys.path:
        # only ever appended by the lazy accessor, never inserted at 0
        assert renderer.sys.path.index(studio) > 0


def test_dynamo_variant_resolves_through_the_truth_registry():
    """The separator opt-in follows the same resolution as the profile."""
    inventory = [{"char": "*", "pos_frac_med": 0.5, "support": 4}]
    layout = {"separators": inventory, "columns": {}}
    assert renderer.vendor_uses_measured_separators("COSTCO WHOLESALE #1187")
    assert (
        renderer.hybrid_layout_separators(
            "COSTCO WHOLESALE #1187", layout, None
        )
        == inventory
    )
    record = renderer.vendor_record_for_merchant("COSTCO WHOLESALE #1187")
    assert record.get("slug") == "costco"


def test_unresolved_merchant_gets_no_vendor_record(monkeypatch):
    assert renderer.vendor_record_for_merchant("Nowhere Mart #9") == {}
    assert renderer.vendor_record_for_merchant("") == {}
    assert renderer.vendor_record_for_merchant(None) == {}
    # a variant the registry cannot resolve does not fall back to a loose
    # match on vendor.json aliases
    assert renderer.vendor_uses_measured_separators("Costco #1187") is False


def test_vendor_record_prefers_the_canonical_name(monkeypatch):
    seen = []
    package = renderer._vendor_package()
    assert package is not None
    real = package.vendor_record_for_merchant

    def spy(name):
        seen.append(name)
        return real(name)

    monkeypatch.setattr(package, "vendor_record_for_merchant", spy)
    renderer.vendor_record_for_merchant("COSTCO WHOLESALE #1187")
    assert seen[0] == "Costco Wholesale"


def test_corpus_font_inputs_solves_thin_only_when_unrecorded(monkeypatch):
    """One implementation for glyph_review and --calibrate-from-corpus."""
    calls = []
    monkeypatch.setattr(
        renderer,
        "cached_font_profile",
        lambda table, merchant, *, region, max_receipts=12, refresh=False: (
            calls.append(("profile", table, merchant, region, max_receipts))
            or "PROF"
        ),
    )

    def fake_thin(table, merchant, *, region, atlas, profile, **kwargs):
        calls.append(("thin", atlas, profile, kwargs["section_scale"]))
        return 0.27

    monkeypatch.setattr(renderer, "resolve_bitmap_thin", fake_thin)

    typ = {"bitmap_font": {"regular": "x.npz"}, "condense": 0.9}
    prof, out = renderer.corpus_font_inputs(
        "tbl",
        "Costco Wholesale",
        region="us-east-1",
        typography=typ,
        atlas="ATLAS",
        section_scale={"HEADER": 0.8},
    )
    assert prof == "PROF"
    assert out["bitmap_thin"] == 0.27
    assert "bitmap_thin" not in typ  # input not mutated
    assert calls == [
        ("profile", "tbl", "Costco Wholesale", "us-east-1", 12),
        ("thin", "ATLAS", "PROF", {"HEADER": 0.8}),
    ]

    calls.clear()
    recorded = {"bitmap_font": {"regular": "x.npz"}, "bitmap_thin": 0.1}
    _, out = renderer.corpus_font_inputs(
        "tbl", "Costco Wholesale", region="us-east-1", typography=recorded
    )
    assert out["bitmap_thin"] == 0.1
    assert calls == [("profile", "tbl", "Costco Wholesale", "us-east-1", 12)]

    calls.clear()
    _, out = renderer.corpus_font_inputs(
        "tbl", "Costco Wholesale", region="us-east-1", typography={"x": 1}
    )
    assert "bitmap_thin" not in out  # TTF merchant: nothing to solve
    assert [c[0] for c in calls] == ["profile"]
