"""Measured Merchant Truth column routing and grid placement."""

from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from receipt_agent.agents.label_evaluator.rendering import (
    receipt_grid,
    receipt_renderer,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridSpec,
    GridWord,
    build_grid_spec,
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


def test_measured_amount_lane_end_uses_the_rightmost_amount_x():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    columns = [
        {"role": "amount", "anchor": "right", "x": 0.3875},
        {"role": "amount", "anchor": "right", "x": 0.9399},
        {"role": "desc", "anchor": "left", "x": 0.01},
    ]
    lane = receipt_grid.measured_amount_lane_end(columns, spec, 760.0)
    expected = (0.9399 * 760.0 - spec.grid_left) / spec.cell_w
    assert lane == pytest.approx(expected)
    assert expected > (0.3875 * 760.0 - spec.grid_left) / spec.cell_w


def test_prefer_measured_amount_lane_falls_back_to_ocr_when_missing():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    assert (
        receipt_grid.prefer_measured_amount_lane(
            70, [{"role": "desc", "anchor": "left", "x": 0.02}], spec, 760.0
        )
        == 70
    )


def test_measured_amount_column_wins_over_ocr_amount_lane():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [_word("ITEM", 20, 70), _word("12.99", 650, 710)]
    columns = [{"role": "amount", "anchor": "right", "x": 0.80}]
    placed = plan_grid_line(
        line,
        spec,
        amount_lane=70,
        measured_columns=columns,
        paper_width=760,
    )
    by_text = {token.word.text: token for token in placed}
    assert 10 + by_text["12.99"].end_col * 10 == pytest.approx(0.80 * 760)


def test_desc_only_measured_columns_still_snap_unmatched_prices_to_lane():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    line = [_word("ITEM", 20, 70), _word("12.99", 660, 710)]
    columns = [{"role": "desc", "anchor": "left", "x": 0.02}]
    placed = plan_grid_line(
        line,
        spec,
        amount_lane=70,
        measured_columns=columns,
        paper_width=760,
    )
    by_text = {token.word.text: token for token in placed}
    assert by_text["12.99"].end_col == pytest.approx(70)


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


def test_measured_amount_lane_end_matches_measured_lane_starts():
    """The lane is the exact measured edge, like _measured_lane_starts."""
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    columns = [{"role": "amount", "anchor": "right", "x": 0.9367}]
    lane = receipt_grid.measured_amount_lane_end(columns, spec, 760.0)
    starts = receipt_grid._measured_lane_starts(
        [_word("116.99", 650, 710)], spec, columns, 760.0
    )
    assert lane == pytest.approx((0.9367 * 760.0 - spec.grid_left) / 10.0)
    assert lane == pytest.approx(starts[0] + len("116.99"))
    assert lane != round(lane)  # a rounded lane would jog half a cell


def _render_words(words, template, *, width=760, height=1000):
    sink = []
    config = receipt_renderer.RenderConfig(
        width=width,
        height=height,
        margin=10,
        grid_mode=True,
        layout_template=template,
        box_sink=sink,
    )
    receipt_renderer.render_receipt(
        {"words": words}, config=config, coord_max=1000.0
    )
    return {entry["text"]: entry for entry in sink}


def test_rows_without_measured_amount_column_keep_the_ocr_lane():
    """Only the section that measured an amount column snaps to its x.

    Default grid: cell_w 9.25px on a 760-wide canvas, so one grid cell is
    12.5 source units and a source right edge of ``col * 12.5`` lands on
    column ``col``. Three item prices jitter over columns 74/75/75 (OCR
    lane = 75); the total_line template measured its amount edge at 0.95
    of the paper (column ~77), within snap tolerance of every price.
    """

    def word(text, x0, y_top, x1, labels=()):
        return {
            "text": text,
            "line_id": int(y_top),
            "word_id": 1,
            "bbox": [x0, y_top, x1, y_top - 30],
            "labels": list(labels),
        }

    words = [
        word("ITEM", 20, 760, 120, labels=("PRODUCT_NAME",)),
        word("12.99", 862.5, 760, 925.0, labels=("LINE_TOTAL",)),
        word("ITEM", 20, 700, 120, labels=("PRODUCT_NAME",)),
        word("3.49", 887.5, 700, 937.5, labels=("LINE_TOTAL",)),
        word("ITEM", 20, 640, 120, labels=("PRODUCT_NAME",)),
        word("7.25", 887.5, 640, 937.5, labels=("LINE_TOTAL",)),
        word("TOTAL", 20, 560, 120),
        word("45.00", 875.0, 560, 937.5),
    ]
    template = {
        "sections": [
            {"name": "items", "pos_frac_med": 0.30},
            {"name": "total_line", "pos_frac_med": 0.44},
        ],
        "columns": {
            "total_line": [{"role": "amount", "anchor": "right", "x": 0.95}]
        },
    }
    boxes = _render_words(words, template)
    spec = build_grid_spec(
        None,
        760 - 20,
        1000 - 20,
        receipt_renderer.RenderConfig(margin=10, width=760, height=1000),
    )
    assert spec.cell_w == pytest.approx(9.25)

    # the measured section sits on its template edge (quarter-cell bearing)
    assert boxes["45.00"]["px"][2] == pytest.approx(
        0.95 * 760 - 0.25 * spec.cell_w, abs=0.5
    )
    # item rows have no measured column: they unify on the OCR-median lane
    # (column 75), not on the total_line template edge
    ocr_lane_px = spec.grid_left + 75 * spec.cell_w
    rights = {t: boxes[t]["px"][2] for t in ("12.99", "3.49", "7.25")}
    for text, right in rights.items():
        # render-true boxes are integer px: allow the floor
        assert right == pytest.approx(ocr_lane_px, abs=1.0), (text, right)
    assert len(set(rights.values())) == 1, rights
    assert rights["12.99"] < boxes["45.00"]["px"][2] - spec.cell_w


def test_unmeasured_section_keeps_ocr_lane_not_other_section_x():
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=16, grid_left=10.0)
    ocr_lane = 50
    items = [{"role": "amount", "anchor": "right", "x": 0.9399}]
    footer = [{"role": "desc", "anchor": "left", "x": 0.02}]
    items_lane = receipt_grid.prefer_measured_amount_lane(
        ocr_lane, items, spec, 760.0
    )
    footer_lane = receipt_grid.prefer_measured_amount_lane(
        ocr_lane, footer, spec, 760.0
    )
    assert items_lane != ocr_lane
    assert footer_lane == ocr_lane
