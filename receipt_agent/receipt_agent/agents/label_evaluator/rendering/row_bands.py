"""Shared y-band row grouping (P1b of the render refactor, #1188).

Four independent "cluster words into visual rows by y" implementations grew
across the codebase. Two of them are the same greedy idea with different
constants and are consolidated here:

* ``glyph_renderer._group_words_by_line`` (flat fallback): sort by center-y
  descending, chain a word onto the current row while its center is within a
  fixed tolerance of the PREVIOUS word's center -> :func:`group_rows_greedy`
  with ``reference="prev"``.
* ``render_synthetic_receipts._group_cached_words_by_line``: quantize the
  bbox top+bottom sum to a fixed step and bucket -> :func:`group_rows_quantized`.

The other two are intentionally NOT consolidated (their behavior is distinct
and load-bearing; folding them here would change rendered bytes):

* ``receipt_grid.group_words_into_grid_lines`` is overlap-aware (median-band
  overlap fraction + center fallback + source-line conflict breaking). Its
  fusion contract is what stops adjacent printed lines from chaining into one
  row; no other call site shares those semantics.
* ``font_profile._line_pitch`` groups by explicit OCR ``line_id`` to measure
  vertical PITCH (a profile statistic), not to build rows.

``glyphstudio.stylescan.group_visual_lines`` (a fifth, tools-side variant:
ascending, row-median reference, per-word tolerance) is expressible as
``group_rows_greedy(reference="row_median", strict=True)``; the tools package
keeps its own copy so pure-glyphstudio environments need not import
receipt_agent, and new measurement riders use it through stylescan.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Callable, Sequence


def group_rows_greedy(
    items: Sequence[Any],
    center: Callable[[Any], float],
    tol: float | Callable[[Any], float],
    *,
    reference: str = "prev",
    descending: bool = False,
    strict: bool = False,
) -> list[list[Any]]:
    """Greedy single-pass row grouping by y-center.

    ``items`` are sorted by ``center`` (``descending`` selects top-of-paper
    order for y-up coordinates), then each item joins the open row when its
    center is within ``tol`` of the reference:

    * ``reference="prev"``: the PREVIOUS item's center (chaining -- the
      glyph_renderer fallback contract).
    * ``reference="row_median"``: the median center of the open row (the
      stylescan contract).

    ``tol`` may be a constant or a per-item callable; ``strict`` selects
    ``<`` (stylescan) over ``<=`` (glyph_renderer) so each migrated call
    site keeps its exact boundary behavior.
    """
    if reference not in ("prev", "row_median"):
        raise ValueError(f"unknown reference {reference!r}")
    ordered = sorted(items, key=center, reverse=descending)
    rows: list[list[Any]] = []
    current: list[Any] = []
    prev_c: float | None = None
    for item in ordered:
        c = center(item)
        limit = tol(item) if callable(tol) else tol
        if reference == "prev":
            ref = prev_c
        else:
            ref = median(center(x) for x in current) if current else None
        near = (
            ref is not None
            and (abs(c - ref) < limit if strict else abs(c - ref) <= limit)
        )
        if not current or near:
            current.append(item)
        else:
            rows.append(current)
            current = [item]
        prev_c = c
    if current:
        rows.append(current)
    return rows


def group_rows_quantized(
    items: Sequence[Any],
    edge_sum: Callable[[Any], float],
    *,
    step: int = 16,
    descending: bool = True,
) -> list[list[Any]]:
    """Bucket items by quantizing the sum of their vertical edges.

    Reproduces ``_group_cached_words_by_line`` exactly: the key is
    ``round(edge_sum(item) / step) * step`` (the SUM of top+bottom, i.e.
    2x the center, so ``step=16`` is an 8-unit center grid in the cached
    0-1000 coordinate space). Buckets are returned in key order
    (``descending=True`` = top of paper first for y-up coords); items within
    a bucket keep their input order (callers sort rows themselves).
    """
    grouped: dict[int, list[Any]] = {}
    for item in items:
        key = int(round(edge_sum(item) / step) * step)
        grouped.setdefault(key, []).append(item)
    return [
        grouped[key] for key in sorted(grouped, reverse=descending)
    ]
