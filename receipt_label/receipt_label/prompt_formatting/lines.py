from statistics import median
from typing import List, Tuple
from receipt_dynamo.entities.receipt_line import ReceiptLine


def _line_vertical_span(line: ReceiptLine) -> Tuple[float, float]:
    """Return (bottom_y, top_y) using corner coordinates.

    Coordinates in this project commonly have larger Y near the top and smaller near the bottom.
    We compute top as the max of the two top corners' y and bottom as the min of the two bottom corners' y.
    """
    top_y = max(
        line.top_left["y"], line.top_right["y"]
    )  # visually higher row has larger y
    bottom_y = min(
        line.bottom_left["y"], line.bottom_right["y"]
    )  # visually lower row has smaller y
    return bottom_y, top_y


def _line_centroid(line: ReceiptLine) -> Tuple[float, float]:
    cx, cy = line.calculate_centroid()
    return cx, cy


def _line_height(line: ReceiptLine) -> float:
    bottom_y, top_y = _line_vertical_span(line)
    return max(0.0, top_y - bottom_y)


def _group_lines_into_rows(
    lines: List[ReceiptLine],
) -> List[List[ReceiptLine]]:
    """Group OCR lines into visual rows using Y-centroid distance, not span overlap.

    Why: Some OCR engines produce tall line boxes that overlap neighbors. Using
    span overlap chains lines together too aggressively. Centroid distance with a
    small adaptive threshold is more stable for visual rows.

    Strategy:
    - Sort by centroid Y descending (top to bottom in this coordinate system)
    - Maintain rows with a running median centroid (row_cy) and median height
    - Place a line into the first row where |cy - row_cy| <= threshold, where
      threshold = max(0.25 * row_mh, 0.25 * h, 0.002)
    """
    if not lines:
        return []

    # Sort by centroid Y descending (top first)
    lines_sorted = sorted(
        lines, key=lambda l: _line_centroid(l)[1], reverse=True
    )

    # Each row entry: (row_lines, row_centroids, row_heights)
    rows: List[Tuple[List[ReceiptLine], List[float], List[float]]] = []

    for line in lines_sorted:
        _cx, cy = _line_centroid(line)
        h = _line_height(line)
        placed = False

        for i, (row_lines, row_cys, row_hs) in enumerate(rows):
            row_cy = median(row_cys)
            row_mh = (
                median(x for x in row_hs if x > 0)
                if any(x > 0 for x in row_hs)
                else h
            )
            # Adaptive, conservative threshold
            threshold = max(0.25 * row_mh, 0.25 * h, 0.002)
            if abs(cy - row_cy) <= threshold:
                row_lines.append(line)
                row_cys.append(cy)
                row_hs.append(h)
                rows[i] = (row_lines, row_cys, row_hs)
                placed = True
                break

        if not placed:
            rows.append(([line], [cy], [h]))

    # Extract only the line lists in correct visual order (already top->bottom)
    ordered_rows = [row_lines for (row_lines, _cys, _hs) in rows]
    return ordered_rows


def format_receipt_lines_visual_order(
    receipt_lines: List[ReceiptLine],
    delimiter: str = " ",
    show_line_ids: bool = False,
) -> str:
    """Return a string with OCR lines ordered to match the image layout.

    - Groups lines by Y using centroids and span overlap
    - Sorts each row left-to-right by X centroid
    - Joins row items with a single space (configurable)
    - Optionally prefixes each rendered row with the comma-separated line_ids
    """
    rows = _group_lines_into_rows(receipt_lines)
    formatted_lines: List[str] = []
    for row in rows:
        row_sorted = sorted(row, key=lambda l: _line_centroid(l)[0])
        row_text = delimiter.join(l.text.strip() for l in row_sorted if l.text)
        if show_line_ids:
            ids = ",".join(str(l.line_id) for l in row_sorted)
            formatted_lines.append(f"{ids}: {row_text}")
        else:
            formatted_lines.append(row_text)
    return "\n".join(formatted_lines)
