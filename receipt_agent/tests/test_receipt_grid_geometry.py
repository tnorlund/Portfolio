"""Geometry contracts for mixed-layout receipt rows."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from receipt_agent.agents.label_evaluator.rendering import receipt_grid
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridSpec,
    GridWord,
    PlacedToken,
    _right_align_source_segments,
    draw_text_run,
)


def _grid_word(
    text: str,
    left: float,
    right: float,
    *,
    word_index: int | None = None,
) -> GridWord:
    return GridWord(
        left=left,
        top=20.0,
        right=right,
        bottom=40.0,
        text=text,
        ink=(0, 0, 0),
        word_index=word_index,
    )


def test_source_column_segment_preserves_each_word_start() -> None:
    """A compact glyph face must not collapse a measured source column."""
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=18, grid_left=0.0)
    placed = [
        PlacedToken(_grid_word("Station:", 0.0, 80.0), 0, 8, False),
        PlacedToken(_grid_word("9033-03", 90.0, 160.0), 9, 7, False),
        # The right column starts after a large measured source gap. These
        # compact placements reproduce the old cursor-relative collapse.
        PlacedToken(_grid_word("Sales", 480.0, 570.0), 34, 5, False),
        PlacedToken(_grid_word("Rep", 575.0, 640.0), 40, 3, False),
        PlacedToken(_grid_word("LCOLE", 645.0, 745.0), 44, 5, False),
    ]

    _right_align_source_segments(placed, spec)

    for token in placed[2:]:
        rendered_left = spec.grid_left + token.start_col * spec.cell_w
        assert abs(rendered_left - token.word.left) <= spec.cell_w / 2


def test_text_run_can_recover_observed_source_span() -> None:
    """Mixed prose rows may be substantially wider than the base cell grid."""
    image = Image.new("RGB", (600, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(
        str(
            Path(receipt_grid.__file__).parent
            / "fonts"
            / "B612Mono-Regular.ttf"
        ),
        18,
    )
    spec = GridSpec(cell_w=10.0, cell_h=20.0, font_px=18, grid_left=0.0)
    texts = ["Number", "of", "items", "purchased:", "2"]
    words = [
        _grid_word(text, 0.0, 1.0, word_index=index)
        for index, text in enumerate(texts)
    ]
    boxes: list[dict] = []

    draw_text_run(
        draw,
        " ".join(texts),
        20.0,
        70.0,
        spec,
        font,
        (0, 0, 0),
        cap_px=18,
        target_width=480.0,
        box_sink=boxes,
        sink_words=words,
    )

    assert boxes[-1]["px"][2] - boxes[0]["px"][0] >= 450.0
