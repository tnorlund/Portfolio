"""Embedding-matched TTF fallback for glyphs the atlas lacks (M3).

A real glyph atlas (M1) only covers the characters a merchant happened to print.
When a synthesized receipt needs a character the atlas is missing, this module
renders it from a monospace TTF — choosing the TTF whose letterforms most
resemble the merchant's *real* glyphs, so the substitute does not jar against the
stamped originals.

"Embedding-matched", honestly scoped
------------------------------------
The charter points at #994's Chroma letter embeddings / an HF vision embedder
(DINOv2, CLIP) as the matcher. Those need network/model weights that are not
available in every run, and DeepFont is not trained on thermal fonts anyway. So
the default matcher here is a self-contained **visual similarity**: render each
candidate font's glyphs for the characters the atlas DOES have, reduce both the
candidate and the real glyph to a small normalized ink vector, and score by mean
cosine similarity. It is a pixel-embedding match, not a learned one — it picks
the closest available monospace, which is all the fallback needs. A learned
embedder can be dropped in via the ``score_font`` hook without touching callers.

This is review-realism DATA only (CHARTER.md): it renders substitute glyphs for
human-review images; it never feeds a training gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Mapping, Sequence

from PIL import Image, ImageFilter, ImageFont

from receipt_agent.agents.label_evaluator.rendering.glyph_atlas import (
    AtlasStyle,
    GlyphAtlas,
    GlyphCrop,
)

# Monospace candidates, most receipt-like first. None are true thermal fonts
# (those are not freely available); the matcher picks the closest of these.
THERMAL_TTF_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/PTMono.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)

_SIM_GLYPH_SIZE = 16  # side of the normalized ink vector used for matching
_FIXED_PITCH_PROBE_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZilWm.:$"
_FIXED_PITCH_TOLERANCE_PX = 1.25
_ADVANCE_PROBE_CHARS = "0MW"
_FALLBACK_ALPHA_GAIN = 1.18
_FALLBACK_ADVANCE_PAD_PX = 2


@dataclass(frozen=True)
class FontMatch:
    """The chosen fallback font and how well it matched the atlas."""

    font_path: str | None
    score: float
    scores: dict[str, float]  # path -> mean similarity (for transparency)
    probe_chars: tuple[str, ...]


def available_candidates(
    candidates: Sequence[str] = THERMAL_TTF_CANDIDATES,
) -> list[str]:
    return [
        path
        for path in candidates
        if os.path.exists(path) and _is_fixed_pitch_font(path)
    ]


def match_fallback_font(
    atlas: GlyphAtlas,
    *,
    candidates: Sequence[str] = THERMAL_TTF_CANDIDATES,
    max_probe_chars: int = 24,
    score_font: "Callable[[str, Mapping[str, GlyphCrop]], float] | None" = None,
) -> FontMatch:
    """Pick the candidate TTF whose glyphs best match the atlas's real glyphs.

    Probes on the characters the atlas already has (preferring letters/digits),
    so the score reflects how close a candidate is to the merchant's true font.
    """
    body = atlas.styles.get("body") or next(iter(atlas.styles.values()), None)
    paths = available_candidates(candidates)
    if body is None or not paths:
        return FontMatch(paths[0] if paths else None, 0.0, {}, ())

    probe = _probe_glyphs(body, max_probe_chars)
    if not probe:
        return FontMatch(paths[0], 0.0, {path: 0.0 for path in paths}, ())

    real_vectors = {
        char: _alpha_vector(crop.image.getchannel("A"))
        for char, crop in probe.items()
    }
    scores: dict[str, float] = {}
    for path in paths:
        if score_font is not None:
            scores[path] = score_font(path, probe)
            continue
        scores[path] = _mean_similarity(path, probe, real_vectors)

    best_path = max(scores, key=lambda p: scores[p])
    return FontMatch(
        font_path=best_path,
        score=scores[best_path],
        scores=scores,
        probe_chars=tuple(sorted(probe)),
    )


def make_ttf_fallback(
    atlas: GlyphAtlas,
    *,
    font_path: str | None = None,
    candidates: Sequence[str] = THERMAL_TTF_CANDIDATES,
    ink: tuple[int, int, int] = (38, 36, 34),
    cap_ratio: float = 0.7,
) -> "Callable[[str, AtlasStyle, int], Image.Image | None]":
    """Build a glyph fallback (the renderer's ``fallback`` hook) for an atlas.

    Picks the best-matching font once (unless ``font_path`` is given) and returns
    ``fallback(char, style, px_height)`` rendering a missing character from that
    font, ink-on-transparent, sized so a capital lands at ~``px_height``. The
    returned image reserves a full monospace advance, not just the tight ink
    bounds, so fallback characters stay on the renderer's fixed receipt grid.
    """
    if font_path is None:
        font_path = match_fallback_font(atlas, candidates=candidates).font_path

    cache: dict[tuple[str, int, bool], Image.Image | None] = {}

    def fallback(
        char: str, style: AtlasStyle, px_height: int
    ) -> Image.Image | None:
        if not char.strip() or px_height <= 1 or font_path is None:
            return None
        bold = style.weight == "bold"
        key = (char, int(px_height), bold)
        if key in cache:
            cached = cache[key]
            return cached.copy() if cached is not None else None
        glyph = _render_ttf_glyph(
            char, font_path, px_height, ink=ink, cap_ratio=cap_ratio, bold=bold
        )
        cache[key] = glyph
        return glyph.copy() if glyph is not None else None

    return fallback


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _probe_glyphs(
    style: AtlasStyle, limit: int
) -> dict[str, GlyphCrop]:
    """Pick representative real glyphs to match against (letters/digits first)."""
    preferred = [c for c in style.chars() if c.isalnum()]
    ordered = sorted(preferred) or sorted(style.chars())
    probe: dict[str, GlyphCrop] = {}
    for char in ordered[:limit]:
        crop = style.glyph_for(char)
        if crop is not None:
            probe[char] = crop
    return probe


_TTF_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _load_ttf(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    size = max(4, int(size))
    key = (path, size)
    cached = _TTF_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        font = ImageFont.truetype(path, size)
    except OSError:
        return None
    _TTF_CACHE[key] = font
    return font


@lru_cache(maxsize=64)
def _is_fixed_pitch_font(path: str) -> bool:
    """Reject proportional/script faces before visual matching can select them."""
    font = _load_ttf(path, 32)
    if font is None:
        return False
    widths = [
        _advance_width(font, char)
        for char in _FIXED_PITCH_PROBE_CHARS
    ]
    widths = [width for width in widths if width > 0]
    if not widths:
        return False
    return max(widths) - min(widths) <= _FIXED_PITCH_TOLERANCE_PX


def _advance_width(font: ImageFont.FreeTypeFont, char: str) -> float:
    try:
        return float(font.getlength(char))
    except AttributeError:
        bbox = font.getbbox(char)
        return float(bbox[2] - bbox[0])


def _render_ttf_glyph(
    char: str,
    font_path: str,
    px_height: int,
    *,
    ink: tuple[int, int, int],
    cap_ratio: float,
    bold: bool,
) -> Image.Image | None:
    """Render one character to a tight ink-on-transparent RGBA at cap≈px_height."""
    size = max(6, int(round(px_height / max(cap_ratio, 0.1))))
    font = _load_ttf(font_path, size)
    if font is None:
        return None
    # Draw on a generous scratch layer, then crop to ink.
    scratch = Image.new("L", (size * 2, size * 2), 0)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(scratch)
    draw.text((size // 2, size // 2), char, fill=255, font=font)
    if bold:  # fake-bold: overlay a 1px-offset copy
        draw.text((size // 2 + 1, size // 2), char, fill=255, font=font)
    bbox = scratch.getbbox()
    if bbox is None:
        return None
    alpha = _thermal_stroke_alpha(scratch.crop(bbox))
    advance_w = _fallback_advance_width(font, char, alpha.width)
    alpha = _pad_to_advance(alpha, advance_w)
    glyph = Image.new("RGBA", alpha.size, ink + (0,))
    glyph.putalpha(alpha)
    return glyph


def _thermal_stroke_alpha(alpha: Image.Image) -> Image.Image:
    """Make matched-TTF fallback strokes read like dark thermal receipt ink."""
    if alpha.width <= 1 or alpha.height <= 1:
        return alpha.point(
            lambda value: min(255, int(value * _FALLBACK_ALPHA_GAIN))
        )
    padded = Image.new("L", (alpha.width + 2, alpha.height + 2), 0)
    padded.paste(alpha, (1, 1))
    stroked = padded.filter(ImageFilter.MaxFilter(3))
    return stroked.point(
        lambda value: min(255, int(value * _FALLBACK_ALPHA_GAIN))
    )


def _fallback_advance_width(
    font: ImageFont.FreeTypeFont, char: str, ink_width: int
) -> int:
    widths = [
        int(round(_advance_width(font, probe)))
        for probe in (*_ADVANCE_PROBE_CHARS, char)
    ]
    widths = [width for width in widths if width > 0]
    fixed_advance = max(widths, default=1) + _FALLBACK_ADVANCE_PAD_PX
    return max(ink_width, fixed_advance)


def _pad_to_advance(alpha: Image.Image, advance_w: int) -> Image.Image:
    advance_w = max(alpha.width, int(advance_w))
    if advance_w == alpha.width:
        return alpha
    out = Image.new("L", (advance_w, alpha.height), 0)
    out.paste(alpha, ((advance_w - alpha.width) // 2, 0))
    return out


def _mean_similarity(
    font_path: str,
    probe: Mapping[str, GlyphCrop],
    real_vectors: Mapping[str, tuple[float, ...]],
) -> float:
    font = _load_ttf(font_path, _SIM_GLYPH_SIZE * 3)
    if font is None:
        return 0.0
    sims: list[float] = []
    for char, real_vec in real_vectors.items():
        cand = _render_plain_alpha(char, font)
        if cand is None:
            continue
        sims.append(_cosine(real_vec, _alpha_vector(cand)))
    if not sims:
        return 0.0
    return sum(sims) / len(sims)


def _render_plain_alpha(
    char: str, font: ImageFont.FreeTypeFont
) -> Image.Image | None:
    from PIL import ImageDraw

    size = _SIM_GLYPH_SIZE * 3
    scratch = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(scratch).text(
        (size // 2, size // 2), char, fill=255, font=font
    )
    bbox = scratch.getbbox()
    if bbox is None:
        return None
    return scratch.crop(bbox)


def _alpha_vector(alpha: Image.Image) -> tuple[float, ...]:
    """Normalize an alpha image to a square ink vector (shape, not size/aspect)."""
    side = max(alpha.width, alpha.height, 1)
    square = Image.new("L", (side, side), 0)
    square.paste(alpha, ((side - alpha.width) // 2, (side - alpha.height) // 2))
    small = square.resize((_SIM_GLYPH_SIZE, _SIM_GLYPH_SIZE), Image.BOX)
    return tuple(v / 255.0 for v in small.getdata())


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)
