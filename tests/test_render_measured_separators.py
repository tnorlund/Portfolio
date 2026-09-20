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


def test_costco_store_suffix_opts_into_measured_separators(monkeypatch):
    monkeypatch.setattr(renderer, "_MERCHANT_TRUTH_REGISTRY", object())
    monkeypatch.setattr(
        renderer,
        "get_merchant_profile_key",
        lambda _merchant: ("Costco Wholesale", {}),
    )
    inventory = [{"char": "*", "pos_frac_med": 0.5, "support": 4}]
    layout = {"separators": inventory, "columns": {}}
    assert (
        renderer.hybrid_layout_separators(
            "COSTCO WHOLESALE #1187", layout, None
        )
        == inventory
    )


def test_layout_template_flag_opts_in_without_costco_name():
    inventory = [{"char": "-", "pos_frac_med": 0.4, "support": 2}]
    layout = {"separators": inventory, "use_measured_separators": True}
    assert (
        renderer.hybrid_layout_separators("Some Mart", layout, None)
        == inventory
    )


def test_render_synthetic_receipts_has_no_module_level_glyphstudio_import():
    import ast
    from pathlib import Path

    src = Path(renderer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("glyphstudio"), alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("glyphstudio"), node.module
    assert 'os.path.join(REPO_ROOT, "tools", "glyph-studio", "py")' not in src


def test_costco_empty_layout_separators_suppress_heuristics():
    assert (
        renderer.hybrid_layout_separators(
            "Costco Wholesale", {"separators": []}, None
        )
        == []
    )
