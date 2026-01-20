"""Line formatting utilities for embedding context.

This module provides functions for formatting line context for embeddings,
including visual row detection and row-based embedding formatting.

Visual Row Approach:
Apple Vision OCR often splits a single visual row into multiple ReceiptLine
entities (e.g., product name on left, price on right). This module groups
lines into visual rows based on vertical overlap, then creates embeddings
with row context (row above + target row + row below).
"""

import re
from collections.abc import Sequence
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


def group_lines_into_visual_rows(
    lines: Sequence[LineLike],
) -> list[list[LineLike]]:
    """Group ReceiptLines into visual rows based on vertical span overlap.

    Lines that vertically overlap are considered part of the same visual row.
    This handles Apple Vision OCR splitting rows into separate entities
    (e.g., "ORGANIC COFFEE" on left and "12.99" on right as separate lines).

    Args:
        lines: All lines in the receipt

    Returns:
        List of visual rows, each row being a list of lines sorted left-to-right.
        Rows are ordered top-to-bottom (highest y first).
    """
    if not lines:
        return []

    # Sort lines by y-coordinate descending (top of receipt first)
    sorted_lines = sorted(
        lines,
        key=lambda ln: -ln.bounding_box.get("y", 0),
    )

    rows: list[list[LineLike]] = []
    current_row: list[LineLike] = [sorted_lines[0]]

    # Track the y-span of the current row
    first_line = sorted_lines[0]
    current_y_min = first_line.bounding_box.get("y", 0)
    current_y_max = current_y_min + first_line.bounding_box.get("height", 0)

    for line in sorted_lines[1:]:
        line_y_min = line.bounding_box.get("y", 0)
        line_y_max = line_y_min + line.bounding_box.get("height", 0)

        # Check if this line overlaps vertically with the current row
        # Overlap: line_y_min <= current_y_max AND line_y_max >= current_y_min
        if line_y_min <= current_y_max and line_y_max >= current_y_min:
            # Same visual row - add to current row
            current_row.append(line)
            # Expand the row's y-span
            current_y_min = min(current_y_min, line_y_min)
            current_y_max = max(current_y_max, line_y_max)
        else:
            # New visual row - finalize current row and start new one
            # Sort current row left-to-right by x-coordinate
            current_row.sort(key=lambda ln: ln.bounding_box.get("x", 0))
            rows.append(current_row)

            # Start new row
            current_row = [line]
            current_y_min = line_y_min
            current_y_max = line_y_max

    # Don't forget the last row
    current_row.sort(key=lambda ln: ln.bounding_box.get("x", 0))
    rows.append(current_row)

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
    embedding in ChromaDB. Uses the first (leftmost) line's ID.

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


def parse_prev_next_from_formatted(fmt: str) -> tuple[str, str]:
    """Parse previous and next lines from formatted embedding input (DEPRECATED).

    Given a string like:
      "<TARGET>LINE TEXT</TARGET> <POS>…</POS> "
      "<CONTEXT>PREV_LINE NEXT_LINE</CONTEXT>"
    return ("PREV_LINE", "NEXT_LINE").

    Args:
        fmt: Formatted string with CONTEXT tags

    Returns:
        Tuple of (prev_line, next_line)
    """
    m = re.search(r"<CONTEXT>(.*?)</CONTEXT>", fmt)
    if not m:
        raise ValueError(f"No <CONTEXT>…</CONTEXT> in {fmt!r}")
    cont = m.group(1).strip()
    # Assuming exactly two tokens separated by whitespace
    parts = cont.split(maxsplit=1)
    prev = parts[0] if parts else "<EDGE>"
    next_line = parts[1] if len(parts) > 1 else "<EDGE>"
    return prev, next_line


def get_line_neighbors(
    target_line: LineLike, all_lines: Sequence[LineLike]
) -> tuple[str, str]:
    """Get the previous and next lines for the target line (DEPRECATED).

    This is the same logic as format_line_context_embedding_input but
    returns the neighbors directly instead of formatting them.

    New code should use group_lines_into_visual_rows() instead.

    Args:
        target_line: The line to find neighbors for
        all_lines: All lines in the receipt

    Returns:
        Tuple of (prev_line, next_line)
    """
    # Find previous and next lines by y-coordinate
    sorted_lines = sorted(all_lines, key=lambda ln: ln.calculate_centroid()[1])

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

    return prev_line, next_line
