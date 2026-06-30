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

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


def group_words_into_grid_lines(
    words: Sequence[GridWord],
    cell_h: float,
) -> list[list[GridWord]]:
    """Cluster pixel-positioned words into visual rows (top -> bottom).

    Words whose vertical centers fall within ``~0.6 * cell_h`` of the current
    row's running center join that row; otherwise a new row opens. Each row is
    returned left-to-right so columns are placed in reading order.
    """
    if not words:
        return []
    band = max(1.0, cell_h * 0.6)
    ordered = sorted(words, key=lambda w: w.center_y)
    lines: list[list[GridWord]] = []
    current: list[GridWord] = []
    anchor = None
    for word in ordered:
        if anchor is None or abs(word.center_y - anchor) <= band:
            current.append(word)
            # Running mean keeps the band centered as a row accretes words.
            anchor = sum(w.center_y for w in current) / len(current)
        else:
            lines.append(sorted(current, key=lambda w: w.left))
            current = [word]
            anchor = word.center_y
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


def draw_grid_line(
    draw: ImageDraw.ImageDraw,
    line: Sequence[GridWord],
    baseline_y: float,
    spec: GridSpec,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Draw every word of one visual row at a single shared baseline.

    Left-aligned words are nudged right when they would collide with the prior
    word, preserving at least a one-cell inter-word gap. A right-aligned price
    token keeps its OWN column position (it is never nudged), but it DOES advance
    the running cursor to its right edge -- so the token that follows it (a tax
    flag ``F`` / unit ``each``) is nudged to at least one cell past the price and
    renders visibly separated. This is the de-glue contract: the separation lives
    in the render-time cursor advance, so the SOURCE bbox no longer has to carry a
    bloated full-cell gap after every price (the old wide-spacing tell).
    """
    cursor_col = None
    for word in line:
        start = token_start_col(word.text, word.left, word.right, spec)
        # Price tokens keep their right-anchored column; only LEFT-aligned tokens
        # are nudged off the running cursor.
        if not is_price_token(word.text) and cursor_col is not None:
            start = max(start, cursor_col + 1)
        draw_token_chars(
            draw, word.text, start, baseline_y, spec, font, word.ink
        )
        # Advance the cursor past EVERY token (price tokens included) so the next
        # word clears the price's right edge by at least one cell.
        cursor_col = start + drawn_cell_count(word.text)


def line_baseline(line: Sequence[GridWord], ascent: int) -> float:
    """Shared baseline for a row: the row's top edge plus the font ascent.

    Using the median word top (rather than each glyph's own box) is what forces
    a single consistent baseline across the row even when the synthesizer's word
    boxes jitter vertically.
    """
    top = median(word.top for word in line)
    return top + ascent


def _positive(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
