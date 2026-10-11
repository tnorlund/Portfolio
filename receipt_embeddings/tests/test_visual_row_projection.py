"""Geometry invariants; private receipt replays provide acceptance evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from receipt_embeddings.formatting.line_format import (
    group_lines_into_visual_rows,
)


def line(line_id, x, width, baseline, slope, corners=True):
    height = 0.012
    points = {
        "top_left": {"x": x, "y": baseline + slope * x + height},
        "top_right": {
            "x": x + width,
            "y": baseline + slope * (x + width) + height,
        },
        "bottom_right": {
            "x": x + width,
            "y": baseline + slope * (x + width),
        },
        "bottom_left": {"x": x, "y": baseline + slope * x},
    }
    ys = [p["y"] for p in points.values()]
    return SimpleNamespace(
        line_id=line_id,
        bounding_box={
            "x": x,
            "y": min(ys),
            "width": width,
            "height": max(ys) - min(ys),
        },
        **(points if corners else {}),
    )


@pytest.mark.parametrize("slope", [-0.12, 0.0, 0.12])
def test_slanted_names_pair_with_their_own_amount_column(slope):
    lines = [
        line(1, 0.1, 0.5, 0.7, slope),
        line(2, 0.8, 0.05, 0.7, slope),
        line(3, 0.1, 0.5, 0.66, slope),
        line(4, 0.8, 0.05, 0.66, slope),
    ]
    before = deepcopy(lines)
    assert [
        [member.line_id for member in row]
        for row in group_lines_into_visual_rows(lines)
    ] == [[1, 2], [3, 4]]
    assert lines == before


def test_cornerless_legacy_geometry_keeps_axis_aligned_rows():
    lines = [
        line(1, 0.1, 0.5, 0.7, 0, corners=False),
        line(2, 0.8, 0.05, 0.7, 0, corners=False),
        line(3, 0.1, 0.5, 0.66, 0, corners=False),
    ]
    assert [
        [member.line_id for member in row]
        for row in group_lines_into_visual_rows(lines)
    ] == [[1, 2], [3]]


def test_mixed_legacy_amount_uses_the_same_projection_frame():
    lines = [
        line(1, 0.1, 0.5, 0.7, 0.12),
        line(2, 0.8, 0.05, 0.7, 0.12, corners=False),
        line(3, 0.1, 0.5, 0.66, 0.12),
    ]
    assert [
        [member.line_id for member in row]
        for row in group_lines_into_visual_rows(lines)
    ] == [[1, 2], [3]]


def test_cornerless_wide_line_is_not_widened_by_projection():
    lines = [
        line(1, 0.1, 0.5, 0.7, 0.04, corners=False),
        line(2, 0.8, 0.05, 0.7, 0.04),
        line(3, 0.1, 0.5, 0.675, 0.04),
        line(4, 0.8, 0.05, 0.675, 0.04),
    ]
    assert [
        [member.line_id for member in row]
        for row in group_lines_into_visual_rows(lines)
    ] == [[1, 2], [3, 4]]


def test_wide_tilted_header_does_not_outweigh_horizontal_body():
    lines = [
        line(0, 0.05, 0.8, 0.85, 0.04),
        line(1, 0.1, 0.2, 0.7, 0),
        line(2, 0.8, 0.05, 0.7, 0),
        line(3, 0.1, 0.2, 0.675, 0),
        line(4, 0.8, 0.05, 0.675, 0),
    ]
    assert [
        [member.line_id for member in row]
        for row in group_lines_into_visual_rows(lines)
    ] == [[0], [1, 2], [3, 4]]
