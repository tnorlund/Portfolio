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
