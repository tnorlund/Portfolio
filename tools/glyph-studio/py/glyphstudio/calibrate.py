"""Cheap, render-free measurement of a bitmap font's ink response.

The render-time ``bitmap_thin`` derivation (synthesis_loop/ink_calibration)
bisects by re-rendering the WHOLE receipt 5-8 times to measure one scalar ink
density. That is the dominant cost of calibrating -- and, when a profile leaves
``bitmap_thin`` unpinned, of *rendering* -- most merchants (Home Depot's
calibration render took ~10 min for exactly this reason).

This module measures the same synth-side density signal directly from the atlas
glyphs, scaled to the render cap height and eroded EXACTLY as ``BitmapFont``
does (NEAREST scale, then ``thin_ink_mask``), weighted by how often each glyph
appears in the receipt text. No receipt composite, no paper texture, no layout:
a full thin response curve costs milliseconds, not minutes. It also exposes the
erosion *saturation* -- beyond ~thin 0.4 the drop-period floors at 2 and no
further edge pixels can be removed -- which the blind bisection wastes probes on.

This is the M1 foundation of the calibration-derivation epic (DERIVATION_EPIC.md):
a cheap measurer the mint-time solver stands on. It is deliberately dependency-
light: only ``BitmapFont``/``thin_ink_mask`` from the renderer, so it can run
in the studio without the full synthesis stack.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Mapping, Sequence

import numpy as np
from PIL import Image
from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
    preserve_top_arc,
    thin_ink_mask,
)

if TYPE_CHECKING:  # pragma: no cover
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

# Beyond this thin, thin_ink_mask's drop-period (round(1/thin)) floors at 2 and
# density stops falling -- probing higher is wasted work. Kept in sync with
# thin_ink_mask's ``amount = min(0.9, amount)`` clamp / period floor.
SATURATION_THIN = 0.5

# Cap-reference glyphs (uppercase without descenders) that define cap height,
# matching BitmapFont's own cap_h derivation.
CAP_REF = "ABDEFGHKLMNPRSTUVXZ"
# The renderer clamps ocr_cap_height_ratio to this band (receipt_renderer.py).
CAP_RATIO_MIN = 0.65
CAP_RATIO_MAX = 0.95


def _rendered_glyph(
    font: "BitmapFont",
    ch: str,
    cap_px: int,
    thin: float,
    condense: float = 1.0,
) -> np.ndarray | None:
    """The eroded ink mask for one glyph at the EXACT pixels the renderer stamps.

    Mirrors ``BitmapFont.glyph``: NEAREST scale to ``cap_px`` first, then
    ``thin_ink_mask`` -- the ordering matters, since erosion works on rendered-
    size edges, not atlas-resolution edges. When ``condense < 1`` and the
    merchant enables ``condense_glyphs`` (e.g. In-N-Out at 0.7), the renderer
    resizes the thinned mask again along x before pasting (receipt_grid.py
    draw_token_chars / draw_text_run); we replicate that so the measured density
    matches the pixels actually stamped, not the un-condensed mask.
    """
    g = font.glyphs.get(ch)
    if g is None:
        return None
    scale = cap_px / font.cap_h
    h = max(1, int(round(g.shape[0] * scale)))
    w = max(1, int(round(g.shape[1] * scale)))
    im = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8)).resize(
        (w, h), Image.NEAREST
    )
    im = thin_ink_mask(im, thin, preserve_top=preserve_top_arc(ch))
    if condense < 0.999:
        im = im.resize(
            (max(1, int(round(im.width * condense))), im.height),
            Image.NEAREST,
        )
    return np.asarray(im) > 0


def _rendered_glyph_ink(
    font: "BitmapFont",
    ch: str,
    cap_px: int,
    thin: float,
    condense: float = 1.0,
) -> tuple[int, int] | None:
    """(ink_px, total_px) for one glyph at the rendered pixels."""
    arr = _rendered_glyph(font, ch, cap_px, thin, condense)
    if arr is None:
        return None
    return int(arr.sum()), int(arr.size)


def text_glyph_coverage(font: "BitmapFont", text: str) -> float:
    """Fraction of ``text``'s drawn chars present in the atlas (1.0 = full).

    The renderer stamps a TTF fallback for atlas misses rather than dropping
    them (receipt_grid.py draw_token_chars/draw_text_run), so a measurer that
    ignores misses is only unbiased at full coverage. In practice the publish
    gate requires 94/94 coverage, so calibration runs on complete fonts -- this
    lets a caller assert that rather than assume it.
    """
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 1.0
    present = sum(1 for c in chars if c in font.glyphs)
    return present / len(chars)


def text_ink_density(
    font: "BitmapFont",
    text: str,
    cap_px: int,
    thin: float,
    condense: float = 1.0,
) -> float | None:
    """Frequency-weighted ink fraction of ``text`` rendered at cap_px/thin.

    Sums ink and area over each drawn glyph weighted by its count in ``text``
    (spaces skipped) -- the per-glyph density the renderer would stamp, before
    the paper-texture pass. ``condense`` replicates the ``condense_glyphs``
    post-thin x-resize for merchants that enable it. ``None`` if no glyph in
    ``text`` is in the atlas. NOTE: atlas misses are skipped, so at less than
    full coverage this under-counts the TTF-fallback ink the renderer stamps --
    check ``text_glyph_coverage`` (the 94/94 publish gate normally guarantees
    it is 1.0).
    """
    counts = Counter(c for c in text if not c.isspace())
    ink = area = 0
    for ch, n in counts.items():
        res = _rendered_glyph_ink(font, ch, cap_px, thin, condense)
        if res is None:
            continue
        ink += res[0] * n
        area += res[1] * n
    if area == 0:
        return None
    return ink / area


def corpus_text(words: Sequence[Mapping[str, object]]) -> str:
    """Concatenate the text of OCR ``words`` (the glyph-frequency basis)."""
    return "".join(str(w.get("text") or "") for w in words)


def thin_response_curve(
    font: "BitmapFont",
    text: str,
    cap_px: int,
    thins: Sequence[float] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
    condense: float = 1.0,
) -> list[tuple[float, float]]:
    """[(thin, density)] over ``thins`` -- the full response in one cheap pass."""
    out = []
    for t in thins:
        d = text_ink_density(font, text, cap_px, t, condense)
        if d is not None:
            out.append((float(t), d))
    return out


# ``thin_ink_mask`` recomputes drops from the ORIGINAL glyph at
# period = round(1/thin), so density is a step function of thin AND is NOT
# guaranteed monotone across period boundaries (a period 3->2 change can re-add
# pixels for sparse glyphs). Distinct behavior therefore lives only at the
# period representatives; enumerate them and pick the closest, rather than
# bisecting on a false monotonicity assumption.
_SOLVE_THINS = (0.0,) + tuple(round(1.0 / p, 4) for p in range(2, 21))


def solve_thin_for_density(
    font: "BitmapFont",
    text: str,
    cap_px: int,
    target_density: float,
    *,
    condense: float = 1.0,
    thins: Sequence[float] = _SOLVE_THINS,
) -> tuple[float, float]:
    """Solve for the ``thin`` whose synth density is closest to ``target``.

    Evaluates the (cheap) density at each ``thin`` -- one per distinct
    ``thin_ink_mask`` period plateau -- and returns the ``(thin,
    achieved_density)`` closest to ``target_density``. This makes no
    monotonicity assumption (thinning is a non-monotone step function across
    period boundaries), and among equally-close plateaus prefers the SMALLEST
    thin so we never over-claim erosion. Ties on density -> least thin.
    """
    best: tuple[float, float] | None = None
    for t in sorted(set(float(x) for x in thins)):
        d = text_ink_density(font, text, cap_px, t, condense)
        if d is None:
            continue
        if (
            best is None
            or abs(d - target_density) < abs(best[1] - target_density) - 1e-12
        ):
            best = (t, d)
    if best is None:
        raise ValueError("no atlas glyphs in text; cannot measure density")
    return best


# --------------------------------------------------------------------------
# Cap height: the h_ratio knob (ocr_cap_height_ratio), derived not eyeballed.
#
# The renderer sets cap_px = median(real OCR word heights) * ocr_cap_height_ratio,
# then stamps cap glyphs scaled to cap_px -- so the rendered cap height is a
# near-linear function of cap_px, and h_ratio (synth cap height / real cap
# height) is near-linear in the ratio. The hand-tuned spread (0.649 Vons ->
# 0.95 Target) was linear correction by eye. This solves it directly: measure
# the synth cap height at a probe cap_px (cheap, from the atlas), and the real
# cap height once from the scan, then back out the ratio. No synth render.
# --------------------------------------------------------------------------


def cap_glyph_height(
    font: "BitmapFont", cap_px: int, thin: float
) -> float | None:
    """Median rendered ink-height (px) of the cap-reference glyphs at cap_px.

    This is the synth-side cap height the scorecard's ``synth_h_med`` measures,
    computed from the atlas without rendering a receipt. ``None`` if the atlas
    carries no cap-reference glyphs.
    """
    heights = []
    for ch in CAP_REF:
        arr = _rendered_glyph(font, ch, cap_px, thin)
        if arr is None or not arr.any():
            continue
        rows = np.where(arr.any(axis=1))[0]
        heights.append(int(rows.max() - rows.min() + 1))
    if not heights:
        return None
    heights.sort()
    return float(heights[len(heights) // 2])


def solve_cap_ratio(
    font: "BitmapFont",
    real_cap_height_px: float,
    median_ocr_word_height_px: float,
    thin: float = 0.0,
    *,
    probe_cap_px: int | None = None,
) -> tuple[float, float]:
    """Derive ocr_cap_height_ratio so the synth cap height matches the real scan.

    ``real_cap_height_px`` is the real receipt's measured cap-glyph ink height
    (the scorecard's real_h_med -- measured once from the scan, cheaply).
    ``median_ocr_word_height_px`` is the median OCR word-box height the renderer
    feeds into ``cap_px = round(that * ratio)``.

    Rendered cap height is linear in cap_px through the origin, so one probe
    fixes the slope: solve for the cap_px whose synth cap height equals the real
    cap height, then ``ratio = cap_px / median_ocr_word_height``. Returns
    ``(ratio, projected_h_ratio)`` where projected_h_ratio is the synth/real
    height ratio at the (clamped) returned ratio -- 1.0 when unclamped.
    """
    probe = probe_cap_px or max(6, int(round(font.cap_h)))
    synth_at_probe = cap_glyph_height(font, probe, thin)
    if synth_at_probe is None or synth_at_probe <= 0:
        raise ValueError("atlas has no cap-reference glyphs")
    if median_ocr_word_height_px <= 0:
        raise ValueError("median OCR word height must be positive")
    # synth_cap(cap_px) ~= (synth_at_probe / probe) * cap_px  -> solve = real
    slope = synth_at_probe / probe
    target_cap_px = real_cap_height_px / slope
    ratio = target_cap_px / median_ocr_word_height_px
    clamped = max(CAP_RATIO_MIN, min(CAP_RATIO_MAX, ratio))
    # projected h_ratio at the clamped ratio (1.0 if the solve wasn't clamped)
    achieved_cap_px = clamped * median_ocr_word_height_px
    projected_synth = slope * achieved_cap_px
    projected_h_ratio = projected_synth / real_cap_height_px
    return clamped, projected_h_ratio
