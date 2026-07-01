"""Render a receipt dict ({lines/words/bboxes}) to a PNG for visual QA.

The renderer draws the *exact word boxes* a synthesizer produced, so the image
is a faithful picture of the geometry the LayoutLM gates will see — not a
re-imagined layout. Per-word size comes from each word's own bounding box (which
the synthesizer derived from real receipts); the :class:`MerchantFontProfile`
contributes the typeface/condensation and a fallback glyph size.

Recovering the real typeface name is out of scope (see the font-analysis pilot
caveat — OCR geometry yields style *groups*, not font names). We render in a
monospace face, which is what most receipts use, and condense it toward the
merchant's measured ``char_aspect`` so the look is recognizable.

Coordinates
-----------
Input boxes are ``[x0, y0, x1, y1]`` with ``y`` high-is-top (the synthesis
``normalized_receipt_0_1000_y_high_is_top`` space, or normalized 0-1 real OCR).
The scale is auto-detected unless ``coord_max`` is given. PNG pixels use the
usual top-left origin, so the renderer flips ``y``.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridSpec,
    GridWord,
    amount_lane_end,
    assign_row_baselines,
    build_grid_spec,
    draw_grid_line,
    draw_token_chars,
    effective_row_sections,
    glyph_advance,
    group_words_into_grid_lines,
    is_price_token,
    section_for_labels,
)

# Grid-path body face, most receipt-like first. Real thermal receipts use a
# clean, light, condensed sans monospace (Epson Font A family). A measured
# bake-off against real receipt photos picked:
#   1. Andale Mono  -- closest match (condensed, light); macOS-bundled, NOT
#      redistributable, so used only when present (e.g. local demo renders).
#   2. B612 Mono    -- vendored, SIL OFL (see fonts/B612Mono-LICENSE.txt); the
#      redistributable default so CI / non-mac renders still get a clean face.
# The old Px437 IBM VGA 8x16 face read as a blocky "serif typewriter" vs the
# real receipts (the dominant Opus typography tell); it is kept only as a last
# resort. ``_GRID_FONT_PATH`` stays defined (= the vendored OFL face) for any
# callers/tests that import it.
_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_VENDORED_THERMAL_FONT = os.path.join(_FONTS_DIR, "B612Mono-Regular.ttf")
_GRID_FONT_PATH = _VENDORED_THERMAL_FONT
_GRID_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    _VENDORED_THERMAL_FONT,
    os.path.join(_FONTS_DIR, "Px437_IBM_VGA_8x16.ttf"),  # legacy last resort
)

# Monospace candidates, most receipt-like first. Falls back to Pillow's bundled
# scalable default when none are present (e.g. CI without system fonts).
_MONOSPACE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/Library/Fonts/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)

# Distinct, QA-friendly colors per label family (used when color_by_label).
_LABEL_COLORS = {
    "PRODUCT_NAME": (20, 90, 200),
    "LINE_TOTAL": (200, 40, 40),
    "UNIT_PRICE": (200, 110, 20),
    "QUANTITY": (150, 60, 160),
    "SUBTOTAL": (180, 30, 30),
    "TAX": (180, 30, 30),
    "GRAND_TOTAL": (160, 20, 20),
    "MERCHANT_NAME": (20, 130, 90),
    "DATE": (90, 90, 90),
    "TIME": (90, 90, 90),
}
_DEFAULT_INK = (15, 15, 15)


@dataclass(frozen=True)
class RenderConfig:
    """Tunable rendering parameters."""

    width: int = 460
    height: int = 1100
    margin: int = 8
    background: tuple[int, int, int] = (252, 251, 248)
    color_by_label: bool = False
    draw_price_column: bool = False
    min_font_px: int = 6
    max_font_px: int = 72
    font_path: str | None = None
    # Grid typography: one fixed-pitch cell grid + one body size per receipt,
    # hard (non-anti-aliased) glyphs on a shared baseline. When False the legacy
    # per-token box-fitting path is used (see ``render_receipt``).
    grid_mode: bool = False
    # Per-section typography (real thermal receipts switch Font A / Font B per
    # region). ``section_scale`` multiplies the body font size for a section
    # (e.g. {"HEADER": 0.8}); ``section_font`` overrides the face for a section.
    # BODY/TOTALS stay at scale 1.0 so they share the amount column. None = the
    # whole receipt uses one size/font.
    section_scale: Mapping[str, float] | None = None
    section_font: Mapping[str, str] | None = None
    # Per-merchant grid-font shaping (measured against the real receipts):
    # ``condense`` horizontally compresses every glyph (real faces are more
    # condensed than off-the-shelf monospace); ``stroke`` thickens them (a
    # double-strike to match heavy thermal print). 1.0 / 0 = no shaping.
    condense: float = 1.0
    stroke: int = 0
    # Render in a real glyph atlas (the merchant's actual letterforms) instead of
    # a TTF. Maps {"regular": atlas.npz, "heavy": atlas.npz}; heavy is used for
    # display headings (see ``display_headings``). None -> the TTF grid font.
    bitmap_font: Mapping[str, str] | None = None
    # Display-heading treatment: a row whose (uppercased) text contains one of
    # these substrings renders in the HEAVY font, enlarged. Either a tuple of
    # substrings (all use ``heading_scale``) or a mapping {substring: scale} for
    # per-phrase sizes (Costco: SELF-CHECKOUT 1.7, THANK YOU 1.4, ITEMS SOLD: 1.8).
    # Empty -> no heading emphasis.
    display_headings: tuple[str, ...] | Mapping[str, float] = ()
    heading_scale: float = 1.0
    # Reverse-video the final TOTAL amount (white glyphs on a solid black box), as
    # Costco prints it. Only the price token on the "TOTAL" row (not SUBTOTAL / the
    # "TOTAL NUMBER OF ITEMS" line) is boxed.
    reverse_total: bool = False
    # Reverse-video the transaction date on the row directly after the "TOTAL NUMBER
    # OF ITEMS SOLD" line (Costco boxes that date, but not the identical date under
    # AMOUNT). Only the leading date token of that one row is boxed.
    reverse_date_after_items: bool = False
    # Center display lines (address block, headings, footer) on the paper midline.
    # A line with no price token and substantial, roughly-symmetric side margins in
    # the SOURCE is a centered line; our condensed font is narrower than the source
    # text, so without re-centering it drifts left. Body/amount lines are untouched.
    center_display_lines: bool = True
    # Draw dashed section separators the OCR drops (Costco prints a dashed rule
    # after the grand TOTAL and after the AMOUNT-block transaction-date line).
    dashed_separators: bool = False


def render_receipt(
    receipt: Mapping[str, Any],
    *,
    profile: MerchantFontProfile | None = None,
    config: RenderConfig | None = None,
    coord_max: float | None = None,
) -> Image.Image:
    """Render a receipt dict to a Pillow image.

    Args:
        receipt: ``{"lines": [{"words": [{"text", "bbox", "labels"}, ...]}]}``
            (a flat ``receipt["words"]`` list is also accepted).
        profile: merchant font profile; supplies the condensation factor and a
            fallback glyph height. ``None`` renders with neutral defaults.
        config: rendering parameters (size, colors, guides).
        coord_max: coordinate scale of the boxes. Auto-detected when ``None``
            (``1.0`` for normalized 0-1 OCR, else ``1000.0``).
    """
    config = config or RenderConfig()
    words = _iter_words(receipt)
    scale = coord_max or _detect_coord_max(words)

    image = Image.new("RGB", (config.width, config.height), config.background)
    draw = ImageDraw.Draw(image)

    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    condense = _condense_factor(profile)

    if config.draw_price_column and profile and profile.price_column_x:
        # Profile fields are always normalized 0-1, independent of the box scale.
        col_x = config.margin + profile.price_column_x * inner_w
        draw.line(
            [(col_x, config.margin), (col_x, config.height - config.margin)],
            fill=(220, 215, 205),
            width=1,
        )

    if config.grid_mode:
        _render_grid(draw, words, scale, config, profile, inner_w, inner_h)
        return image

    # Pass 1: fit each word to its own box, but bucket by line and remember the
    # SMALLEST fitted size on each line. A lone char ("a", "@", "t") otherwise
    # renders at full line-height while words get width-shrunk, so single-char
    # tokens looked oversized/serif-ish. Unifying to the line's body size fixes it.
    prepared = []
    line_size: dict[int, int] = {}
    for word in words:
        bbox = word.get("bbox")
        text = str(word.get("text") or "")
        if not bbox or not text.strip():
            continue
        px = _to_pixel_box(bbox, scale, config, inner_w, inner_h)
        if px is None:
            continue
        left, top, right, bottom = px
        box_h = max(1, bottom - top)
        box_w = max(1, right - left)
        font = _fit_font(draw, text, box_w, box_h, config, condense=condense)
        size = int(getattr(font, "size", box_h) or box_h)
        line_key = int(round((top + box_h / 2) / 6))
        line_size[line_key] = min(line_size.get(line_key, size), size)
        prepared.append(
            (left, top, box_w, box_h, text, _ink_for(word, config), line_key)
        )

    # Pass 2: draw every token at its line's body size (consistent within a line).
    for left, top, box_w, box_h, text, ink, line_key in prepared:
        font = _load_font(line_size[line_key], config)
        # Per-token horizontal squeeze so the glyphs never overrun the word box
        # (the 9px font floor can otherwise push text past the box right edge,
        # fusing it into the neighbour or clipping it off the paper margin).
        eff_condense = _fit_condense(draw, text, font, box_w, condense)
        # Vertically center the glyph in its box; left-align horizontally.
        _draw_text(
            image, draw, (left, top + box_h / 2), text, font, ink, eff_condense
        )

    return image


def _is_final_total(row_text: str) -> bool:
    """True for the grand-TOTAL row (Costco reverse-videos its amount), but not
    SUBTOTAL nor the "TOTAL NUMBER OF ITEMS SOLD" line."""
    t = row_text.upper()
    if "TOTAL" not in t:
        return False
    return "SUBTOTAL" not in t and "NUMBER" not in t


_DATE_LED = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")


def _draw_dash_row(draw, x0: float, x1: float, baseline_y: float, spec, font,
                   *, ink, stroke=0, condense=1.0, bitmap_font=None,
                   cap_px=None) -> None:
    """A thermal separator rule printed as a run of actual ``-`` glyphs.

    Costco prints its section separators as a full-width row of the dash
    character in the same monospace face as the body (dropped by OCR). Rendering
    them through :func:`draw_token_chars` -- rather than a drawn line -- gives the
    real letterform (bitmap atlas), condense, stroke and ink of the body text, so
    the rule reads as printed dashes instead of a clean vector line."""
    start_col = int(round((x0 - spec.grid_left) / spec.cell_w))
    n = int((x1 - x0) / spec.cell_w)
    if n <= 0:
        return
    draw_token_chars(draw, "-" * n, start_col, baseline_y, spec, font, ink,
                     stroke=stroke, condense=condense, bitmap_font=bitmap_font,
                     cap_px=cap_px)


def _render_grid(
    draw: ImageDraw.ImageDraw,
    words: Sequence[Mapping[str, Any]],
    scale: float,
    config: RenderConfig,
    profile: MerchantFontProfile | None,
    inner_w: int,
    inner_h: int,
) -> None:
    """Grid-typography path: one fixed cell grid + one body size per receipt.

    Groups words into visual rows, picks a single font size from the merchant
    profile, kills anti-aliasing (hard thermal dots), and snaps every glyph to
    its grid column on a shared per-row baseline. Draws onto ``draw`` (the caller
    owns the backing ``Image`` and returns it).
    """
    # Pick the body size from the profile first, then measure the loaded font's
    # real monospace advance so the grid pitch matches the glyph we actually draw
    # (avoids the wide letter-spacing a spacing-inflated profile char_width gives).
    sizing = build_grid_spec(profile, inner_w, inner_h, config)
    font = _load_grid_font(sizing.font_px, config)
    advance = glyph_advance(draw, font) * float(config.condense)
    # Bitmap (glyph-atlas) font: the merchant's actual letterforms. cap_px is the
    # target cap height (~0.72 of the em); advance comes from the atlas.
    bmf = bmf_heavy = None
    cap_px = None
    if config.bitmap_font:
        from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
            BitmapFont,
        )
        bmf = BitmapFont(config.bitmap_font["regular"])
        heavy_path = config.bitmap_font.get("heavy", config.bitmap_font["regular"])
        bmf_heavy = BitmapFont(heavy_path)
        cap_px = max(6, int(round(sizing.font_px * 0.72)))
        # Apply the merchant condense to the bitmap advance too (the TTF path
        # already does): real thermal faces pack glyphs tighter than the atlas's
        # widest-letter + gap estimate, so an un-condensed advance reads too airy.
        advance = bmf.advance(cap_px) * float(config.condense)
    spec = build_grid_spec(
        profile, inner_w, inner_h, config, char_advance_px=advance
    )
    # "1" => 1-bit (no anti-aliasing): glyphs render as hard on/off dots, which
    # is what a thermal/dot-matrix head actually lays down.
    draw.fontmode = "1"
    ascent, descent = font.getmetrics()
    # Minimum baseline-to-baseline pitch. This is a FLOOR that only pushes apart
    # rows the source packed tighter than a glyph; the true spacing still comes
    # from the OCR row positions. ``ascent + descent`` (the font's full line box,
    # ~1.5x cap height) was clobbering that -- real thermal receipts pack lines at
    # only ~1.08x the glyph height, so every row was forced ~30% too loose. Tie
    # the floor to the cap height instead (caps have no descender, so 1.12x clears
    # them) and let the OCR positions carry the real, tighter pitch.
    cap_h = float(cap_px) if cap_px else 0.72 * float(sizing.font_px)
    min_pitch = cap_h * 1.12

    grid_words: list[GridWord] = []
    for word in words:
        bbox = word.get("bbox")
        text = str(word.get("text") or "")
        if not bbox or not text.strip():
            continue
        px = _to_pixel_box(bbox, scale, config, inner_w, inner_h)
        if px is None:
            continue
        left, top, right, bottom = px
        grid_words.append(
            GridWord(
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                text=text,
                ink=_ink_for(word, config),
                section=section_for_labels(word.get("labels")),
            )
        )

    rows = group_words_into_grid_lines(grid_words, spec.cell_h)
    amount_lane = amount_lane_end(rows, spec)
    baselines = assign_row_baselines(rows, ascent, min_pitch)

    # Per-section typography: a row whose section has a scale != 1.0 or a font
    # override is drawn with its own (cached) spec/font. BODY/TOTALS stay at the
    # base spec so the shared amount column still aligns; scaled sections (HEADER,
    # PAYMENT) drop the lane since they carry no price column.
    section_scale = config.section_scale or {}
    section_font = config.section_font or {}
    # Normalize headings to (substring, scale) rules.
    raw_head = config.display_headings or ()
    if hasattr(raw_head, "items"):
        heading_rules = [(k.upper(), float(v)) for k, v in raw_head.items()]
    else:
        heading_rules = [(str(h).upper(), float(config.heading_scale or 1.0))
                         for h in raw_head]
    # The big bottom "Items Sold:" date line inherits that phrase's scale.
    items_sold_scale = next((sc for pat, sc in heading_rules if "ITEMS SOLD:" in pat),
                            None)
    # Centering geometry (paper content span in pixels).
    content_left = float(config.margin)
    content_right = float(config.width - config.margin)
    content_cw = max(1.0, content_right - content_left)

    def _center_target(line):
        """Paper-center x for a centered display line (no price, symmetric source
        margins), else None -> the line keeps its source-column placement."""
        if not config.center_display_lines:
            return None
        if any(is_price_token(w.text) for w in line):
            return None
        ll = min(w.left for w in line)
        lr = max(w.right for w in line)
        lm, rm = ll - content_left, content_right - lr
        if (lm > 0.08 * content_cw and rm > 0.08 * content_cw
                and abs(lm - rm) < 0.18 * content_cw):
            return content_left + content_cw / 2.0
        return None
    eff_sections = effective_row_sections(rows)
    # Rows after which Costco prints a dashed rule (dropped by OCR): the grand
    # TOTAL, and the AMOUNT-block transaction-date line (the date row whose prior
    # row is the "AMOUNT:" line).
    row_texts = [" ".join(w.text for w in ln).upper() for ln in rows]
    dash_after_rows: set[int] = set()
    if config.dashed_separators:
        for k, t in enumerate(row_texts):
            prevt = row_texts[k - 1] if k > 0 else ""
            first = t.split()[0] if t.split() else ""
            if _is_final_total(t) or ("AMOUNT" in prevt and _DATE_LED.match(first)):
                dash_after_rows.add(k)
    # The OCR drops the dashed rule, so no blank line exists for it -- reserve one
    # line below each anchor by pushing the following rows down, and place the rule
    # in the created gap (else it overprints the next row).
    dash_ys: list[float] = []
    if dash_after_rows:
        pitch = float(min_pitch)
        adjusted, shift = [], 0.0
        for k, b in enumerate(baselines):
            adjusted.append(b + shift)
            if k in dash_after_rows:
                dash_ys.append(adjusted[k] + pitch * 0.9)
                shift += pitch
        baselines = adjusted
    row_cache: dict[tuple, tuple] = {}
    prev_text = ""
    for line, baseline, sect in zip(rows, baselines, eff_sections):
        row_text = " ".join(w.text for w in line).upper()
        # A display heading (e.g. SELF-CHECKOUT, THANK YOU, ITEMS SOLD:) renders
        # heavy + enlarged; the heavy face is NOT applied to the whole TOTALS zone
        # (real Costco totals are body weight -- only headings, the reverse-video
        # total, and the bottom block stand out).
        hscale = next((sc for pat, sc in heading_rules if pat in row_text), None)
        # Bottom date line: the big date right after "Items Sold:" (distinct from
        # the reverse-video date after "TOTAL NUMBER OF ITEMS SOLD").
        if hscale is None and items_sold_scale is not None and "ITEMS SOLD:" in prev_text:
            hscale = items_sold_scale
        is_heading = hscale is not None
        is_total = bool(config.reverse_total) and _is_final_total(row_text)
        # The date on the row right after "TOTAL NUMBER OF ITEMS SOLD" is boxed.
        is_date_row = (bool(config.reverse_date_after_items)
                       and "NUMBER OF ITEMS SOLD" in prev_text)
        prev_text = row_text
        center_to = _center_target(line)
        fpath = section_font.get(sect) if sect else None
        if is_heading:
            sc = float(hscale)
            bf_row = bmf_heavy if bmf else None
        else:
            sc = float(section_scale.get(sect, 1.0)) if sect else 1.0
            bf_row = bmf
        if sc == 1.0 and not fpath:
            draw_grid_line(draw, line, baseline, spec, font, amount_lane=amount_lane,
                           stroke=config.stroke, condense=config.condense,
                           bitmap_font=bf_row, cap_px=cap_px,
                           reverse_price=is_total, reverse_date=is_date_row,
                           background=config.background, center_to=center_to)
            continue
        key = (fpath, sc, is_heading)
        cached = row_cache.get(key)
        if cached is None:
            row_font_px = max(6, int(round(sizing.font_px * sc)))
            row_cfg = config if not fpath else replace(config, font_path=fpath)
            row_font = _load_grid_font(row_font_px, row_cfg)
            if bf_row is not None:
                # Bitmap pitch comes from the atlas advance at the scaled cap, NOT
                # the TTF advance (the two differ -> mis-spaced enlarged rows).
                row_cap = max(6, int(round((cap_px or row_font_px) * sc)))
                row_adv = bf_row.advance(row_cap)
            else:
                row_cap = None
                row_adv = glyph_advance(draw, row_font) * float(config.condense)
            row_spec = GridSpec(
                cell_w=row_adv, cell_h=spec.cell_h,
                font_px=row_font_px, grid_left=spec.grid_left,
            )
            row_cache[key] = (row_spec, row_font, row_cap)
            cached = row_cache[key]
        row_spec, row_font, row_cap = cached
        # Lane only applies when the row shares the base cell grid (scale 1.0).
        lane = amount_lane if sc == 1.0 else None
        cp = row_cap if row_cap else (int(round(cap_px * sc)) if cap_px else None)
        draw_grid_line(draw, line, baseline, row_spec, row_font, amount_lane=lane,
                       stroke=config.stroke, condense=config.condense,
                       bitmap_font=bf_row, cap_px=cp,
                       reverse_price=is_total, reverse_date=is_date_row,
                       background=config.background, center_to=center_to)

    # Dashed section rules, printed as a run of real ``-`` glyphs in the reserved
    # gap below each anchor row. Use the merchant's bitmap face when it carries a
    # dash glyph, else the body TTF (which always has one).
    dash_bmf = bmf if (bmf is not None and bmf.has("-")) else None
    for y in dash_ys:
        _draw_dash_row(draw, content_left, content_right, y, spec, font,
                       ink=_DEFAULT_INK, stroke=config.stroke,
                       condense=config.condense, bitmap_font=dash_bmf,
                       cap_px=cap_px)


def _load_grid_font(
    size: int, config: RenderConfig
) -> ImageFont.FreeTypeFont:
    """Load the body font for the grid path.

    Prefers an explicit ``config.font_path``, else the first present face in
    ``_GRID_FONT_CANDIDATES`` (Andale Mono -> vendored B612 Mono -> legacy
    Px437), else the legacy monospace candidates / Pillow default.
    """
    if config.font_path:
        return _load_font(size, config)
    px = int(max(1, size))
    for path in _GRID_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        key = (path, px)
        cached = _FONT_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(path, px)
            _FONT_CACHE[key] = font
            return font
        except OSError:
            continue
    return _load_font(size, config)


def render_real_vs_synthetic(
    real_receipt: Mapping[str, Any],
    synthetic_receipt: Mapping[str, Any],
    *,
    profile: MerchantFontProfile | None = None,
    config: RenderConfig | None = None,
    labels: tuple[str, str] = ("real", "synthetic"),
) -> Image.Image:
    """Render real and synthetic receipts side by side for visual diffing."""
    config = config or RenderConfig()
    left = render_receipt(real_receipt, profile=profile, config=config)
    right = render_receipt(synthetic_receipt, profile=profile, config=config)
    return _hstack_labeled(left, right, labels)


def save_receipt_png(
    receipt: Mapping[str, Any],
    path: str,
    *,
    profile: MerchantFontProfile | None = None,
    config: RenderConfig | None = None,
    coord_max: float | None = None,
) -> str:
    """Render ``receipt`` and write it to ``path`` (PNG). Returns ``path``."""
    image = render_receipt(
        receipt, profile=profile, config=config, coord_max=coord_max
    )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.save(path, format="PNG")
    return path


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _iter_words(receipt: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    flat = receipt.get("words")
    if isinstance(flat, list) and flat:
        return [word for word in flat if isinstance(word, Mapping)]
    words: list[Mapping[str, Any]] = []
    for line in receipt.get("lines", []) or []:
        for word in line.get("words", []) or []:
            if isinstance(word, Mapping):
                words.append(word)
    return words


def _detect_coord_max(words: Sequence[Mapping[str, Any]]) -> float:
    peak = 0.0
    for word in words:
        bbox = word.get("bbox")
        if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)):
            continue
        for value in list(bbox)[:4]:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                peak = max(peak, abs(number))
    return 1.0 if peak <= 1.5 else 1000.0


def _to_pixel_box(
    bbox: Sequence[float],
    scale: float,
    config: RenderConfig,
    inner_w: int,
    inner_h: int,
) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)):
        return None
    if len(bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    if scale <= 0:
        return None
    left = config.margin + (min(x0, x1) / scale) * inner_w
    right = config.margin + (max(x0, x1) / scale) * inner_w
    # y is high-is-top: the larger receipt-y is the visual top, so flip.
    top_receipt = max(y0, y1)
    bottom_receipt = min(y0, y1)
    top = config.margin + (1.0 - top_receipt / scale) * inner_h
    bottom = config.margin + (1.0 - bottom_receipt / scale) * inner_h
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _condense_factor(profile: MerchantFontProfile | None) -> float:
    """Horizontal squeeze applied to glyphs to approach the merchant aspect.

    Monospace faces render ~0.6 advance/height. ``char_aspect`` is the
    merchant's measured advance/height. Clamp the ratio to a gentle range so a
    noisy aspect never collapses or balloons the text.
    """
    if profile is None or profile.char_aspect <= 0:
        return 1.0
    monospace_aspect = 0.6
    factor = profile.char_aspect / monospace_aspect
    return max(0.6, min(1.6, factor))


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    box_w: float,
    box_h: float,
    config: RenderConfig,
    *,
    condense: float,
) -> ImageFont.FreeTypeFont:
    """Largest font (capped by box height) whose text still fits the box width."""
    size = int(max(config.min_font_px, min(config.max_font_px, box_h)))
    font = _load_font(size, config)
    width = _text_width(draw, text, font) * condense
    if width <= box_w or width <= 0:
        return font
    # Shrink proportionally to fit, then floor at the minimum readable size.
    shrunk = max(config.min_font_px, int(size * (box_w / width)))
    if shrunk >= size:
        return font
    return _load_font(shrunk, config)


def _fit_condense(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    box_w: float,
    condense: float,
) -> float:
    """Squeeze factor that keeps ``text`` inside its box with a small gap.

    Reserves a thin inter-word gap inside each box so adjacent same-line tokens
    keep visible whitespace, then narrows the glyph horizontally (height — and
    thus OCR legibility — is preserved) only as much as needed to fit. Floored
    so text is never crushed into an unreadable sliver.
    """
    raw = _text_width(draw, text, font)
    if raw <= 0:
        return condense
    gap = max(1.5, box_w * 0.05)
    avail = max(1.0, box_w - gap)
    eff = condense
    if raw * eff > avail:
        eff = avail / raw
    # Floor the squeeze so a long token (e.g. a 9px-floored value crammed into a
    # thin box) stays legible. A little overflow past the box is preferable to an
    # unreadable sliver; the gap reservation still keeps neighbours separated.
    return max(0.62, min(condense, eff))


def _draw_text(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    anchor_left_center: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    ink: tuple[int, int, int],
    condense: float,
) -> None:
    left, center_y = anchor_left_center
    if abs(condense - 1.0) < 1e-3:
        draw.text((left, center_y), text, font=font, fill=ink, anchor="lm")
        return
    # Condensing means rendering to a scratch layer and squashing horizontally.
    ascent, descent = font.getmetrics()
    text_w = max(1, int(_text_width(draw, text, font)))
    text_h = max(1, ascent + descent)
    layer = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    layer_draw.text((0, 0), text, font=font, fill=ink + (255,))
    new_w = max(1, int(text_w * condense))
    layer = layer.resize((new_w, text_h), Image.LANCZOS)
    paste_y = int(center_y - text_h / 2)
    image.paste(layer, (int(left), paste_y), layer)


def _ink_for(
    word: Mapping[str, Any], config: RenderConfig
) -> tuple[int, int, int]:
    if not config.color_by_label:
        return _DEFAULT_INK
    for label in word.get("labels") or []:
        color = _LABEL_COLORS.get(_strip_bio(label))
        if color is not None:
            return color
    return _DEFAULT_INK


def _strip_bio(label: Any) -> str:
    """Normalize a label, dropping a ``B-``/``I-`` BIO prefix if present."""
    text = str(label).upper()
    if len(text) > 2 and text[:2] in ("B-", "I-"):
        return text[2:]
    return text


def _text_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont
) -> float:
    try:
        return float(draw.textlength(text, font=font))
    except (TypeError, ValueError):
        bbox = font.getbbox(text)
        return float(bbox[2] - bbox[0])


_FONT_CACHE: dict[tuple[str | None, int], ImageFont.FreeTypeFont] = {}


def _load_font(size: int, config: RenderConfig) -> ImageFont.FreeTypeFont:
    size = max(1, int(size))
    key = (config.font_path, size)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    candidates = (
        [config.font_path] if config.font_path else []
    ) + list(_MONOSPACE_FONT_CANDIDATES)
    for path in candidates:
        if path and os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[key] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = font
    return font


def _hstack_labeled(
    left: Image.Image,
    right: Image.Image,
    labels: tuple[str, str],
) -> Image.Image:
    gap = 12
    banner = 22
    height = max(left.height, right.height) + banner
    width = left.width + right.width + gap
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(left, (0, banner))
    canvas.paste(right, (left.width + gap, banner))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=14)
    draw.text((4, 4), labels[0], font=font, fill=(0, 0, 0))
    draw.text((left.width + gap + 4, 4), labels[1], font=font, fill=(0, 0, 0))
    draw.line([(left.width + gap // 2, 0), (left.width + gap // 2, height)],
              fill=(200, 200, 200), width=1)
    return canvas
