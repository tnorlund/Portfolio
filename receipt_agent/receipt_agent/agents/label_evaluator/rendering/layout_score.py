"""Deterministic *rendered*-layout scorecard for the grid renderer.

The synthesis-side ``layout_integrity`` gate scores the SOURCE word boxes, so it
passes candidates that still render as garbage once the fixed-pitch grid snaps
them (fused summary rows, overprinted amounts, a wandering price column). This
module scores the ACTUAL grid placement -- the same :func:`plan_grid_line` output
the drawer renders -- so the loop can finally measure what the eye (and the Opus
judge) sees, with no font, no AWS, no image diff.

Metrics (all lower-is-better except where noted):

* ``row_merge_count`` -- rendered rows that fuse words from >1 source line
  (the `NV TAX 8.37500 ... TOTAL` collapse). Uses ``GridWord.source_line`` when
  present, else a vertical-extent heuristic (row taller than ``1.5 * cell_h``).
* ``token_overlap_count`` -- pairs of tokens in one row whose drawn column spans
  ``[start_col, end_col)`` intersect (glyph overprint).
* ``amount_col_cv`` -- coefficient of variation of the price tokens' right-edge
  columns (0.0 = every amount shares one column). ``amount_col_spread`` is the
  same signal in whole cells (max-min).
"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Sequence

from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridSpec,
    GridWord,
    plan_grid_line,
)


def _row_merges(row: Sequence[GridWord], cell_h: float) -> bool:
    """True when a grouped row fuses more than one source line."""
    lines = {
        w.source_line for w in row if getattr(w, "source_line", None) is not None
    }
    if len(lines) > 1:
        return True
    if lines:
        # Provenance present and single -> trust it, not the geometry heuristic.
        return False
    if len(row) < 2:
        return False
    extent = max(w.bottom for w in row) - min(w.top for w in row)
    return extent > 1.5 * cell_h


def score_grid_layout(
    rows: Sequence[Sequence[GridWord]], spec: GridSpec
) -> dict:
    """Score grouped+planned rows for rendered-layout pathologies.

    Args:
        rows: the output of :func:`group_words_into_grid_lines` (visual rows,
            each a left-to-right sequence of :class:`GridWord`).
        spec: the :class:`GridSpec` the renderer will draw with.

    Returns:
        ``{row_merge_count, token_overlap_count, amount_col_cv,
        amount_col_spread, row_count, amount_count}``.
    """
    row_merges = 0
    overlaps = 0
    amount_ends: list[int] = []
    for row in rows:
        if _row_merges(row, spec.cell_h):
            row_merges += 1
        placed = plan_grid_line(row, spec)
        spans = sorted((p.start_col, p.end_col) for p in placed)
        for (_, a_end), (b_start, _) in zip(spans, spans[1:]):
            if b_start < a_end:
                overlaps += 1
        amount_ends.extend(p.end_col for p in placed if p.is_price)

    if len(amount_ends) >= 2:
        mu = mean(amount_ends)
        cv = (pstdev(amount_ends) / mu) if mu else 0.0
        spread = max(amount_ends) - min(amount_ends)
    else:
        cv = 0.0
        spread = 0

    return {
        "row_merge_count": row_merges,
        "token_overlap_count": overlaps,
        "amount_col_cv": round(cv, 4),
        "amount_col_spread": spread,
        "row_count": len(rows),
        "amount_count": len(amount_ends),
    }
