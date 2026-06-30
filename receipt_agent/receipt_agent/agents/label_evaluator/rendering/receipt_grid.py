"""Fixed-character-grid typography for synthetic thermal receipts.

The legacy renderer (:mod:`receipt_renderer`) fits a font to *each word box*
independently, so a long item name and a two-digit total on the same receipt
end up at wildly different point sizes, prices drift off their column, and the
baseline wobbles line to line. Real thermal / dot-matrix printers do the
opposite: one fixed-pitch face, ONE cell size, every glyph snapped to a column
on a shared baseline. This module reproduces that.

The flow is:

* :func:`build_grid_spec` turns a :class:`MerchantFontProfile` (normalized 0-1
  geometry) plus the inner canvas size into a concrete cell grid: ``cell_w``
  (character advance), ``cell_h`` (line pitch), ``font_px`` (one body size) and
  ``grid_left`` (the column-0 origin in pixels).
* :func:`group_words_into_grid_lines` clusters already-pixel-positioned words
  into visual rows.
* :func:`draw_token_chars` places EACH character of a token at
  ``grid_left + col * cell_w`` (``col`` derived from the glyph's own left edge)
  on a shared baseline, with anti-aliasing off so glyphs read as hard dots.

Everything here is pure geometry + drawing; it shares the legacy renderer's
coordinate helpers and font loader so the two paths stay consistent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median
from typing import Any, Sequence

from PIL import ImageDraw, ImageFont

# A right-aligned amount token (optional currency/sign, 2-decimal tail, optional
# trailing sign/tax flag). These share a right edge in a price column, so we snap
# their RIGHT edge to the grid; everything else snaps its left edge.
# The integer part accepts EITHER comma-grouped digits (``1,234``) OR a plain run
# of digits (``1000``, ``1234``); without the bare-``\d+`` alternative an
# uncommaed large amount falls through to left-snap and breaks the column.
_PRICE_TOKEN = re.compile(
    r"^[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}[-+]?[A-Z]?$"
)

# Hard floor/ceiling on the body font, independent of any per-merchant noise.
# The realism control is the profile geometry; these only stop a degenerate
# profile from producing an unreadable (<9px) or comically large (>28px) face.
_GRID_FONT_MIN = 9
_GRID_FONT_MAX = 28

# Fallback normalized geometry when a merchant profile is missing/degenerate.
# ~1.2% of receipt width per char and ~1.8% of receipt height per line is a
# typical thermal body size; enough to land inside the [9, 28] px clamp.
_FALLBACK_CHAR_WIDTH = 0.0125
_FALLBACK_FONT_HEIGHT = 0.018


@dataclass(frozen=True)
class GridSpec:
    """Concrete pixel grid derived from a merchant profile + canvas size."""

    cell_w: float
    cell_h: float
    font_px: int
    grid_left: float


def build_grid_spec(
    profile: Any | None,
    inner_w: int,
    inner_h: int,
    config: Any,
    *,
    char_advance_px: float | None = None,
) -> GridSpec:
    """Derive the fixed character grid for a receipt.

    Args:
        profile: :class:`MerchantFontProfile` (or ``None``). Supplies the
            normalized ``font_height``, ``char_width`` and ``line_pitch``.
        inner_w / inner_h: drawable area in pixels (canvas minus margins).
        config: a ``RenderConfig``; ``margin`` sets the grid origin and
            ``min_font_px`` / ``max_font_px`` act as clamps (not the realism
            control).
        char_advance_px: the loaded font's measured monospace advance at
            ``font_px``. When provided it sets ``cell_w`` so the grid pitch
            matches the glyph the renderer actually draws (dense, no stray
            letter-spacing). Falls back to the profile's ``char_width`` (which
            can be a wide, spacing-inflated estimate) only when unknown.

    Returns:
        A :class:`GridSpec` with ``cell_w`` (char advance px), ``cell_h`` (line
        pitch px), ``font_px`` (single body size) and ``grid_left`` (column-0 x).
    """
    char_width_n = _positive(getattr(profile, "char_width", None), _FALLBACK_CHAR_WIDTH)
    font_height_n = _positive(
        getattr(profile, "font_height", None), _FALLBACK_FONT_HEIGHT
    )
    line_pitch_n = getattr(profile, "line_pitch", None)

    # Clamp the body size to a sane range, honoring the config clamps but never
    # dropping below the readability floor / above the sanity ceiling.
    lo = max(_GRID_FONT_MIN, int(getattr(config, "min_font_px", _GRID_FONT_MIN)))
    hi = min(_GRID_FONT_MAX, int(getattr(config, "max_font_px", _GRID_FONT_MAX)))
    if hi < lo:
        hi = lo
    font_px = int(round(font_height_n * inner_h))
    font_px = max(lo, min(hi, font_px))

    if char_advance_px and char_advance_px > 0:
        cell_w = float(char_advance_px)
    else:
        cell_w = max(1.0, char_width_n * inner_w)

    if line_pitch_n and line_pitch_n > 0:
        cell_h = max(float(font_px) * 1.05, line_pitch_n * inner_h)
    else:
        cell_h = font_height_n * inner_h * 1.35
    cell_h = max(cell_h, float(font_px) * 1.05)

    grid_left = float(getattr(config, "margin", 0))
    return GridSpec(cell_w=cell_w, cell_h=cell_h, font_px=font_px, grid_left=grid_left)


def glyph_advance(
    draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont
) -> float:
    """Monospace cell advance of ``font`` in pixels (width of one '0')."""
    try:
        return float(draw.textlength("0", font=font))
    except (TypeError, ValueError):
        box = font.getbbox("0")
        return float(box[2] - box[0])


def is_price_token(text: str) -> bool:
    """True for a right-aligned amount token (snaps its right edge to the grid)."""
    return bool(_PRICE_TOKEN.match(str(text or "").strip().replace(" ", "")))


def drawn_cell_count(text: str) -> int:
    """Number of grid cells a token actually occupies = its non-space glyphs.

    Spaces are never drawn and never advance a column (see
    :func:`draw_token_chars`), so they must NOT be counted when right-anchoring a
    price or advancing the inter-word cursor. Counting them (``len(text)``) shifts
    a token like ``"1.99 T"`` one cell too far left of its visible glyphs.
    """
    return len(text.replace(" ", ""))


@dataclass
class GridWord:
    """A word already positioned in pixel space, ready for grid placement."""

    left: float
    top: float
    right: float
    bottom: float
    text: str
    ink: tuple[int, int, int]
    # Source line id (when known), so the layout scorecard can detect a grouped
    # row that fuses words from more than one printed line. ``None`` falls back to
    # a vertical-extent heuristic.
    source_line: int | None = None

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


# A word joins the current row when its box overlaps the row's vertical band by
# at least this fraction of the shorter height, OR its center is within
# ``_ROW_CENTER_TOL_CELLS * cell_h`` of the row's running-median center. Real
# same-line OCR boxes overlap ~80-95%; vertically adjacent printed lines overlap
# only ~10-25%, so a 0.35 threshold cleanly separates them. The center fallback
# catches same-line tokens whose heights differ enough to thin the overlap.
_ROW_OVERLAP_FRAC = 0.35
_ROW_CENTER_TOL_CELLS = 0.35


def _row_band(rows: Sequence[GridWord]) -> tuple[float, float]:
    """Representative (top, bottom) of a row: the MEDIAN edges of its members.

    Median (not running mean) is what stops a row from drifting open as words
    accrete -- the old running-mean anchor let three printed lines chain into one
    fused row (the `NV TAX 8.37500 ... TOTAL` collapse).
    """
    return (
        median(w.top for w in rows),
        median(w.bottom for w in rows),
    )


def group_words_into_grid_lines(
    words: Sequence[GridWord],
    cell_h: float,
) -> list[list[GridWord]]:
    """Cluster pixel-positioned words into visual rows (top -> bottom).

    Overlap-aware: a word joins the current row when its box vertically overlaps
    the row's running-median band by ``>= _ROW_OVERLAP_FRAC`` of the shorter
    height, or its center is within ``_ROW_CENTER_TOL_CELLS * cell_h`` of the
    band center; otherwise a new row opens. Each row is returned left-to-right so
    columns are placed in reading order.
    """
    if not words:
        return []
    center_tol = max(1.0, cell_h * _ROW_CENTER_TOL_CELLS)
    ordered = sorted(words, key=lambda w: w.center_y)
    lines: list[list[GridWord]] = []
    current: list[GridWord] = []
    for word in ordered:
        if not current:
            current = [word]
            continue
        band_top, band_bottom = _row_band(current)
        overlap = min(word.bottom, band_bottom) - max(word.top, band_top)
        shorter = min(word.bottom - word.top, band_bottom - band_top)
        frac = (overlap / shorter) if shorter > 0 else 0.0
        band_center = (band_top + band_bottom) / 2.0
        center_close = abs(word.center_y - band_center) <= center_tol
        if frac >= _ROW_OVERLAP_FRAC or center_close:
            current.append(word)
        else:
            lines.append(sorted(current, key=lambda w: w.left))
            current = [word]
    if current:
        lines.append(sorted(current, key=lambda w: w.left))
    return lines


def token_start_col(
    text: str, left: float, right: float, spec: GridSpec
) -> int:
    """Grid column of the token's first character.

    Left-aligned tokens snap their left edge to the nearest column. Price tokens
    are right-aligned in real receipts (the column shares a right edge), so they
    snap their *right* edge and back off the number of cells the glyphs actually
    occupy (``drawn_cell_count`` -- non-space glyphs, since spaces are not drawn).
    This keeps the price column (and its decimal points) on one column even though
    different amounts have different widths.
    """
    if is_price_token(text):
        end_col = round((right - spec.grid_left) / spec.cell_w)
        return end_col - drawn_cell_count(text)
    return round((left - spec.grid_left) / spec.cell_w)


def draw_token_chars(
    draw: ImageDraw.ImageDraw,
    text: str,
    start_col: int,
    baseline_y: float,
    spec: GridSpec,
    font: ImageFont.FreeTypeFont,
    ink: tuple[int, int, int],
) -> None:
    """Draw each glyph of ``text`` at consecutive grid columns on a baseline.

    Each drawn glyph lands at the next consecutive column starting at
    ``start_col``. Because ``cell_w`` is the font's own advance, glyphs sit flush
    (dense, no stray letter-spacing). Spaces are skipped and do NOT consume a
    column, so the rendered span equals ``drawn_cell_count(text)`` -- matching the
    cell count :func:`token_start_col` backs off for a right-anchored price. Drawn
    with ``anchor="ls"`` (left / baseline) so every line shares one baseline.
    """
    if not text:
        return
    col = start_col
    for char in text:
        if char == " ":
            continue
        x = spec.grid_left + col * spec.cell_w
        draw.text((x, baseline_y), char, font=font, fill=ink, anchor="ls")
        col += 1


@dataclass
class PlacedToken:
    """A word with its resolved grid column (the output of the planner).

    ``start_col`` is the column of the first drawn glyph; ``cells`` is how many
    columns the token occupies (``drawn_cell_count`` -- non-space glyphs). The
    rendered span is ``[start_col, start_col + cells)``. Separating this PLAN from
    the draw lets the layout scorecard inspect placements (overlaps, amount-column
    variance) without a font, and keeps the draw step a pure execution of the plan.
    """

    word: GridWord
    start_col: int
    cells: int
    is_price: bool

    @property
    def end_col(self) -> int:
        return self.start_col + self.cells


# A price token whose own right-edge column lands within this many cells of the
# shared amount lane is snapped to the lane (so the decimal points line up).
# Prices further left than this (unit prices, qty columns, coupon columns) keep
# their own column so we don't collapse a legitimately multi-column receipt.
_AMOUNT_LANE_TOL_CELLS = 4


def amount_lane_end(
    rows: Sequence[Sequence[GridWord]], spec: GridSpec
) -> int | None:
    """The shared right-edge column for the receipt's main amount column.

    Real receipts right-align every line total / summary amount to one column so
    the decimal points stack. The synthesizer's source boxes jitter that edge a
    few px line to line; rendered, the column wanders. We take the rightmost
    cluster of price-token right edges (within ``_AMOUNT_LANE_TOL_CELLS`` of the
    rightmost) and return its median column. ``None`` when there are no prices.
    """
    ends: list[int] = []
    for row in rows:
        for word in row:
            if is_price_token(word.text):
                ends.append(round((word.right - spec.grid_left) / spec.cell_w))
    if not ends:
        return None
    rightmost = max(ends)
    cluster = [e for e in ends if rightmost - e <= _AMOUNT_LANE_TOL_CELLS]
    return int(round(median(cluster)))


def plan_grid_line(
    line: Sequence[GridWord],
    spec: GridSpec,
    amount_lane: int | None = None,
) -> list[PlacedToken]:
    """Resolve every word of one visual row to a grid column (no drawing).

    Left-aligned words are nudged right when they would collide with the prior
    word, preserving at least a one-cell inter-word gap. A right-aligned price
    token keeps its OWN column position (it is never nudged), but it DOES advance
    the running cursor to its right edge -- so the token that follows it (a tax
    flag ``F`` / unit ``each``) is nudged to at least one cell past the price and
    renders visibly separated. This is the de-glue contract: the separation lives
    in the render-time cursor advance, so the SOURCE bbox no longer has to carry a
    bloated full-cell gap after every price (the old wide-spacing tell).

    When ``amount_lane`` is given, a price whose right edge falls within
    ``_AMOUNT_LANE_TOL_CELLS`` of the lane is snapped so its right edge sits on
    the lane -- giving one straight decimal column across the receipt.

    Body (left-aligned, non-first) tokens are placed by SOURCE whitespace, not
    their absolute snapped column: ``start = min(absolute_col, cursor +
    source_gap)`` floored at ``cursor + 1``. Because the source pitch is wider
    than the rendered cell, the absolute column inflates every inter-word gap (the
    ``365   Everyday   Value`` sprawl); taking the source gap collapses that to the
    real ~1-space spacing, while ``min(absolute_col, ...)`` guarantees we never
    push a token PAST its own column -- so a genuine middle column (a ``NF`` / ``T``
    tax flag that really sits far right in the source) keeps its position.
    """
    placed: list[PlacedToken] = []
    cursor_col = None
    prev_right_px: float | None = None
    for word in line:
        price = is_price_token(word.text)
        absolute = token_start_col(word.text, word.left, word.right, spec)
        cells = drawn_cell_count(word.text)
        if price:
            start = absolute
            # Snap a near-lane price's right edge onto the shared amount column.
            if amount_lane is not None and abs((start + cells) - amount_lane) <= _AMOUNT_LANE_TOL_CELLS:
                start = amount_lane - cells
        elif cursor_col is None:
            # First token on the line keeps its absolute column (line indent).
            start = absolute
        else:
            # Body token: place by source whitespace, but never past its own
            # absolute column, and never glued to the previous token.
            source_gap = 1
            if prev_right_px is not None:
                source_gap = max(
                    1, round((word.left - prev_right_px) / spec.cell_w)
                )
            start = min(absolute, cursor_col + source_gap)
            start = max(start, cursor_col + 1)
        placed.append(
            PlacedToken(word=word, start_col=start, cells=cells, is_price=price)
        )
        # Advance the cursor past EVERY token (price tokens included) so the next
        # word clears the price's right edge by at least one cell.
        cursor_col = start + cells
        prev_right_px = word.right
    return placed


def draw_grid_line(
    draw: ImageDraw.ImageDraw,
    line: Sequence[GridWord],
    baseline_y: float,
    spec: GridSpec,
    font: ImageFont.FreeTypeFont,
    amount_lane: int | None = None,
) -> None:
    """Draw every word of one visual row at a single shared baseline.

    Pure execution of :func:`plan_grid_line`: each planned token's glyphs are
    drawn at its resolved column on the shared baseline.
    """
    for placed in plan_grid_line(line, spec, amount_lane=amount_lane):
        draw_token_chars(
            draw,
            placed.word.text,
            placed.start_col,
            baseline_y,
            spec,
            font,
            placed.word.ink,
        )


def line_baseline(line: Sequence[GridWord], ascent: int) -> float:
    """Shared baseline for a row: the row's top edge plus the font ascent.

    Using the median word top (rather than each glyph's own box) is what forces
    a single consistent baseline across the row even when the synthesizer's word
    boxes jitter vertically.
    """
    top = median(word.top for word in line)
    return top + ascent


def assign_row_baselines(
    rows: Sequence[Sequence[GridWord]],
    ascent: int,
    min_pitch: float,
) -> list[float]:
    """Baseline per row (top -> bottom) with a minimum inter-row pitch floor.

    Each row's natural baseline is ``line_baseline``. When the synthesizer packs
    summary lines closer than one glyph-height (e.g. SUBTOTAL / TAX / TOTAL at
    ~0.6 cell pitch), consecutive baselines would overlap vertically and the
    glyphs collide. We walk top to bottom and push any row that sits less than
    ``min_pitch`` below its predecessor down to exactly ``prev + min_pitch``.

    This only moves CRAMPED rows: a row already >= ``min_pitch`` below its
    predecessor is untouched, so the push never propagates past a normal gap and
    there is no global downward drift.
    """
    baselines: list[float] = []
    prev = None
    for row in rows:
        base = line_baseline(row, ascent)
        if prev is not None and base - prev < min_pitch:
            base = prev + min_pitch
        baselines.append(base)
        prev = base
    return baselines


def _positive(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
