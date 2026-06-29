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
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridWord,
    build_grid_spec,
    draw_grid_line,
    glyph_advance,
    group_words_into_grid_lines,
    line_baseline,
)

# Vendored fixed-pitch thermal face for the grid path (Px437 IBM VGA 8x16,
# CC BY-SA 4.0 -- see fonts/NOTICE). Preferred over system monospace in grid
# mode because it renders as a hard pixel grid with anti-aliasing off.
_GRID_FONT_PATH = os.path.join(
    os.path.dirname(__file__), "fonts", "Px437_IBM_VGA_8x16.ttf"
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
        _render_grid(
            image, draw, words, scale, config, profile, inner_w, inner_h
        )
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


def _render_grid(
    image: Image.Image,
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
    its grid column on a shared per-row baseline.
    """
    # Pick the body size from the profile first, then measure the loaded font's
    # real monospace advance so the grid pitch matches the glyph we actually draw
    # (avoids the wide letter-spacing a spacing-inflated profile char_width gives).
    sizing = build_grid_spec(profile, inner_w, inner_h, config)
    font = _load_grid_font(sizing.font_px, config)
    advance = glyph_advance(draw, font)
    spec = build_grid_spec(
        profile, inner_w, inner_h, config, char_advance_px=advance
    )
    # "1" => 1-bit (no anti-aliasing): glyphs render as hard on/off dots, which
    # is what a thermal/dot-matrix head actually lays down.
    draw.fontmode = "1"
    ascent, _descent = font.getmetrics()

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
            )
        )

    for line in group_words_into_grid_lines(grid_words, spec.cell_h):
        baseline = line_baseline(line, ascent)
        draw_grid_line(draw, line, baseline, spec, font)


def _load_grid_font(
    size: int, config: RenderConfig
) -> ImageFont.FreeTypeFont:
    """Load the body font for the grid path.

    Prefers an explicit ``config.font_path``, else the vendored Px437 IBM VGA
    face, else the legacy monospace candidates / Pillow default.
    """
    if config.font_path:
        return _load_font(size, config)
    if os.path.exists(_GRID_FONT_PATH):
        key = (_GRID_FONT_PATH, int(max(1, size)))
        cached = _FONT_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(_GRID_FONT_PATH, int(max(1, size)))
            _FONT_CACHE[key] = font
            return font
        except OSError:
            pass
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
