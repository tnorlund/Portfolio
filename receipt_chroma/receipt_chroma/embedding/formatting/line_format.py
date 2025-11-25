"""Line formatting utilities for embedding context.

This module provides functions for formatting line context for embeddings,
including neighbor detection and position calculation.
"""

import re
from typing import List, Tuple

from receipt_dynamo.entities import ReceiptLine


def _get_line_position(line: ReceiptLine) -> int:
    """
    Calculate the position of a line within its receipt.

    Position is determined by sorting lines by y-coordinate (top to bottom).
    """
    # This is a simplified version - in practice, you'd need all lines
    # to calculate position. This function is typically called with
    # all_lines available.
    return 0  # Placeholder - actual implementation needs all_lines


def format_line_context_embedding_input(
    target_line: ReceiptLine, all_lines: List[ReceiptLine]
) -> str:
    """
    Format line with vertical context matching batch embedding structure.

    Replicates the format from embedding/line/submit.py:
    <TARGET>line text</TARGET> <POS>position</POS> <CONTEXT>prev_line next_line</CONTEXT>

    Args:
        target_line: The line to format
        all_lines: All lines in the receipt for context

    Returns:
        Formatted string with target, position, and context
    """
    # Calculate position using same logic as batch system
    sorted_lines = sorted(all_lines, key=lambda l: l.calculate_centroid()[1])
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


def parse_prev_next_from_formatted(fmt: str) -> Tuple[str, str]:
    """
    Parse previous and next lines from formatted embedding input.

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
    target_line: ReceiptLine, all_lines: List[ReceiptLine]
) -> Tuple[str, str]:
    """
    Get the previous and next lines for the target line.

    This is the same logic as format_line_context_embedding_input but
    returns the neighbors directly instead of formatting them.

    Args:
        target_line: The line to find neighbors for
        all_lines: All lines in the receipt

    Returns:
        Tuple of (prev_line, next_line)
    """
    # Find previous and next lines by y-coordinate
    sorted_lines = sorted(all_lines, key=lambda l: l.calculate_centroid()[1])

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
