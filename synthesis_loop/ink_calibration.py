#!/usr/bin/env python3
"""Derive ``bitmap_thin`` from measured ink density.

The hybrid renderer erodes bitmap glyphs by ``bitmap_thin``. The right amount
is whatever makes a re-render of a real receipt match that receipt's own
measured ink density. This module closes that loop with the scorecard's ink
measurers: render the real receipt's words at a candidate ``thin``, compare
per-word ink density against the real scan, and bisect.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

from receipt_line_scorecard import _word_scores

RenderFn = Callable[[float], Image.Image]


def measure_density_ratio(
    real: Image.Image,
    synth: Image.Image,
    words: Sequence[Mapping[str, Any]],
    *,
    synth_margin: int = 10,
    min_words: int = 15,
) -> float | None:
    """Median per-word synthetic/real ink density ratio."""
    scores = _word_scores(real, synth, words, synth_margin=synth_margin)
    ratios = [s["density_ratio"] for s in scores]
    if len(ratios) < min_words:
        return None
    return float(median(ratios))


def derive_bitmap_thin(
    render: RenderFn,
    real: Image.Image,
    words: Sequence[Mapping[str, Any]],
    *,
    lo: float = 0.0,
    hi: float = 0.6,
    tol: float = 0.03,
    max_iters: int = 5,
    synth_margin: int = 10,
) -> tuple[float, float] | None:
    """Solve for ``bitmap_thin`` whose render matches real ink density."""

    def ratio_at(thin: float) -> float | None:
        return measure_density_ratio(
            real, render(thin), words, synth_margin=synth_margin
        )

    ratio_lo = ratio_at(lo)
    if ratio_lo is None:
        return None
    if ratio_lo <= 1.0 + tol:
        return lo, ratio_lo
    ratio_hi = ratio_at(hi)
    if ratio_hi is None:
        return None
    if ratio_hi >= 1.0 - tol:
        return hi, ratio_hi

    best = (lo, ratio_lo)
    for _ in range(max_iters):
        mid = (lo + hi) / 2.0
        ratio_mid = ratio_at(mid)
        if ratio_mid is None:
            break
        if abs(ratio_mid - 1.0) < abs(best[1] - 1.0):
            best = (mid, ratio_mid)
        if abs(ratio_mid - 1.0) <= tol:
            return mid, ratio_mid
        if ratio_mid > 1.0:
            lo = mid
        else:
            hi = mid
    return best
