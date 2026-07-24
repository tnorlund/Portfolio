"""Tests for authoritative measured separator inventories."""

from unittest.mock import patch

import pytest

from receipt_agent.agents.label_evaluator.rendering import receipt_renderer
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridWord,
    build_grid_spec,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    RenderConfig,
    _inferred_policy_separator_baselines,
    render_receipt,
)


def _word(text, line_id, bbox):
    return {
        "text": text,
        "line_id": line_id,
        "word_id": 1,
        "bbox": bbox,
        "labels": [],
    }


def test_none_inventory_preserves_legacy_phrase_separator_heuristics():
    receipt = {
        "words": [
            _word("TOTAL", 1, [60, 800, 300, 760]),
            _word("1.00", 1, [700, 800, 940, 760]),
            _word("NEXT", 2, [60, 700, 300, 660]),
        ]
    }
    config = RenderConfig(
        width=240,
        height=360,
        margin=10,
        grid_mode=True,
        dashed_separators=True,
        separators=None,
    )

    with patch.object(receipt_renderer, "_draw_dash_row") as draw_rule:
        render_receipt(receipt, config=config, coord_max=1000.0)

    draw_rule.assert_called_once()


def test_empty_inventory_suppresses_phrase_separator_heuristics():
    receipt = {
        "words": [
            _word("TOTAL", 1, [60, 800, 300, 760]),
            _word("1.00", 1, [700, 800, 940, 760]),
            _word("NEXT", 2, [60, 700, 300, 660]),
        ]
    }
    config = RenderConfig(
        width=240,
        height=360,
        margin=10,
        grid_mode=True,
        dashed_separators=True,
        separators=(),
    )

    with patch.object(receipt_renderer, "_draw_dash_row") as draw_rule:
        render_receipt(receipt, config=config, coord_max=1000.0)

    draw_rule.assert_not_called()


def test_literal_rule_uses_source_y_with_measured_layout():
    words = [
        _word(
            f"ROW{i}",
            i,
            [60, 900 - i * 12, 300, 875 - i * 12],
        )
        for i in range(20)
    ]
    star_bbox = [40, 250, 950, 235]
    words.append(_word("*" * 20, 99, star_bbox))
    receipt = {"words": words}
    config = RenderConfig(
        width=240,
        height=360,
        margin=10,
        grid_mode=True,
        separators=(),
    )
    captured = []

    def record_rule(*args, **kwargs):
        if str(args[1]).startswith("*"):
            captured.append(args[3])

    with patch.object(receipt_renderer, "draw_token_chars", record_rule):
        render_receipt(receipt, config=config, coord_max=1000.0)

    assert len(captured) == 1
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    sizing = build_grid_spec(None, inner_w, inner_h, config)
    font = receipt_renderer._load_grid_font(sizing.font_px, config)
    ascent, _descent = font.getmetrics()
    source_box = receipt_renderer._to_pixel_box(
        star_bbox, 1000.0, config, inner_w, inner_h
    )
    assert source_box is not None
    assert captured[0] == pytest.approx(source_box[1] + ascent)


def test_nonempty_inventory_draws_at_measured_fraction():
    receipt = {"words": [_word("BODY", 1, [60, 800, 300, 760])]}
    config = RenderConfig(
        width=240,
        height=360,
        margin=10,
        grid_mode=True,
        separators=({"char": "=", "pos_frac_med": 0.6, "support": 4},),
    )

    with patch.object(receipt_renderer, "_draw_dash_row") as draw_rule:
        render_receipt(receipt, config=config, coord_max=1000.0)

    draw_rule.assert_called_once()
    assert draw_rule.call_args.kwargs["char"] == "="
    assert draw_rule.call_args.args[3] > 0.6 * config.height


def _row(text: str) -> list[GridWord]:
    return [
        GridWord(
            left=20.0,
            top=680.0,
            right=300.0,
            bottom=700.0,
            text=text,
            ink=(0, 0, 0),
        )
    ]


def test_infers_rule_in_blank_row_before_lower_policy_copy() -> None:
    result = _inferred_policy_separator_baselines(
        [_row("by email at Grocer.example"), _row("Please keep your receipt")],
        ["HEADER", "FOOTER"],
        [700.0, 760.0],
        min_pitch=30.0,
        cap_h=20.0,
        content_top=10.0,
        content_height=1000.0,
    )

    assert result == [740.0]


def test_does_not_infer_rule_without_website_boundary_or_blank_row() -> None:
    common = {
        "sections": ["HEADER", "FOOTER"],
        "min_pitch": 30.0,
        "cap_h": 20.0,
        "content_top": 10.0,
        "content_height": 1000.0,
    }

    no_website = _inferred_policy_separator_baselines(
        [_row("save money save paper"), _row("Please keep your receipt")],
        baselines=[700.0, 760.0],
        **common,
    )
    no_blank_row = _inferred_policy_separator_baselines(
        [_row("visit Grocer.example"), _row("Please keep your receipt")],
        baselines=[700.0, 740.0],
        **common,
    )

    assert no_website == []
    assert no_blank_row == []


def test_does_not_infer_rule_in_top_header() -> None:
    result = _inferred_policy_separator_baselines(
        [_row("visit Grocer.example"), _row("Please keep your receipt")],
        ["HEADER", "FOOTER"],
        [300.0, 360.0],
        min_pitch=30.0,
        cap_h=20.0,
        content_top=10.0,
        content_height=1000.0,
    )

    assert result == []
