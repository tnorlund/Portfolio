"""section_scale schema: legacy float vs {height_scale, condense} mapping."""

import pytest

from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    remap_grid_column,
    section_style,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridSpec,
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


def test_scaled_row_amount_lane_keeps_the_same_pixel_edge():
    base = GridSpec(cell_w=10.0, cell_h=18.0, font_px=12, grid_left=8.0)
    scaled = GridSpec(cell_w=12.5, cell_h=18.0, font_px=15, grid_left=8.0)

    mapped = remap_grid_column(30.0, base, scaled)

    assert mapped == 24.0
    assert base.grid_left + 30.0 * base.cell_w == (
        scaled.grid_left + mapped * scaled.cell_w
    )


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


def test_section_height_scale_affects_baseline_pitch(monkeypatch):
    from receipt_agent.agents.label_evaluator.rendering import (
        receipt_renderer as renderer,
    )

    captured = {}
    original = renderer.assign_row_baselines

    def capture(rows, ascent, min_pitch):
        captured["pitch"] = list(min_pitch)
        captured["sections"] = renderer.effective_row_sections(rows)
        return original(rows, ascent, min_pitch)

    monkeypatch.setattr(renderer, "assign_row_baselines", capture)
    words = [
        {
            "text": "ITEM",
            "bbox": [50, 790, 200, 810],
            "labels": ["PRODUCT_NAME"],
        },
        {
            "text": "1.00",
            "bbox": [750, 790, 900, 810],
            "labels": ["LINE_TOTAL"],
        },
        {"text": "RETURN", "bbox": [50, 590, 250, 610], "labels": []},
        {"text": "POLICY", "bbox": [50, 560, 250, 580], "labels": []},
        {"text": "DETAILS", "bbox": [50, 530, 250, 550], "labels": []},
    ]
    renderer.render_receipt(
        {"words": words},
        config=renderer.RenderConfig(
            width=400,
            height=600,
            grid_mode=True,
            min_font_px=20,
            max_font_px=20,
            section_scale={"FOOTER": 0.5},
        ),
        coord_max=1000,
    )

    assert captured["sections"] == ["BODY", "FOOTER", "FOOTER", "FOOTER"]
    # The transition into FOOTER still clears the preceding body row. Once
    # both neighboring rows are footer-sized, the geometric floor halves.
    assert captured["pitch"][1] == captured["pitch"][0]
    assert captured["pitch"][2] == captured["pitch"][0] * 0.5
    assert captured["pitch"][3] == captured["pitch"][0] * 0.5


def test_separator_anchors_reject_summary_and_item_count_lookalikes():
    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        GridWord,
    )
    from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
        RenderConfig,
        _separator_anchor_rows,
    )

    def row(text, top):
        words = []
        left = 10.0
        for token in text.split():
            right = left + max(10.0, len(token) * 8.0)
            words.append(
                GridWord(
                    left=left,
                    top=top,
                    right=right,
                    bottom=top + 20.0,
                    text=token,
                    ink=(0, 0, 0),
                )
            )
            left = right + 8.0
        return words

    texts = [
        "**** TOTAL 957.97",
        "TOTAL TAX 78.00",
        "TOTAL NU BE EMS SOLD - 4",
    ]
    rows = [row(text, index * 40.0) for index, text in enumerate(texts)]
    assert _separator_anchor_rows(
        rows,
        texts,
        RenderConfig(dashed_separators=True),
    ) == {0}


def test_separator_layout_uses_existing_gap_and_only_adds_missing_clearance():
    from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
        _separator_layout,
    )

    roomy, dash_ys = _separator_layout(
        [100.0, 180.0, 260.0],
        {0, 1},
        pitch=40.0,
        cap_h=36.0,
    )
    assert roomy == [100.0, 180.0, 260.0]
    assert 100.0 < dash_ys[0] < 180.0
    assert 180.0 < dash_ys[1] < 260.0

    cramped, _ = _separator_layout(
        [100.0, 130.0, 180.0],
        {0},
        pitch=40.0,
        cap_h=36.0,
    )
    # Required clearance is 50.4px, so add 20.4px—not a full 40px row.
    assert cramped[1] == pytest.approx(150.4)
    assert cramped[2] == pytest.approx(200.4)
