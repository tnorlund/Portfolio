"""Font-profile → synthesis spacing fallback wiring (M3).

These tests prove that the merchant font profile (PR #994) is used ONLY as a
fallback for scaffolds that cannot measure their own item-region geometry, and
never overrides real measured geometry — so the structure gate is unaffected for
receipts that can measure themselves.
"""

from __future__ import annotations

from receipt_agent.agents.label_evaluator.merchant_synthesis import (
    MerchantAnalysis,
    MerchantLineItem,
    _font_geometry_px,
    _line_step,
    _template_fill_geometry,
)

FONT_GEOMETRY = {
    "char_width_px": 23.0,
    "font_height_px": 20.0,
    "line_step_px": 31.0,
    "price_column_x_px": 880.0,
    "char_aspect": 1.7,
}


def _bare_analysis(receipt):
    return MerchantAnalysis(
        receipt=receipt,
        line_items=[],
        subtotal=None,
        tax_total=None,
        grand_total=None,
        grand_total_line_indices=[],
    )


def test_template_geometry_uses_profile_fallback_when_unmeasurable():
    # No item-region words to measure (no line_items -> empty band), so the
    # char width / height / price column fall back to the merchant profile.
    receipt = {"lines": [], "font_geometry": FONT_GEOMETRY}
    geo = _template_fill_geometry(_bare_analysis(receipt))
    assert geo["char_w"] == 23
    assert geo["height"] == 20
    # price center (880) + half price width (3*char_w) capped at 996.
    assert geo["price_x1"] == min(880 + 3 * 23, 996)


def test_template_geometry_uses_constants_without_profile():
    receipt = {"lines": []}
    geo = _template_fill_geometry(_bare_analysis(receipt))
    assert geo["char_w"] == 16
    assert geo["height"] == 18


def test_template_geometry_prefers_measured_over_profile():
    # A measurable item band must win over the profile fallback.
    receipt = {
        "lines": [
            {
                "line_id": 0,
                "words": [
                    {"text": "BANANAS", "bbox": [80, 500, 220, 520],
                     "labels": ["PRODUCT_NAME"]},
                    {"text": "1.99", "bbox": [860, 500, 900, 520],
                     "labels": ["LINE_TOTAL"]},
                ],
            }
        ],
        "font_geometry": FONT_GEOMETRY,
    }
    item = MerchantLineItem(
        line_index=0,
        line_indices=[0],
        amount=__import__("decimal").Decimal("1.99"),
        product_text="BANANAS",
        center_y=510,
        taxable=False,
        band_line_indices=[0],
    )
    analysis = MerchantAnalysis(
        receipt=receipt,
        line_items=[item],
        subtotal=None,
        tax_total=None,
        grand_total=None,
        grand_total_line_indices=[],
    )
    geo = _template_fill_geometry(analysis)
    # measured char width = median(140/7, 40/4) = median(20, 10) = 15, which is
    # the receipt's own measurement, NOT the profile's 23.
    assert geo["char_w"] == 15
    # price column anchored on the real LINE_TOTAL center (880) + 3*char_w(15).
    assert geo["price_x1"] == 880 + 3 * 15


def test_line_step_uses_profile_fallback_only_when_opted_in():
    # Generation/layout call sites opt in -> profile pitch is used.
    receipt = {"lines": [], "font_geometry": FONT_GEOMETRY}
    assert _line_step([], receipt, allow_font_geometry_fallback=True) == 31


def test_line_step_scoring_path_ignores_profile():
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _DEFAULT_LINE_STEP,
    )

    # Default (scoring/signature) path must NOT use the profile, so the
    # structure-similarity gate and emitted evidence stay measurement-only.
    receipt = {"lines": [], "font_geometry": FONT_GEOMETRY}
    assert _line_step([], receipt) == _DEFAULT_LINE_STEP


def test_line_step_constant_without_profile():
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _DEFAULT_LINE_STEP,
    )

    assert (
        _line_step([], {"lines": []}, allow_font_geometry_fallback=True)
        == _DEFAULT_LINE_STEP
    )


def test_font_geometry_px_rejects_missing_and_nonpositive():
    assert _font_geometry_px({}, "char_width_px", default=16) == 16
    assert _font_geometry_px({"x": None}, "x", default=16) == 16
    assert _font_geometry_px({"x": 0}, "x", default=16) == 16
    assert _font_geometry_px({"x": -5}, "x", default=16) == 16
    assert _font_geometry_px({"x": 22.6}, "x", default=16) == 23
