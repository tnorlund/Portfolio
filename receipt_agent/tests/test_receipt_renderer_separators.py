from unittest.mock import patch

import pytest

from receipt_agent.agents.label_evaluator.rendering import receipt_renderer
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    build_grid_spec,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    RenderConfig,
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


def test_measured_empty_inventory_suppresses_phrase_separator_heuristics():
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
        measured_separators=(),
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
        measured_separators=(),
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


def test_nonempty_measured_inventory_draws_at_measured_fraction():
    receipt = {"words": [_word("BODY", 1, [60, 800, 300, 760])]}
    config = RenderConfig(
        width=240,
        height=360,
        margin=10,
        grid_mode=True,
        measured_separators=(
            {"char": "=", "pos_frac_med": 0.6, "support": 4},
        ),
    )

    with patch.object(receipt_renderer, "_draw_dash_row") as draw_rule:
        render_receipt(receipt, config=config, coord_max=1000.0)

    draw_rule.assert_called_once()
    assert draw_rule.call_args.kwargs["char"] == "="
    assert draw_rule.call_args.args[3] > 0.6 * config.height
