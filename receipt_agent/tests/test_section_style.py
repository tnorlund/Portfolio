"""section_scale schema: legacy float vs {height_scale, condense} mapping."""

from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    _scaled_bitmap_thin,
    _stylemap_uses_underlines,
    section_style,
)


def test_legacy_float_is_height_only():
    assert section_style(0.8) == (0.8, 1.0)


def test_mapping_carries_height_and_condense():
    assert section_style({"height_scale": 0.9, "condense": 0.85}) == (
        0.9,
        0.85,
    )


def test_mapping_defaults_missing_keys_to_noop():
    assert section_style({"condense": 0.85}) == (1.0, 0.85)
    assert section_style({"height_scale": 0.9}) == (0.9, 1.0)
    assert section_style({}) == (1.0, 1.0)


def test_date_time_header_vote_is_positional():
    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        section_for_labels,
    )

    # In the header zone (default) DATE/TIME are header typography.
    assert section_for_labels(["DATE", "TIME"]) == "HEADER"
    # Low on the paper they are transaction-block reprints, not headers.
    assert section_for_labels(["DATE"], in_header_zone=False) is None
    assert section_for_labels(["TIME"], in_header_zone=False) is None
    # Non-positional header labels still vote HEADER anywhere.
    assert (
        section_for_labels(["MERCHANT_NAME"], in_header_zone=False) == "HEADER"
    )
    # A word with another section's label keeps that section.
    assert (
        section_for_labels(["DATE", "PAYMENT_METHOD"], in_header_zone=False)
        == "PAYMENT"
    )


def test_section_style_clamps_malformed_values():
    for bad in (0, -1.0, float("nan"), float("inf"), None, "junk"):
        h, c = section_style({"height_scale": bad, "condense": bad})
        assert 0.25 <= h <= 4.0
        assert 0.2 <= c <= 2.0
    assert section_style({"condense": 0}) == (1.0, 0.2)
    assert section_style(float("nan")) == (1.0, 1.0)


def test_unlabeled_priceless_receipt_never_becomes_all_footer():
    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        GridWord,
        effective_row_sections,
    )

    def row(text, top):
        return [
            GridWord(
                left=10.0,
                top=top,
                right=200.0,
                bottom=top + 20.0,
                text=text,
                ink=(0, 0, 0),
            )
        ]

    rows = [row("GELSONS MARKET", 10.0), row("THANK YOU", 40.0)]
    assert effective_row_sections(rows) == [None, None]


def test_underline_stylemap_enables_bitmap_baseline_variation():
    assert _stylemap_uses_underlines(
        {
            "sections": {
                "item": {"underline": False},
                "section_header": {"underline": "sometimes"},
            }
        }
    )
    assert not _stylemap_uses_underlines(
        {"sections": {"item": {"underline": False}}}
    )


def test_material_bitmap_downscale_compensates_stroke_quantization():
    assert _scaled_bitmap_thin(0.225, 0.95) == 0.225
    assert _scaled_bitmap_thin(0.225, 0.85) == 0.525
    assert _scaled_bitmap_thin(0.8, 0.5) == 0.9


def test_non_positional_label_beats_date_time_everywhere():
    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        section_for_labels,
    )

    # Even in the header zone, a payment label on the word wins.
    assert section_for_labels(["DATE", "PAYMENT_METHOD"]) == "PAYMENT"
    assert (
        section_for_labels(["DATE", "PAYMENT_METHOD"], in_header_zone=False)
        == "PAYMENT"
    )
