"""Measured Merchant Truth column routing and grid placement."""

from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from receipt_agent.agents.label_evaluator.rendering import receipt_grid
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridSpec,
    GridWord,
    draw_grid_line,
    effective_canonical_row_sections,
    plan_grid_line,
)

INK = (0, 0, 0)


def _word(text, left, right, *, top=100, labels=()):
    return GridWord(
        left=float(left),
        top=float(top),
        right=float(right),
        bottom=float(top + 16),
        text=text,
        ink=INK,
        labels=tuple(labels),
    )


def test_no_layout_columns_preserve_the_legacy_plan():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [_word("ITEM", 20, 70), _word("12.99", 660, 710)]

    legacy = plan_grid_line(line, spec, amount_lane=70)
    absent = plan_grid_line(
        line,
        spec,
        amount_lane=70,
        measured_columns=None,
        paper_width=760,
    )

    assert [
        (token.start_col, token.cells, token.draw_text) for token in absent
    ] == [(token.start_col, token.cells, token.draw_text) for token in legacy]


def test_measured_payment_lanes_anchor_labels_and_both_amount_edges():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [
        _word("AMOUNT:", 165, 225),
        _word("$181.60", 230, 305),
        _word("116.99", 650, 710),
    ]
    columns = [
        {"role": "label", "anchor": "left", "x": 0.2228},
        {"role": "amount", "anchor": "right", "x": 0.3875},
        {"role": "amount", "anchor": "right", "x": 0.9399},
    ]

    placed = plan_grid_line(
        line,
        spec,
        amount_lane=70,
        measured_columns=columns,
        paper_width=760,
    )
    by_text = {token.word.text: token for token in placed}

    assert 10 + by_text["AMOUNT:"].start_col * 10 == pytest.approx(
        0.2228 * 760
    )
    assert 10 + by_text["$181.60"].end_col * 10 == pytest.approx(0.3875 * 760)
    assert 10 + by_text["116.99"].end_col * 10 == pytest.approx(0.9399 * 760)


def test_measured_description_and_quantity_roles_use_their_declared_edges():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [
        _word("ITEM", 35, 85),
        _word("3", 640, 650, labels=("QUANTITY",)),
    ]
    columns = [
        {"role": "desc", "anchor": "left", "x": 0.0125},
        {"role": "qty", "anchor": "right", "x": 0.8852},
    ]

    placed = plan_grid_line(
        line,
        spec,
        measured_columns=columns,
        paper_width=760,
    )
    by_text = {token.word.text: token for token in placed}

    # The paper margin clamps a measured edge that falls just outside the
    # drawable grid; the quantity retains its measured right edge.
    assert 10 + by_text["ITEM"].start_col * 10 == pytest.approx(10)
    assert 10 + by_text["3"].end_col * 10 == pytest.approx(0.8852 * 760)


def test_measured_flag_lane_anchors_detached_flag_to_declared_left_edge():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [_word("4.29", 565, 615), _word("F", 620, 650)]
    columns = [
        {"role": "amount", "anchor": "right", "x": 0.8085},
        {"role": "flag", "anchor": "left", "x": 0.8187},
    ]

    placed = plan_grid_line(
        line,
        spec,
        measured_columns=columns,
        paper_width=760,
    )
    by_text = {token.word.text: token for token in placed}

    assert 10 + by_text["4.29"].end_col * 10 == pytest.approx(0.8085 * 760)
    assert 10 + by_text["F"].start_col * 10 == pytest.approx(0.8187 * 760)
    assert by_text["F"].measured_anchor == "left_flag"


def test_flag_lane_bearings_are_reflected_in_render_true_boxes():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [
        replace(_word("4.29", 565, 615), word_index=0),
        replace(_word("F", 620, 650), word_index=1),
    ]
    columns = [
        {"role": "amount", "anchor": "right", "x": 0.8085},
        {"role": "flag", "anchor": "left", "x": 0.8187},
    ]
    font_path = (
        Path(receipt_grid.__file__).parent / "fonts" / "B612Mono-Regular.ttf"
    )
    image = Image.new("RGB", (760, 180), "white")
    sink = []

    draw_grid_line(
        ImageDraw.Draw(image),
        line,
        100,
        spec,
        ImageFont.truetype(str(font_path), 16),
        measured_columns=columns,
        paper_width=760,
        box_sink=sink,
    )
    by_index = {placement["word_index"]: placement for placement in sink}

    assert by_index[0]["px"][2] == pytest.approx(0.8085 * 760 - spec.cell_w)
    assert by_index[1]["px"][0] == pytest.approx(0.8187 * 760 + spec.cell_w)


def test_amount_only_lane_keeps_quarter_cell_bearing():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [replace(_word("116.99", 650, 710), word_index=0)]
    columns = [{"role": "amount", "anchor": "right", "x": 0.9399}]
    font_path = (
        Path(receipt_grid.__file__).parent / "fonts" / "B612Mono-Regular.ttf"
    )
    image = Image.new("RGB", (760, 180), "white")
    sink = []

    draw_grid_line(
        ImageDraw.Draw(image),
        line,
        100,
        spec,
        ImageFont.truetype(str(font_path), 16),
        measured_columns=columns,
        paper_width=760,
        box_sink=sink,
    )

    assert sink[0]["px"][2] == pytest.approx(0.9399 * 760 - 0.25 * spec.cell_w)


def test_canonical_sections_use_labels_then_generic_payment_markers():
    rows = [
        [_word("COSTCO", 250, 360, top=40, labels=("MERCHANT_NAME",))],
        [_word("ITEM", 20, 80, top=220, labels=("PRODUCT_NAME",))],
        [_word("SUBTOTAL", 20, 100, top=340, labels=("SUBTOTAL",))],
        [_word("TOTAL", 60, 120, top=390)],
        [_word("APPROVED", 20, 100, top=450)],
        [_word("THANK", 260, 310, top=820)],
    ]
    template = {
        "sections": [
            {"name": "storefront", "pos_frac_med": 0.04},
            {"name": "items", "pos_frac_med": 0.30},
            {"name": "summary", "pos_frac_med": 0.35},
            {"name": "total_line", "pos_frac_med": 0.39},
            {"name": "payment", "pos_frac_med": 0.43},
            {"name": "footer", "pos_frac_med": 0.81},
        ]
    }

    assert effective_canonical_row_sections(rows, template, 1000) == [
        "storefront",
        "items",
        "summary",
        "total_line",
        "payment",
        "footer",
    ]
