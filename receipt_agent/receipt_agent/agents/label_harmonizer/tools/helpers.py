"""
Helper functions for label harmonizer tools.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def label_to_dict(lb: Any) -> dict:
    """
    Serialize a ReceiptWordLabel to a plain dict suitable for state/tool use.
    Includes fields required to round-trip with ReceiptWordLabel(**dict).
    """
    return {
        "image_id": str(lb.image_id),
        "receipt_id": int(lb.receipt_id),
        "line_id": int(lb.line_id),
        "word_id": int(lb.word_id),
        "label": lb.label or "",
        "reasoning": getattr(lb, "reasoning", None),
        "timestamp_added": getattr(lb, "timestamp_added", None),
        "validation_status": getattr(lb, "validation_status", None),
        "label_proposed_by": getattr(lb, "label_proposed_by", None),
        "label_consolidated_from": getattr(lb, "label_consolidated_from", None),
    }


def extract_pricing_table_from_words(words: list[dict]) -> dict:
    """
    Deterministically group words into a pricing table using geometry.

    Returns a structure with rows, columns, and cells. Filters out rows that
    don't resemble line items (no digits and no financial/line-item label).
    """
    if not words:
        return {"error": "No words found"}

    prepared = []
    for w in words:
        bb = w.get("bounding_box") or {}
        x = bb.get("x")
        y = bb.get("y")
        width = bb.get("width")
        height = bb.get("height")
        if x is None or y is None or width is None or height is None:
            # skip words without geometry; they won't help with table layout
            continue
        cx = x + width / 2
        cy = y + height / 2
        prepared.append(
            {
                "text": w.get("text", ""),
                "line_id": w.get("line_id"),
                "word_id": w.get("word_id"),
                "bbox": bb,
                "cx": cx,
                "cy": cy,
                "width": width,
                "height": height,
                "label": w.get("label"),
            }
        )

    if not prepared:
        return {"error": "No words with geometry found"}

    # Row grouping by y-center
    prepared.sort(key=lambda w: w["cy"])
    median_height = sorted([w["height"] for w in prepared])[len(prepared) // 2]
    row_tol = max(3.0, median_height * 0.75)
    rows = []
    current_row = []
    last_y = None
    for w in prepared:
        if last_y is None or abs(w["cy"] - last_y) <= row_tol:
            current_row.append(w)
            last_y = w["cy"] if last_y is None else (last_y + w["cy"]) / 2
        else:
            rows.append(current_row)
            current_row = [w]
            last_y = w["cy"]
    if current_row:
        rows.append(current_row)

    # Column inference across all rows
    x_centers = []
    for row in rows:
        for w in row:
            x_centers.append(w["cx"])
    x_centers.sort()
    median_width = sorted([w["width"] for w in prepared])[len(prepared) // 2]
    col_tol = max(5.0, median_width * 1.0)
    col_bands = []
    current_band = []
    for xc in x_centers:
        if not current_band or abs(xc - current_band[-1]) <= col_tol:
            current_band.append(xc)
        else:
            col_bands.append(current_band)
            current_band = [xc]
    if current_band:
        col_bands.append(current_band)
    columns = [{"col": i, "x_center": sum(b) / len(b)} for i, b in enumerate(col_bands)]

    # Assign columns
    def assign_col(xc: float) -> int:
        best = 0
        best_dist = float("inf")
        for c in columns:
            d = abs(xc - c["x_center"])
            if d < best_dist:
                best_dist = d
                best = c["col"]
        return best

    # Filter to line-item-like rows
    keep_rows = []
    financial_labels = {
        "UNIT_PRICE",
        "LINE_TOTAL",
        "QUANTITY",
        "PRODUCT_NAME",
        "TOTAL",
        "SUBTOTAL",
        "TAX",
    }
    for row in rows:
        has_digit = any(any(ch.isdigit() for ch in w["text"]) for w in row)
        has_fin_label = any(
            (w.get("label") or "").upper() in financial_labels for w in row
        )
        if has_digit or has_fin_label:
            keep_rows.append(row)

    structured_rows = []
    for ridx, row in enumerate(keep_rows):
        cells = []
        for w in sorted(row, key=lambda w: w["cx"]):
            cells.append(
                {
                    "row": ridx,
                    "col": assign_col(w["cx"]),
                    "text": w["text"],
                    "line_id": w["line_id"],
                    "word_id": w["word_id"],
                    "bbox": w["bbox"],
                    "label": w.get("label"),
                }
            )
        structured_rows.append({"row": ridx, "cells": cells})

    return {
        "rows": structured_rows,
        "columns": columns,
        "total_rows": len(structured_rows),
        "total_columns": len(columns),
    }
