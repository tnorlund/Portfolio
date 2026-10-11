"""Line formatting utilities for embedding context.

This module provides functions for formatting line context for embeddings,
including visual row detection and row-based embedding formatting.

Visual Row Approach:
Apple Vision OCR often splits a single visual row into multiple ReceiptLine
entities (e.g., product name on left, price on right). This module groups
lines into visual rows based on centroid overlap, then creates embeddings
with row context (row above + target row + row below).

Centroid Overlap Algorithm:
Observed quadrilaterals are projected onto a common receipt baseline before
comparing vertical spans. Lines are connected when one projected centroid
falls inside the other span, then grouped using union-find. Cornerless legacy
inputs retain their axis-aligned comparison.
"""

from collections import defaultdict
from collections.abc import Sequence
from math import isfinite
from statistics import median
from typing import Protocol, runtime_checkable


@runtime_checkable
class LineLike(Protocol):
    """Protocol defining the interface for line objects.

    This allows both ReceiptLine and test mocks to be used interchangeably.
    """

    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict[str, float]

    def calculate_centroid(self) -> tuple[float, float]:
        """Calculate the centroid coordinates of the line."""


def _row_spans(
    lines: Sequence[LineLike],
) -> tuple[list[tuple[float, float]], bool]:
    """Project observed OCR quadrilaterals onto a common receipt baseline.

    Axis-aligned boxes grow taller as text tilts and can bridge neighboring
    rows. Use the median observed edge slope so a wide, differently tilted header
    cannot outweigh the body, then compare projected quadrilaterals. Legacy
    records use their boxes; an entirely cornerless input is unchanged.
    """
    corners = []
    slopes = []
    for line in lines:
        points = [
            getattr(line, key, None)
            for key in ("top_left", "top_right", "bottom_right", "bottom_left")
        ]
        if not all(
            isinstance(p, dict)
            and all(
                isinstance(p.get(k), (int, float)) and isfinite(p[k])
                for k in ("x", "y")
            )
            for p in points
        ):
            corners.append(None)
            continue
        tl, tr, br, bl = points
        dx = tr["x"] - tl["x"]
        if (
            dx <= 0
            or br["x"] <= bl["x"]
            or tl["y"] + tr["y"] <= bl["y"] + br["y"]
        ):
            corners.append(None)
            continue
        candidate = (tr["y"] - tl["y"]) / dx
        if not isfinite(candidate):
            corners.append(None)
            continue
        corners.append(points)
        slopes.append(candidate)
    slope = median(slopes) if slopes else 0.0
    spans = []
    for line, points in zip(lines, corners, strict=True):
        if points is None:
            box = line.bounding_box
            center = box.get("y", 0) + box.get("height", 0) / 2
            center -= slope * (box.get("x", 0) + box.get("width", 0) / 2)
            half_height = box.get("height", 0) / 2
            # No oriented edges: translate the center, never widen the box.
            spans.append((center - half_height, center + half_height))
            continue
        projected = [p["y"] - slope * p["x"] for p in points]
        spans.append((min(projected), max(projected)))
    return spans, any(points is not None for points in corners)


def projected_line_centers(lines: Sequence[LineLike]) -> dict[int, float]:
    """Return reading-order centers when observed corners define a frame.

    Text-only facades and entirely cornerless records retain legacy ordering.
    """
    if not all(
        isinstance(getattr(line, "bounding_box", None), dict) for line in lines
    ):
        return {}
    spans, has_corners = _row_spans(lines)
    if not has_corners:
        return {}
    return {
        line.line_id: (low + high) / 2
        for line, (low, high) in zip(lines, spans, strict=True)
    }


def projected_row_center(
    line_ids: Sequence[int], centers: dict[int, float], fallback: float
) -> float:
    """Average in the same sequential order as Swift, including float ties."""
    if not line_ids or any(line_id not in centers for line_id in line_ids):
        return fallback
    total = 0.0
    for line_id in line_ids:
        total += centers[line_id]
    return total / len(line_ids)


def group_lines_into_visual_rows(
    lines: Sequence[LineLike],
) -> list[list[LineLike]]:
    """Group ReceiptLines into visual rows based on centroid overlap.

    Two lines share a row when one projected vertical centroid falls within
    the other projected span. Observed corners remove residual receipt tilt
    before comparison; no OCR coordinates are mutated.

    This handles Apple Vision OCR splitting rows into separate entities
    (e.g., "ORGANIC COFFEE" on left and "12.99" on right as separate lines).

    Args:
        lines: All lines in the receipt

    Returns:
        List of visual rows, each a list of lines sorted left-to-right.
        Rows are ordered top-to-bottom (highest y first).
    """
    if not lines:
        return []

    lines_list = list(lines)
    n = len(lines_list)
    spans, has_corners = _row_spans(lines_list)
    centers = (
        {
            line.line_id: (low + high) / 2
            for line, (low, high) in zip(lines_list, spans, strict=True)
        }
        if has_corners
        else {}
    )

    # Build adjacency: which lines share a row?
    # Two lines are on the same row if one's centroid falls within the other's
    # vertical span
    same_row = [[False] * n for _ in range(n)]

    for i in range(n):
        same_row[i][i] = True
        y_min_i, y_max_i = spans[i]
        cy_i = (y_min_i + y_max_i) / 2

        for j in range(i + 1, n):
            y_min_j, y_max_j = spans[j]
            cy_j = (y_min_j + y_max_j) / 2

            # Check if centroids fall within each other's spans
            i_centroid_in_j = y_min_j <= cy_i <= y_max_j
            j_centroid_in_i = y_min_i <= cy_j <= y_max_i

            if i_centroid_in_j or j_centroid_in_i:
                same_row[i][j] = True
                same_row[j][i] = True

    # Find connected components using union-find
    parent = list(range(n))

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if same_row[i][j]:
                union(i, j)

    # Group by component
    components: dict[int, list[LineLike]] = defaultdict(list)
    for i in range(n):
        components[find(i)].append(lines_list[i])

    # Sort lines within rows by x-coordinate (left to right)
    rows: list[list[LineLike]] = []
    for comp_lines in components.values():
        comp_lines.sort(key=lambda ln: ln.bounding_box.get("x", 0))
        rows.append(comp_lines)

    # Keep geometric groups and their reading order in the same frame.
    rows.sort(
        key=lambda row: -projected_row_center(
            [line.line_id for line in row],
            centers,
            sum(line.bounding_box.get("y", 0) for line in row) / len(row),
        )
    )

    return rows


def format_visual_row(row: Sequence[LineLike]) -> str:
    """Format a visual row as space-separated line texts.

    Args:
        row: Lines in the visual row, should be sorted left-to-right

    Returns:
        Space-separated concatenation of line texts
    """
    return " ".join(line.text for line in row)


def format_row_embedding_input(
    target_row: Sequence[LineLike],
    row_above: Sequence[LineLike] | None,
    row_below: Sequence[LineLike] | None,
) -> str:
    """Format embedding input with row context.

    Creates a newline-separated string with:
    - Row above (or <EDGE> if at top)
    - Target row
    - Row below (or <EDGE> if at bottom)

    Args:
        target_row: The target visual row (list of lines sorted left-to-right)
        row_above: The visual row above, or None if at top edge
        row_below: The visual row below, or None if at bottom edge

    Returns:
        Newline-separated string: "{above}\\n{target}\\n{below}"
    """
    above_text = format_visual_row(row_above) if row_above else "<EDGE>"
    target_text = format_visual_row(target_row)
    below_text = format_visual_row(row_below) if row_below else "<EDGE>"

    return f"{above_text}\n{target_text}\n{below_text}"


def get_row_embedding_inputs(
    lines: Sequence[LineLike],
) -> list[tuple[str, list[int]]]:
    """Generate embedding inputs for all visual rows in a receipt.

    Each visual row gets one embedding that includes context from the rows
    above and below.

    Args:
        lines: All lines in the receipt

    Returns:
        List of (embedding_input, line_ids) tuples where:
        - embedding_input: Formatted string for embedding
        - line_ids: List of line_id values in the target row
    """
    rows = group_lines_into_visual_rows(lines)

    if not rows:
        return []

    results: list[tuple[str, list[int]]] = []

    for i, target_row in enumerate(rows):
        row_above = rows[i - 1] if i > 0 else None
        row_below = rows[i + 1] if i < len(rows) - 1 else None

        embedding_input = format_row_embedding_input(
            target_row, row_above, row_below
        )
        line_ids = [line.line_id for line in target_row]

        results.append((embedding_input, line_ids))

    return results


def get_primary_line_id(row: Sequence[LineLike]) -> int:
    """Get the primary line_id for a visual row.

    The primary line_id is used as the unique identifier for the row's
    embedding item. Uses the first (leftmost) line's ID.

    Args:
        row: Lines in the visual row, should be sorted left-to-right

    Returns:
        The line_id of the first line in the row
    """
    if not row:
        raise ValueError("Cannot get primary line_id from empty row")
    return row[0].line_id


# =============================================================================
# Legacy functions (deprecated - kept for backward compatibility)
# =============================================================================


def format_line_context_embedding_input(
    target_line: LineLike, all_lines: Sequence[LineLike]
) -> str:
    """Format line with vertical context (DEPRECATED).

    This is the legacy format using XML tags:
    <TARGET>line text</TARGET> <POS>position</POS>
    <CONTEXT>prev_line next_line</CONTEXT>

    New code should use get_row_embedding_inputs() instead.

    Args:
        target_line: The line to format
        all_lines: All lines in the receipt for context

    Returns:
        Formatted string with target, position, and context
    """
    # Calculate position using same logic as batch system
    sorted_lines = sorted(all_lines, key=lambda ln: ln.calculate_centroid()[1])
    position = 0
    for i, line in enumerate(sorted_lines):
        if line.line_id == target_line.line_id:
            position = i
            break

    # Find previous and next lines by y-coordinate
    prev_line = "<EDGE>"
    next_line = "<EDGE>"

    target_index = None
    for i, line in enumerate(sorted_lines):
        if line.line_id == target_line.line_id:
            target_index = i
            break

    if target_index is not None:
        if target_index > 0:
            prev_line = sorted_lines[target_index - 1].text
        if target_index < len(sorted_lines) - 1:
            next_line = sorted_lines[target_index + 1].text

    return (
        f"<TARGET>{target_line.text}</TARGET> <POS>{position}</POS> "
        f"<CONTEXT>{prev_line} {next_line}</CONTEXT>"
    )
