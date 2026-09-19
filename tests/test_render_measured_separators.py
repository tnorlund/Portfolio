"""Production plumbing tests for measured separator inventories."""

from __future__ import annotations

from scripts import render_synthetic_receipts as renderer


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
