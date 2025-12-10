"""Utilities for rendering receipt text in receipt-space (no image warp)."""

from __future__ import annotations

from typing import Iterable, Sequence

from receipt_dynamo.entities import ReceiptLine


def _sorted_lines(lines: Iterable[ReceiptLine]) -> list[ReceiptLine]:
    return sorted(lines, key=lambda line: line.calculate_centroid()[1])


def format_receipt_text_receipt_space(
    lines: Sequence[ReceiptLine],
    highlight_line_id: int | None = None,
) -> str:
    """
    Render receipt text in receipt space, grouping visually contiguous lines.

    Lines are sorted by centroid Y (top to bottom). Consecutive lines whose
    centroid lies within the previous line's vertical span are merged onto the
    same row. Optionally brackets the text for a highlighted line_id.
    """
    if not lines:
        return ""

    sorted_lines = _sorted_lines(lines)
    formatted: list[str] = []

    for index, line in enumerate(sorted_lines):
        line_text = line.text or ""
        if highlight_line_id is not None and line.line_id == highlight_line_id:
            line_text = f"[{line_text}]"

        if index > 0:
            prev = sorted_lines[index - 1]
            centroid_y = line.calculate_centroid()[1]
            if prev.bottom_left["y"] < centroid_y < prev.top_left["y"]:
                formatted[-1] += f" {line_text}"
                continue

        formatted.append(line_text)

    return "\n".join(formatted)
