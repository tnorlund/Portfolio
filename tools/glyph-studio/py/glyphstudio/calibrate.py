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

Scope & units -- what this measures and what it deliberately does NOT
--------------------------------------------------------------------
The signal here is *synth-side, tight-ink* density: ink pixels over drawn-glyph
area, aggregated to match the scorecard's protocol (``scorecard_words`` +
``median_word_density`` mirror ``_word_scores`` / ``density_ratio_median``). Two
parts of the scorecard's ABSOLUTE density are layout/pipeline effects that
cannot be reproduced without a render, and are out of scope by design:

* Padded-crop denominator: ``receipt_line_scorecard.measure_ink`` divides ink by
  the padded word-crop area (word box + ``synth_margin`` + pad), so its absolute
  density is margin- and cell-spacing-dominated, not a tight-glyph fraction.
  This measurer therefore tracks the *shape* of the thin response and the
  erosion saturation point; it is not a drop-in for the scorecard's absolute
  number. Absolute agreement between the cheap solve and a real render is
  established empirically by the M1/M2 validation milestone, and the render-time
  solver remains the source of truth when they disagree.
* Stylemap faces/scales: merchants whose stylemap renders header/total/footer
  rows with a heavy face, a scale multiplier, or a double-strike stamp more ink
  than a body-``cap_px`` regular glyph. This module samples one regular face at
  one cap_px, so styled rows are measured light. Exclude those rows from the
  corpus upstream (``scorecard_words`` handles word-class filtering but not
  section styling) or calibrate them separately.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from typing import TYPE_CHECKING, Mapping, Sequence

import numpy as np
from PIL import Image

if TYPE_CHECKING:  # pragma: no cover
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

# The renderer lives in a sibling monorepo package, not a declared dependency of
# the ``glyphstudio`` package (pyproject only lists numpy/pillow). Match the
# rest of the studio tooling (compile.py._import_bitmap_font): insert the
# sibling package paths lazily and import on first use, so ``import
# glyphstudio.calibrate`` still succeeds in a clean studio env where the
# renderer is only needed when a measurement actually runs.
_WORKTREE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)


def _renderer():
    """Lazily import the renderer's ``thin_ink_mask`` / ``preserve_top_arc``."""
    for pkg in ("receipt_agent", "receipt_upload", "receipt_dynamo"):
        path = os.path.join(_WORKTREE, pkg)
        if path not in sys.path:
            sys.path.insert(0, path)
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        preserve_top_arc,
        thin_ink_mask,
    )

    return thin_ink_mask, preserve_top_arc


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


def dilate_ink(mask: np.ndarray, iters: int) -> np.ndarray:
    """Morphological dilation by ``iters`` steps (8-connected), pure numpy.

    Models stroke *weight*: the mint thickens glyph strokes before the atlas is
    frozen, so a heavier weight stamps more ink. Each step grows the ink by one
    pixel in all 8 directions; ``iters <= 0`` is the identity. This is the
    weight analog of ``thin_ink_mask``'s erosion -- density rises monotonically
    with ``iters``, which is the property the joint solver relies on.
    """
    if iters <= 0:
        return mask
    out = mask.astype(bool)
    for _ in range(int(iters)):
        d = out.copy()
        d[1:, :] |= out[:-1, :]
        d[:-1, :] |= out[1:, :]
        d[:, 1:] |= out[:, :-1]
        d[:, :-1] |= out[:, 1:]
        d[1:, 1:] |= out[:-1, :-1]
        d[1:, :-1] |= out[:-1, 1:]
        d[:-1, 1:] |= out[1:, :-1]
        d[:-1, :-1] |= out[1:, 1:]
        out = d
    return out


def effective_condense(condense: float, condense_glyphs: bool) -> float:
    """The x-resize factor the renderer applies to the glyph MASK, gated.

    Critical distinction: ``condense`` in a merchant profile always narrows the
    cell *advance* (spacing), but the renderer only resizes the glyph mask when
    ``condense_glyphs`` is enabled (receipt_grid draw_token_chars/draw_text_run).
    Target/Vons/CVS set ``condense < 1`` with ``condense_glyphs`` FALSE, so their
    masks are NOT narrowed -- measuring them condensed would understate width and
    solve the wrong density. Pass ``effective_condense(profile.condense,
    profile.condense_glyphs)`` as the ``condense`` argument to the density
    functions so the mask is only narrowed when production actually narrows it.
    """
    return float(condense) if condense_glyphs else 1.0


def _rendered_glyph(
    font: "BitmapFont",
    ch: str,
    cap_px: int,
    thin: float,
    condense: float = 1.0,
    weight_iters: int = 0,
) -> np.ndarray | None:
    """The eroded ink mask for one glyph at the EXACT pixels the renderer stamps.

    Mirrors ``BitmapFont.glyph``: NEAREST scale to ``cap_px`` first, then
    ``thin_ink_mask`` -- the ordering matters, since erosion works on rendered-
    size edges, not atlas-resolution edges.

    ``condense`` here is the EFFECTIVE mask-resize factor, i.e. already gated on
    the merchant's ``condense_glyphs`` flag (use ``effective_condense``). The
    renderer resizes the thinned mask along x before pasting ONLY when
    ``condense_glyphs`` is enabled (e.g. In-N-Out at 0.7); when it is off,
    ``condense`` affects only cell advance, not the mask, so pass ``1.0`` here.

    ``weight_iters`` models the mint's stroke-weight dilation (see ``dilate_ink``
    / ``solve_weight_and_thin``): dilation is applied AFTER scale and BEFORE
    erosion, mirroring the mint order (weight is baked into the atlas, then the
    render erodes). ``weight_iters`` is an explicit pixel count rather than a
    ``weight`` float because the weight->pixels magnitude is mint-specific and
    is calibrated at M4/M5 against the real mint; the density *shape* (monotone
    in iters) is faithful regardless. ``0`` = current render behavior.
    """
    g = font.glyphs.get(ch)
    if g is None:
        return None
    thin_ink_mask, preserve_top_arc = _renderer()
    scale = cap_px / font.cap_h
    h = max(1, int(round(g.shape[0] * scale)))
    w = max(1, int(round(g.shape[1] * scale)))
    im = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8)).resize(
        (w, h), Image.NEAREST
    )
    if weight_iters > 0:
        arr = dilate_ink(np.asarray(im) > 0, weight_iters)
        im = Image.fromarray(arr.astype(np.uint8) * 255)
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
    weight_iters: int = 0,
) -> tuple[int, int] | None:
    """(ink_px, total_px) for one glyph at the rendered pixels."""
    arr = _rendered_glyph(font, ch, cap_px, thin, condense, weight_iters)
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
    weight_iters: int = 0,
) -> float | None:
    """Frequency-weighted ink fraction of ``text`` rendered at cap_px/thin.

    Sums ink and area over each drawn glyph weighted by its count in ``text``
    (spaces skipped) -- the per-glyph density the renderer would stamp, before
    the paper-texture pass. ``condense`` replicates the ``condense_glyphs``
    post-thin x-resize for merchants that enable it. ``weight_iters`` models the
    mint's stroke-weight dilation (see ``_rendered_glyph``). ``None`` if no glyph
    in ``text`` is in the atlas. NOTE: atlas misses are skipped, so at less than
    full coverage this under-counts the TTF-fallback ink the renderer stamps --
    check ``text_glyph_coverage`` (the 94/94 publish gate normally guarantees
    it is 1.0).
    """
    counts = Counter(c for c in text if not c.isspace())
    ink = area = 0
    for ch, n in counts.items():
        res = _rendered_glyph_ink(
            font, ch, cap_px, thin, condense, weight_iters
        )
        if res is None:
            continue
        ink += res[0] * n
        area += res[1] * n
    if area == 0:
        return None
    return ink / area


def corpus_text(words: Sequence[Mapping[str, object]]) -> str:
    """Concatenate the text of OCR ``words`` (the glyph-frequency basis).

    NOTE: this is the raw char-frequency basis (every token). For a signal that
    tracks the scorecard's ``density_ratio_median`` -- which the render-time
    solver actually targets -- filter with ``scorecard_words`` and aggregate
    with ``median_word_density`` instead of taking one corpus-wide mean.
    """
    return "".join(str(w.get("text") or "") for w in words)


# The scorecard drops two word classes before measuring density
# (receipt_line_scorecard._word_scores / _is_long_numeric_caption). We mirror
# the predicates here -- rather than import the synthesis stack this module is
# deliberately independent of -- so the corpus we measure matches the tokens
# that actually contribute to ``density_ratio_median``. Spec source:
# synthesis_loop/receipt_line_scorecard.py (keep in sync if it changes).
import re as _re  # noqa: E402  (local: keep the module import surface minimal)

_LONG_NUMERIC_RE = _re.compile(r"^[\[\]\(\)\sXx0-9\-]+$")


def is_long_numeric_caption(text: str) -> bool:
    """True for barcode/UPC-style captions the scorecard excludes from density."""
    glyphs = _re.sub(r"\s+", "", text)
    digits = sum(ch.isdigit() for ch in glyphs)
    return (
        digits >= 14
        and digits >= 0.75 * max(1, len(glyphs))
        and bool(_LONG_NUMERIC_RE.match(glyphs))
    )


def scorecard_words(
    words: Sequence[Mapping[str, object]],
) -> list[str]:
    """The word texts that survive the scorecard's density-word filter.

    Drops sub-2-char words and long-numeric captions exactly as
    ``_word_scores`` does, so per-word density aggregates over the same tokens
    the scorecard scores. Styled/heavy rows (stylemap sections) are NOT handled
    here -- see the module "Scope" note; exclude them upstream if a merchant's
    stylemap renders sections with a different face/scale.
    """
    out = []
    for w in words:
        text = str(w.get("text") or "").strip()
        if len(text.replace(" ", "")) < 2:
            continue
        if is_long_numeric_caption(text):
            continue
        out.append(text)
    return out


def word_ink_densities(
    font: "BitmapFont",
    words: Sequence[str],
    cap_px: int,
    thin: float,
    condense: float = 1.0,
    weight_iters: int = 0,
) -> list[float]:
    """Per-word tight-ink density for each of ``words`` (atlas-covered glyphs).

    One density per word (ink / drawn-glyph area within that word), so a single
    long dense token cannot dominate the aggregate the way a char-frequency
    corpus mean lets it. ``weight_iters`` models the mint stroke-weight dilation.
    Words with no atlas glyphs are skipped.
    """
    out = []
    for text in words:
        d = text_ink_density(font, text, cap_px, thin, condense, weight_iters)
        if d is not None:
            out.append(d)
    return out


def median_word_density(
    font: "BitmapFont",
    words: Sequence[str],
    cap_px: int,
    thin: float,
    condense: float = 1.0,
    weight_iters: int = 0,
) -> float | None:
    """Median of per-word densities -- the scorecard's aggregation protocol.

    ``ink_calibration.measure_density_ratio`` takes ``median`` over per-word
    ratios; this mirrors that on the synth side so the cheap signal moves with
    ``density_ratio_median`` rather than an area-weighted corpus mean.
    ``weight_iters`` models the mint stroke-weight dilation. ``None`` if no word
    carries atlas glyphs.
    """
    ds = word_ink_densities(font, words, cap_px, thin, condense, weight_iters)
    if not ds:
        return None
    ds.sort()
    n = len(ds)
    if n % 2:
        return ds[n // 2]
    return (ds[n // 2 - 1] + ds[n // 2]) / 2.0


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
#
# Periods run 2..MAX so the grid reaches down to very slight erosion
# (thin = 1/MAX): each larger period is a distinct mask (drops 1/period of the
# edge pixels), so stopping early would force a merchant that only needs a hair
# of erosion onto the 0.0 or a coarser plateau. MAX=60 -> finest thin ~0.017,
# well below the scorecard's ~0.03 density tolerance; probes are milliseconds so
# the extra plateaus are free.
_MAX_SOLVE_PERIOD = 60
_SOLVE_THINS = (0.0,) + tuple(
    round(1.0 / p, 4) for p in range(2, _MAX_SOLVE_PERIOD + 1)
)


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


def solve_thin_for_word_density(
    font: "BitmapFont",
    words: Sequence[str],
    cap_px: int,
    target_density: float,
    *,
    condense: float = 1.0,
    thins: Sequence[float] = _SOLVE_THINS,
) -> tuple[float, float]:
    """``solve_thin_for_density`` over the median-of-per-word protocol.

    Same plateau-argmin search, but the measured signal is
    ``median_word_density`` (the scorecard's aggregation) rather than a single
    char-weighted corpus mean -- so the solved thin tracks
    ``density_ratio_median`` and is not skewed by one long dense token. Pass
    ``scorecard_words(words)`` so the corpus matches the tokens the scorecard
    scores. Raises ValueError if no word carries atlas glyphs.
    """
    best: tuple[float, float] | None = None
    for t in sorted(set(float(x) for x in thins)):
        d = median_word_density(font, words, cap_px, t, condense)
        if d is None:
            continue
        if (
            best is None
            or abs(d - target_density) < abs(best[1] - target_density) - 1e-12
        ):
            best = (t, d)
    if best is None:
        raise ValueError("no atlas glyphs in words; cannot measure density")
    return best


# --------------------------------------------------------------------------
# M3: derive weight + bitmap_thin JOINTLY.
#
# density_ratio (ink) is controlled by TWO coupled knobs: weight (mint stroke
# dilation, ink UP) and bitmap_thin (render erosion, ink DOWN). The epic's
# recipe: raise weight to bring density into range, then erode with thin to
# fine-tune to target. Because density is monotone-increasing in weight and
# (mostly) decreasing in thin, this is a 2-stage solve, not a 2-D search:
# choose the LEAST weight whose un-eroded density still meets-or-exceeds the
# target (so erosion can bring it down), then solve thin at that weight. This
# generalizes derive_bitmap_thin (thin only) and moves it off the render path.
#
# The solver takes an INJECTED ``density_at(weight_iters, thin) -> float | None``
# so it is agnostic to how weight/thin map to pixels: the cheap path passes the
# render-free model (weight_density_fn below); a validation path can pass a real
# render closure. weight is expressed as integer dilation ``weight_iters``; the
# iters<->weight-float magnitude is mint-specific and calibrated at M4/M5.
# --------------------------------------------------------------------------


def weight_density_fn(
    font: "BitmapFont",
    words: Sequence[str],
    cap_px: int,
    *,
    condense: float = 1.0,
):
    """Build ``density_at(weight_iters, thin)`` for ``solve_weight_and_thin``.

    The returned closure measures the render-free median-word density with the
    mint stroke-weight dilation modelled (``weight_iters`` dilation steps) and
    the render erosion applied (``thin``). Injecting it keeps the solver's
    algorithm independent of the (mint-specific) weight->pixels magnitude.
    """

    def density_at(weight_iters: int, thin: float) -> float | None:
        return median_word_density(
            font, words, cap_px, thin, condense, int(weight_iters)
        )

    return density_at


def solve_weight_and_thin(
    density_at,
    target_density: float,
    *,
    weight_iters_options: Sequence[int] = (0, 1, 2, 3, 4),
    thins: Sequence[float] = _SOLVE_THINS,
) -> tuple[int, float, float]:
    """Jointly solve (weight_iters, thin) for density closest to ``target``.

    ``density_at(weight_iters, thin) -> float | None`` is injected (cheap model
    or real render). Un-eroded density (``thin`` at its minimum) is the MOST ink
    a given weight can produce, so a weight can reach ``target`` by erosion iff
    its un-eroded density >= target. Strategy (per the epic):

    * Among weights whose un-eroded density >= target, pick the SMALLEST (least
      thickening), then argmin ``thin`` to fine-tune down to target.
    * If NO weight reaches target even un-eroded (target too dense), pick the
      HEAVIEST weight and its closest thin (best effort from below).

    Returns ``(weight_iters, thin, achieved_density)``. Raises ValueError if
    ``density_at`` yields no measurable density for any (weight, thin).
    """
    weights = sorted(set(int(w) for w in weight_iters_options))
    thin_grid = sorted(set(float(t) for t in thins))
    min_thin = thin_grid[0]

    def best_thin_at(weight: int) -> tuple[float, float] | None:
        best: tuple[float, float] | None = None
        for t in thin_grid:
            d = density_at(weight, t)
            if d is None:
                continue
            if (
                best is None
                or abs(d - target_density)
                < abs(best[1] - target_density) - 1e-12
            ):
                best = (t, d)
        return best

    # Un-eroded (max-ink) density per weight, to find which can reach target.
    reachable: list[int] = []
    heaviest_uneroded: tuple[int, float] | None = None
    for w in weights:
        d0 = density_at(w, min_thin)
        if d0 is None:
            continue
        heaviest_uneroded = (w, d0)
        if d0 >= target_density - 1e-12:
            reachable.append(w)

    if reachable:
        chosen = min(reachable)  # least thickening that can erode to target
    elif heaviest_uneroded is not None:
        chosen = heaviest_uneroded[
            0
        ]  # target too dense: heaviest, best effort
    else:
        raise ValueError("density_at yielded no measurable density")

    bt = best_thin_at(chosen)
    if bt is None:
        raise ValueError("density_at yielded no measurable density")
    thin, achieved = bt
    return chosen, thin, achieved


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


def renderer_cap_px(
    cap_ratio: float,
    median_ocr_word_height_px: float,
    *,
    font_px: float | None = None,
    bitmap_cap_ratio: float | None = None,
    max_font_px: float | None = None,
) -> int:
    """The ``cap_px`` the renderer stamps, reproducing _derive_bitmap_metrics.

    ``measured_cap = round(median * cap_ratio)``, floored at
    ``round(base_cap * 0.9)`` (``base_cap = round(font_px * bitmap_cap_ratio)``,
    the small-text floor) and capped at ``max_font_px`` (the large-text ceiling).
    Floor/ceiling only apply when their inputs are given; otherwise this is the
    unclamped rounded linear cap_px. Shared by ``solve_cap_ratio`` (h_ratio
    projection) and ``calibrate_merchant`` (so the density solve runs at the
    exact size production renders).
    """
    cap_px = int(round(cap_ratio * median_ocr_word_height_px))
    if font_px is not None and bitmap_cap_ratio is not None:
        base_cap = max(6, int(round(float(font_px) * float(bitmap_cap_ratio))))
        cap_px = max(int(round(base_cap * 0.9)), cap_px)  # small-text floor
    if max_font_px is not None:
        cap_px = min(max(6, int(max_font_px)), cap_px)  # large-text ceiling
    return cap_px


def solve_cap_ratio(
    font: "BitmapFont",
    real_cap_height_px: float,
    median_ocr_word_height_px: float,
    thin: float = 0.0,
    *,
    probe_cap_px: int | None = None,
    font_px: float | None = None,
    bitmap_cap_ratio: float | None = None,
    max_font_px: float | None = None,
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
    height ratio the RENDERER will actually produce at the returned ratio --
    1.0 when nothing binds.

    The renderer does more than clamp the ratio (receipt_renderer.py
    _derive_bitmap_metrics): it rounds ``measured_cap = round(median * ratio)``,
    floors it at ``round(base_cap * 0.9)`` where ``base_cap = round(font_px *
    bitmap_cap_ratio)`` (binds for small text), and caps it at ``max_font_px``
    (binds for large text). Pass ``font_px``/``bitmap_cap_ratio``/``max_font_px``
    to project through that exact cap_px so we don't advertise an h_ratio the
    renderer can't reach when a floor/ceiling binds; omit them to project the
    unclamped linear cap_px (rounding still applied).
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
    # Reproduce the renderer's cap_px so the projection matches what it stamps.
    cap_px = renderer_cap_px(
        clamped,
        median_ocr_word_height_px,
        font_px=font_px,
        bitmap_cap_ratio=bitmap_cap_ratio,
        max_font_px=max_font_px,
    )
    projected_synth = slope * cap_px
    projected_h_ratio = projected_synth / real_cap_height_px
    return clamped, projected_h_ratio


# --------------------------------------------------------------------------
# M4: calibrate_merchant -- one call emits the full calibrated typography block.
#
# Composes the three solves (all render-free) into the block ADD_MERCHANT.md
# step 7 used to reach by eye: ocr_cap_height_ratio (M2), then weight+bitmap_thin
# jointly (M3) at the exact cap_px the renderer will stamp. The REAL-side
# measurements (cap height, median OCR word height, target density) are measured
# once from the scans by the caller/scorecard and injected -- this stays pure and
# render-free; the render-time solver is only needed for the M5 validation pass.
# --------------------------------------------------------------------------


def calibrate_merchant(
    font: "BitmapFont",
    receipts: "Sequence[Mapping[str, object]]",
    *,
    real_cap_height_px: float,
    median_ocr_word_height_px: float,
    target_density: float,
    condense: float = 1.0,
    condense_glyphs: bool = False,
    weight_iters_options: "Sequence[int]" = (0, 1, 2, 3, 4),
    cap_probe_thin: float = 0.0,
    font_px: "float | None" = None,
    bitmap_cap_ratio: "float | None" = None,
    max_font_px: "float | None" = None,
    provenance: "str | None" = None,
) -> dict:
    """Derive ``{ocr_cap_height_ratio, weight_iters, bitmap_thin}`` in one call.

    ``receipts`` supplies the corpus (each item is an OCR receipt dict with a
    ``words`` list of ``{"text": ...}``); it is filtered through
    ``scorecard_words`` so calibration measures the same tokens the scorecard
    scores. The real-side scalars are measured once from the scans and injected:

    * ``real_cap_height_px`` -- the scorecard's real_h_med (real cap ink height).
    * ``median_ocr_word_height_px`` -- median OCR word-box height (the renderer's
      ``cap_px = round(that * ratio)`` input).
    * ``target_density`` -- the real receipts' median per-word density (the
      value the synth density must match; scorecard density_ratio -> 1.0).

    Returns a dict with the solved knobs plus ``projected`` (h_ratio + achieved
    density the cheap model predicts), ``cap_px``, ``coverage`` (assert 1.0 for
    an unbiased solve; see ``text_glyph_coverage``), and ``provenance``. The
    weight is reported as ``weight_iters`` (dilation steps) -- mapping it to the
    profile's ``weight`` float is the mint-specific step done at M5.

    Raises ValueError if the corpus has no scorecard-eligible words with atlas
    glyphs, or the atlas has no cap-reference glyphs.
    """
    words = scorecard_words(
        [w for r in receipts for w in (r.get("words") or [])]
    )
    if not words:
        raise ValueError("no scorecard-eligible words in receipts")

    # M2: cap-height ratio, then the exact cap_px the renderer will stamp.
    ratio, h_ratio = solve_cap_ratio(
        font,
        real_cap_height_px,
        median_ocr_word_height_px,
        thin=cap_probe_thin,
        font_px=font_px,
        bitmap_cap_ratio=bitmap_cap_ratio,
        max_font_px=max_font_px,
    )
    cap_px = renderer_cap_px(
        ratio,
        median_ocr_word_height_px,
        font_px=font_px,
        bitmap_cap_ratio=bitmap_cap_ratio,
        max_font_px=max_font_px,
    )

    # M3: weight + bitmap_thin jointly, measured at that cap_px.
    eff_condense = effective_condense(condense, condense_glyphs)
    density_at = weight_density_fn(font, words, cap_px, condense=eff_condense)
    weight_iters, thin, achieved = solve_weight_and_thin(
        density_at,
        target_density,
        weight_iters_options=weight_iters_options,
    )

    coverage = text_glyph_coverage(font, "".join(words))
    n = len(list(receipts))
    return {
        "ocr_cap_height_ratio": ratio,
        "weight_iters": weight_iters,
        "bitmap_thin": thin,
        "cap_px": cap_px,
        "projected": {
            "h_ratio": h_ratio,
            "density": achieved,
            "target_density": target_density,
        },
        "coverage": coverage,
        "provenance": provenance or f"fleet-derived over {n} receipts",
    }
