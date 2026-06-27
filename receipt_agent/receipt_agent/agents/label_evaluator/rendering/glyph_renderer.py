"""Stamp a synthesized receipt with a merchant's REAL glyph crops (M2).

Where ``receipt_renderer.py`` draws a synthesized receipt in a generic monospace
TTF, this renderer composites the actual ink letterforms from a
:class:`GlyphAtlas` (M1) so the result carries the merchant's own typeface, its
intra-receipt **bold** weight, the real **logo** wordmark, and thermal-paper
texture — a render that is, at a glance, hard to tell from a photo of a real
receipt. It is for HUMAN REVIEW realism only (CHARTER.md): the structured
receipt stays the only training artifact; nothing here feeds a training gate.

Geometry contract
-----------------
The word boxes drive placement exactly as in ``receipt_renderer`` — the same
``_to_pixel_box`` / ``_detect_coord_max`` helpers are reused, so this renderer
does not change the existing geometry renderer's contract; it only swaps glyph
drawing (TTF -> real-crop stamping).

Glyph scale + baseline
----------------------
Atlas crops are tight to ink, which drops cap-height / baseline info. Each glyph
is rescaled by its own ``box_height_norm`` relative to the style's median glyph
height, so a tall ``H`` and a short ``-`` keep their real relative sizes, and is
bottom-aligned to the word-box baseline (with a small descender allowance).
"""

from __future__ import annotations

import math
import os
import random
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFilter

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
)
from receipt_agent.agents.label_evaluator.rendering.glyph_atlas import (
    AtlasStyle,
    GlyphAtlas,
    GlyphCrop,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    _detect_coord_max,
    _iter_words,
    _strip_bio,
    _to_pixel_box,
)

# A fallback hook (char, style) -> RGBA ink glyph at a given pixel height, used
# for characters the atlas lacks. M3 (TTF fallback) supplies one; by default
# missing glyphs are simply skipped (a faint gap, like a light receipt strike).
GlyphFallback = Callable[[str, AtlasStyle, int], "Image.Image | None"]

# Bold is applied to whole lines on real receipts (promo + category headers).
# When a synthesized receipt does not mark emphasis, fall back to these cues.
_BOLD_LINE_HINTS = ("MEMBER SAVINGS", "SAVINGS", "YOU SAVED")
_CATEGORY_HINTS = (
    "GROCERY", "PRODUCE", "LIQUOR", "BAKERY", "BAKED GOODS", "DELI", "DAIRY",
    "REFRIG", "FROZEN", "MEAT", "SEAFOOD", "GENERAL", "HEALTH", "BEAUTY",
    "HOUSEHOLD", "BEVERAGE",
)
_MERCHANT_LABELS = ("MERCHANT_NAME", "STORE_NAME")

# Tokens that are right-aligned into the price/amount column.
_AMOUNT_LABELS = frozenset({
    "LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL", "UNIT_PRICE", "AMOUNT",
    "BALANCE", "TOTAL", "PRICE",
})
_AMOUNT_RE = re.compile(r"^\$?\d{1,4}(?:,\d{3})*\.\d{2}\$?[A-Z]?-?$")


@dataclass(frozen=True)
class GlyphRenderConfig:
    """Tunable parameters for glyph stamping + thermal realism."""

    width: int = 460
    height: int = 1100
    margin: int = 10
    paper: tuple[int, int, int] = (250, 249, 245)
    ink: tuple[int, int, int] = (38, 36, 34)
    ink_jitter: int = 26  # per-glyph brightness variation (thermal unevenness)
    char_tracking: float = 0.04  # extra advance as a fraction of the cell width
    descender_frac: float = 0.12  # baseline raised this fraction above box bottom
    noise: float = 0.5  # 0 = clean, 1 = heavy paper/scan noise
    blur: float = 0.4  # gaussian blur radius for thermal softness
    seed: int = 7
    use_logo: bool = True


def render_receipt_glyphs(
    receipt: Mapping[str, Any],
    atlas: GlyphAtlas,
    *,
    profile: MerchantFontProfile | None = None,
    config: GlyphRenderConfig | None = None,
    coord_max: float | None = None,
    fallback: GlyphFallback | None = None,
) -> Image.Image:
    """Render a receipt dict by stamping ``atlas`` glyph crops.

    Args:
        receipt: ``{"lines":[{"words":[{"text","bbox","labels"}]}]}`` (a flat
            ``receipt["words"]`` list is also accepted), boxes ``[x0,y0,x1,y1]``
            with y high-is-top — the same shape ``receipt_renderer`` consumes.
        atlas: the merchant glyph atlas (M1).
        profile: optional font profile (reserved for advance tuning).
        config: render parameters.
        coord_max: coordinate scale; auto-detected when ``None``.
        fallback: optional glyph fallback for chars absent from the atlas (M3).
    """
    config = config or GlyphRenderConfig()
    rng = random.Random(config.seed)
    words = _iter_words(receipt)
    scale = coord_max or _detect_coord_max(words)

    image = _thermal_paper(config, rng)
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin

    line_words = _group_words_by_line(receipt, words)
    body_h_ref = _median_word_height(words)
    # Character pitch (advance) in normalized inner-width units: prefer the
    # merchant profile (a real fixed pitch), else the receipt's own median
    # advance. This is what makes lines render on a true grid instead of each
    # word being squeezed into its own box (the collapsed-spacing failure).
    pitch_norm = _pitch_norm(words, scale, profile)
    font_h_norm = profile.font_height if (profile and profile.font_height) else None
    for line in line_words:
        is_logo = _line_is_logo(line, body_h_ref)
        bold = _line_is_bold(line)
        if is_logo and config.use_logo and atlas.logo is not None:
            if _stamp_logo(image, line, atlas, scale, config, inner_w, inner_h):
                continue  # logo stamped; skip per-char rendering for this line
        _stamp_line_fixed_pitch(
            image, line, atlas, scale, config, inner_w, inner_h,
            bold=bold, rng=rng, fallback=fallback,
            pitch_norm=pitch_norm, font_h_norm=font_h_norm,
        )

    return _thermal_finish(image, config)


def render_real_vs_glyph(
    real_image: Image.Image,
    receipt: Mapping[str, Any],
    atlas: GlyphAtlas,
    *,
    profile: MerchantFontProfile | None = None,
    config: GlyphRenderConfig | None = None,
    coord_max: float | None = None,
    fallback: GlyphFallback | None = None,
    labels: tuple[str, str] = ("real", "glyph-render"),
) -> Image.Image:
    """A real receipt photo beside its glyph-stamped render, for visual diffing."""
    config = config or GlyphRenderConfig()
    rendered = render_receipt_glyphs(
        receipt, atlas, profile=profile, config=config, coord_max=coord_max,
        fallback=fallback
    )
    left = _fit_height(real_image.convert("RGB"), rendered.height)
    return _hstack_labeled(left, rendered, labels)


def save_receipt_glyphs(
    receipt: Mapping[str, Any],
    atlas: GlyphAtlas,
    path: str,
    *,
    profile: MerchantFontProfile | None = None,
    config: GlyphRenderConfig | None = None,
    coord_max: float | None = None,
    fallback: GlyphFallback | None = None,
) -> str:
    """Render and write a glyph-stamped receipt PNG. Returns ``path``."""
    image = render_receipt_glyphs(
        receipt, atlas, profile=profile, config=config, coord_max=coord_max,
        fallback=fallback
    )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.save(path, format="PNG")
    return path


# --------------------------------------------------------------------------- #
# stamping
# --------------------------------------------------------------------------- #


def _pitch_norm(
    words: Sequence[Mapping[str, Any]],
    scale: float,
    profile: MerchantFontProfile | None,
) -> float:
    """Character advance as a fraction of the inner render width.

    Prefer the merchant profile's measured ``char_width`` (a real printer pitch);
    otherwise estimate the receipt's own median advance = word_width / char_count.
    """
    if profile is not None and profile.char_width and profile.char_width > 0:
        return float(profile.char_width)
    advances: list[float] = []
    for word in words:
        text = str(word.get("text") or "")
        n = len(text.strip())
        if n < 2:
            continue
        bbox = word.get("bbox")
        if not isinstance(bbox, Sequence) or len(bbox) < 4:
            continue
        try:
            w_norm = abs(float(bbox[2]) - float(bbox[0])) / max(scale, 1e-9)
        except (TypeError, ValueError):
            continue
        if w_norm > 0 and math.isfinite(w_norm):
            advances.append(w_norm / n)
    return median(advances) if advances else 0.012


def _is_amount(word: Mapping[str, Any]) -> bool:
    labels = {_strip_bio(label) for label in (word.get("labels") or [])}
    if labels & _AMOUNT_LABELS:
        return True
    return bool(_AMOUNT_RE.match(str(word.get("text") or "").strip()))


def _snap_to_pitch(value: float, pitch: float, *, origin: float) -> float:
    """Snap a pixel coordinate to the fixed character grid."""
    if pitch <= 0:
        return value
    return origin + round((value - origin) / pitch) * pitch


def _stamp_line_fixed_pitch(
    image: Image.Image,
    line: Sequence[Mapping[str, Any]],
    atlas: GlyphAtlas,
    scale: float,
    config: GlyphRenderConfig,
    inner_w: int,
    inner_h: int,
    *,
    bold: bool,
    rng: random.Random,
    fallback: GlyphFallback | None,
    pitch_norm: float,
    font_h_norm: float | None,
) -> None:
    """Render one line on a fixed character pitch.

    Words keep their real grid positions (so columns and inter-word gaps survive),
    but every glyph advances by the SAME pitch, and a running cursor guarantees at
    least one blank cell between words — eliminating collapsed spacing and
    cross-word overprinting. Amount tokens are right-aligned by their ending edge
    so the price column stays straight.
    """
    style = atlas.style_for_role("body", bold=bold)
    if style is None:
        return
    placed = []
    for word in line:
        text = str(word.get("text") or "")
        if not text.strip():
            continue
        px = _to_pixel_box(word.get("bbox"), scale, _PixelCfg(config), inner_w, inner_h)
        if px is not None:
            placed.append((word, text, px))
    if not placed:
        return
    placed.sort(key=lambda t: t[2][0])  # left-to-right

    pitch = max(2.0, pitch_norm * inner_w)
    box_heights = [b - t for _, _, (l, t, r, b) in placed]
    line_h = median(box_heights)
    glyph_h = (
        max(line_h * 0.7, min(line_h * 1.15, font_h_norm * inner_h))
        if font_h_norm
        else line_h
    )
    baseline = median([b for _, _, (l, t, r, b) in placed]) - line_h * config.descender_frac
    ref_h = style.median_box_height or 1.0
    max_glyph_w = pitch * (1.0 - config.char_tracking)
    grid_origin = float(config.margin)

    cursor = -1.0  # right edge (px) of the last glyph cell drawn on this line
    for word, text, (left, top, right, bottom) in placed:
        n = len(text)
        if _is_amount(word):
            end_x = _snap_to_pitch(right, pitch, origin=grid_origin)
            start_x = end_x - n * pitch
        else:
            start_x = _snap_to_pitch(left, pitch, origin=grid_origin)
        if start_x < cursor:  # never overlap the previous word
            start_x = cursor
        for i, char in enumerate(text):
            cell_left = start_x + i * pitch
            if char.strip():
                glyph_img = _render_glyph(
                    char, atlas, style, bold=bold, target_h=glyph_h,
                    ref_h=ref_h, max_w=max_glyph_w, config=config, rng=rng,
                    fallback=fallback,
                )
                if glyph_img is not None:
                    gx = int(cell_left + (pitch - glyph_img.width) / 2)
                    gy = int(baseline - glyph_img.height)
                    image.alpha_composite(glyph_img, (max(0, gx), max(0, gy)))
        cursor = start_x + n * pitch + pitch  # one blank cell before next word


def _render_glyph(
    char: str,
    atlas: GlyphAtlas,
    style: AtlasStyle,
    *,
    bold: bool,
    target_h: float,
    ref_h: float,
    max_w: float,
    config: GlyphRenderConfig,
    rng: random.Random,
    fallback: GlyphFallback | None,
) -> Image.Image | None:
    """Produce a sized, inked glyph for one character (atlas crop or fallback)."""
    crop = atlas.glyph(char, role="body", bold=bold)
    if crop is not None:
        rel = max(0.5, min(1.0, crop.box_height_norm / ref_h if ref_h else 1.0))
        h = max(2.0, target_h * rel)
        w = h * crop.aspect
        if w > max_w:
            h *= max_w / w
        glyph_img = _scaled_inked(crop, h, config, rng)
    elif fallback is not None:
        glyph_img = fallback(char, style, int(max(2, target_h)))
    else:
        return None
    if glyph_img is None:
        return None
    if glyph_img.width > max_w:  # cap fallback glyphs to the cell
        s = max_w / glyph_img.width
        glyph_img = glyph_img.resize(
            (max(1, int(glyph_img.width * s)), max(1, int(glyph_img.height * s))),
            Image.LANCZOS,
        )
    return glyph_img


def _stamp_word(
    image: Image.Image,
    word: Mapping[str, Any],
    atlas: GlyphAtlas,
    scale: float,
    config: GlyphRenderConfig,
    inner_w: int,
    inner_h: int,
    *,
    bold: bool,
    rng: random.Random,
    fallback: GlyphFallback | None,
) -> None:
    text = str(word.get("text") or "")
    bbox = word.get("bbox")
    if not text.strip() or not bbox:
        return
    px = _to_pixel_box(bbox, scale, _PixelCfg(config), inner_w, inner_h)
    if px is None:
        return
    left, top, right, bottom = px
    box_w = max(1.0, right - left)
    box_h = max(1.0, bottom - top)

    style = atlas.style_for_role("body", bold=bold)
    if style is None:
        return
    ref_h = style.median_box_height or 1.0
    baseline = bottom - box_h * config.descender_frac

    n = len(text)
    cell_w = box_w / n
    max_glyph_w = cell_w * (1.0 - config.char_tracking)
    for i, char in enumerate(text):
        cell_left = left + i * cell_w
        if not char.strip():
            continue
        crop = atlas.glyph(char, role="body", bold=bold)
        glyph_img: Image.Image | None
        if crop is not None:
            # Scale by this glyph's real height relative to the style median, so
            # tall caps and short x-height/punctuation keep their relative sizes.
            # Clamp the ratio: caps land at the box height and the shortest marks
            # at ~0.5, which removes the per-instance height jitter (the same
            # char from different receipts varies) that reads as ragged text.
            rel = crop.box_height_norm / ref_h if ref_h else 1.0
            rel = max(0.5, min(1.0, rel))
            target_h = max(2.0, box_h * rel)
            target_w = target_h * crop.aspect
            # ...but never wider than the monospace cell, so glyphs do not bleed
            # into their neighbours (keeps inter-word gaps and avoids ink bands).
            if target_w > max_glyph_w:
                shrink = max_glyph_w / target_w
                target_h *= shrink
                target_w = max_glyph_w
            glyph_img = _scaled_inked(crop, target_h, config, rng)
        elif fallback is not None:
            glyph_img = fallback(char, style, int(box_h))
        else:
            glyph_img = None
        if glyph_img is None:
            continue
        # A fallback glyph (M3 TTF) has not been width-fitted; cap any glyph to
        # the cell so it cannot bleed into its neighbour.
        if glyph_img.width > max_glyph_w:
            s = max_glyph_w / glyph_img.width
            glyph_img = glyph_img.resize(
                (max(1, int(glyph_img.width * s)),
                 max(1, int(glyph_img.height * s))),
                Image.LANCZOS,
            )
        # Center in the cell horizontally; bottom-align to the baseline.
        gx = int(cell_left + (cell_w - glyph_img.width) / 2)
        gy = int(baseline - glyph_img.height)
        image.alpha_composite(glyph_img, (max(0, gx), max(0, gy)))


def _scaled_inked(
    crop: GlyphCrop,
    target_h: float,
    config: GlyphRenderConfig,
    rng: random.Random,
) -> Image.Image:
    """Scale a glyph crop to ``target_h`` and tint its alpha with thermal ink."""
    target_h = max(2, int(round(target_h)))
    target_w = max(1, int(round(target_h * crop.aspect)))
    alpha = crop.image.getchannel("A").resize(
        (target_w, target_h), Image.LANCZOS
    )
    jitter = rng.randint(-config.ink_jitter, config.ink_jitter)
    ink = tuple(max(0, min(255, c + jitter)) for c in config.ink)
    glyph = Image.new("RGBA", (target_w, target_h), ink + (0,))
    glyph.putalpha(alpha)
    return glyph


def _stamp_logo(
    image: Image.Image,
    line: Sequence[Mapping[str, Any]],
    atlas: GlyphAtlas,
    scale: float,
    config: GlyphRenderConfig,
    inner_w: int,
    inner_h: int,
) -> bool:
    """Composite the real logo wordmark into the line's pixel box."""
    box = _union_pixel_box(line, scale, config, inner_w, inner_h)
    if box is None or atlas.logo is None:
        return False
    left, top, right, bottom = box
    box_w = max(1.0, right - left)
    box_h = max(1.0, bottom - top)
    logo = atlas.logo
    s = min(box_w / logo.width, box_h / logo.height)
    new = (max(1, int(logo.width * s)), max(1, int(logo.height * s)))
    scaled = logo.resize(new, Image.LANCZOS)
    inked = Image.new("RGBA", scaled.size, config.ink + (0,))
    inked.putalpha(scaled.getchannel("A"))
    gx = int(left + (box_w - inked.width) / 2)
    gy = int(top + (box_h - inked.height) / 2)
    image.alpha_composite(inked, (max(0, gx), max(0, gy)))
    return True


# --------------------------------------------------------------------------- #
# line role / emphasis
# --------------------------------------------------------------------------- #


def _group_words_by_line(
    receipt: Mapping[str, Any], words: Sequence[Mapping[str, Any]]
) -> list[list[Mapping[str, Any]]]:
    """Group words into visual lines.

    Uses the receipt's own ``lines`` structure when present; otherwise clusters
    the flat word list by vertical position (box center-y).
    """
    lines = receipt.get("lines")
    if isinstance(lines, list) and lines:
        grouped = []
        for line in lines:
            line_words = [
                w for w in (line.get("words") or []) if isinstance(w, Mapping)
            ]
            if line_words:
                grouped.append(line_words)
        if grouped:
            return grouped
    # Flat fallback: bucket by center-y (boxes are y high-is-top). Bad/degenerate
    # bboxes are skipped here the same way ``_to_pixel_box`` ignores them, so a
    # non-numeric coordinate never crashes the render.
    rows: list[tuple[float, Mapping[str, Any]]] = []
    for w in words:
        cy = _bbox_center_y(w)
        if cy is not None:
            rows.append((cy, w))
    rows.sort(key=lambda r: -r[0])
    grouped, current, last_cy = [], [], None
    tol = _row_tolerance(rows)
    for cy, w in rows:
        if last_cy is None or abs(cy - last_cy) <= tol:
            current.append(w)
        else:
            grouped.append(current)
            current = [w]
        last_cy = cy
    if current:
        grouped.append(current)
    return grouped


def _line_text(line: Sequence[Mapping[str, Any]]) -> str:
    return " ".join(str(w.get("text") or "") for w in line).strip()


def _line_labels(line: Sequence[Mapping[str, Any]]) -> set[str]:
    labels: set[str] = set()
    for w in line:
        for label in w.get("labels") or []:
            labels.add(_strip_bio(label))
    return labels


def _line_is_logo(
    line: Sequence[Mapping[str, Any]], body_h_ref: float
) -> bool:
    """A line is the logo only if it is a merchant/store name AND display-size.

    Real receipts label MERCHANT_NAME on the big top wordmark; the same label can
    also tag a small footer repeat, so the height gate keeps the logo image from
    being stamped over ordinary lines."""
    if not (_line_labels(line) & set(_MERCHANT_LABELS)):
        return False
    if body_h_ref <= 0:
        return True
    return _line_max_height(line) >= body_h_ref * 1.6


def _line_is_bold(line: Sequence[Mapping[str, Any]]) -> bool:
    # Explicit emphasis hint on any word wins.
    for w in line:
        emphasis = str(w.get("emphasis") or w.get("weight") or "").lower()
        if emphasis in ("bold", "heavy"):
            return True
    text = _line_text(line).upper()
    if any(hint in text for hint in _BOLD_LINE_HINTS):
        return True
    # A short all-caps line that is exactly a category name is a section header.
    compact = text.replace("/", " ")
    if any(compact == hint or compact.startswith(hint) for hint in _CATEGORY_HINTS):
        return True
    return False


# --------------------------------------------------------------------------- #
# thermal paper + noise
# --------------------------------------------------------------------------- #


def _thermal_paper(config: GlyphRenderConfig, rng: random.Random) -> Image.Image:
    image = Image.new("RGBA", (config.width, config.height), config.paper + (255,))
    if config.noise <= 0:
        return image
    # Sparse darker speckle + faint horizontal scan banding for thermal realism.
    draw = ImageDraw.Draw(image)
    speckles = int(config.width * config.height * 0.0006 * config.noise)
    for _ in range(speckles):
        x = rng.randint(0, config.width - 1)
        y = rng.randint(0, config.height - 1)
        d = rng.randint(6, 22)
        base = config.paper
        shade = tuple(max(0, c - d) for c in base)
        draw.point((x, y), fill=shade + (255,))
    return image


def _thermal_finish(image: Image.Image, config: GlyphRenderConfig) -> Image.Image:
    out = image.convert("RGB")
    if config.blur > 0:
        out = out.filter(ImageFilter.GaussianBlur(radius=config.blur))
    return out


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


class _PixelCfg:
    """Adapter exposing the few attrs ``_to_pixel_box`` reads from a config."""

    def __init__(self, config: GlyphRenderConfig) -> None:
        self.margin = config.margin


def _union_pixel_box(
    line: Sequence[Mapping[str, Any]],
    scale: float,
    config: GlyphRenderConfig,
    inner_w: int,
    inner_h: int,
) -> tuple[float, float, float, float] | None:
    boxes = []
    for w in line:
        px = _to_pixel_box(
            w.get("bbox"), scale, _PixelCfg(config), inner_w, inner_h
        )
        if px is not None:
            boxes.append(px)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _bbox_yspan(word: Mapping[str, Any]) -> tuple[float, float] | None:
    """``(y0, y1)`` of a word's bbox, or ``None`` for a missing/degenerate box."""
    bbox = word.get("bbox")
    if isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes)):
        if len(bbox) >= 4:
            try:
                y0, y1 = float(bbox[1]), float(bbox[3])
            except (TypeError, ValueError):
                return None
            if y0 == y0 and y1 == y1:  # reject NaN
                return (y0, y1)
    return None


def _bbox_height(word: Mapping[str, Any]) -> float:
    span = _bbox_yspan(word)
    return abs(span[1] - span[0]) if span else 0.0


def _bbox_center_y(word: Mapping[str, Any]) -> float | None:
    span = _bbox_yspan(word)
    return (span[0] + span[1]) / 2 if span else None


def _median_word_height(words: Sequence[Mapping[str, Any]]) -> float:
    heights = sorted(h for h in (_bbox_height(w) for w in words) if h > 0)
    if not heights:
        return 0.0
    return heights[len(heights) // 2]


def _line_max_height(line: Sequence[Mapping[str, Any]]) -> float:
    heights = [_bbox_height(w) for w in line]
    return max(heights) if heights else 0.0


def _row_tolerance(rows: Sequence[tuple[float, Mapping[str, Any]]]) -> float:
    heights = sorted(h for h in (_bbox_height(w) for _, w in rows) if h > 0)
    if not heights:
        return 1.0
    return max(heights[len(heights) // 2] * 0.6, 1e-6)


def _fit_height(image: Image.Image, height: int) -> Image.Image:
    if image.height == height:
        return image
    w = max(1, int(image.width * height / image.height))
    return image.resize((w, height), Image.LANCZOS)


def _hstack_labeled(
    left: Image.Image, right: Image.Image, labels: tuple[str, str]
) -> Image.Image:
    gap, banner = 14, 22
    height = max(left.height, right.height) + banner
    width = left.width + right.width + gap
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(left, (0, banner))
    canvas.paste(right, (left.width + gap, banner))
    draw = ImageDraw.Draw(canvas)
    draw.text((4, 4), labels[0], fill=(0, 0, 0))
    draw.text((left.width + gap + 4, 4), labels[1], fill=(0, 0, 0))
    return canvas
