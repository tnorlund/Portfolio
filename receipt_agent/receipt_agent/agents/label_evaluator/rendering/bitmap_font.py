"""Bitmap (glyph-atlas) font for the grid renderer.

Wraps a per-character glyph atlas (extracted from a real font chart, e.g. the
Costco bitMatrix-C2 family) so the grid path can render in the merchant's ACTUAL
letterforms instead of an off-the-shelf TTF. Glyphs are scaled by a shared cap
height (preserving relative sizes) and baseline-aligned to the row baseline.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# Uppercase letters with a flat cap height -- the scale reference.
_CAP_REF = "ABDEFGHKLMNPRSTUVXZ"


_PRESERVE_TOP_ARC = frozenset("oceCO0QG")


def preserve_top_arc(ch: str) -> bool:
    return str(ch or "")[:1] in _PRESERVE_TOP_ARC


def thin_ink_mask(
    mask: Image.Image, amount: float, *, preserve_top: bool = False
) -> Image.Image:
    """Deterministically remove a fraction of edge pixels from a binary glyph.

    Real thermal scans have ragged, incomplete stroke edges; the data-built
    consensus atlas is binary and slightly too solid. Removing only boundary
    pixels lowers ink density without changing the OCR-derived cap or advance.
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0 or mask.width <= 2 or mask.height <= 2:
        return mask
    amount = min(0.9, amount)
    arr = np.asarray(mask).copy()
    ink = arr > 0
    if not ink.any():
        return mask
    padded = np.pad(ink, 1, mode="constant", constant_values=False)
    solid_neighbors = (
        padded[1:-1, :-2]
        & padded[1:-1, 2:]
        & padded[:-2, 1:-1]
        & padded[2:, 1:-1]
    )
    edge = ink & ~solid_neighbors
    yy, xx = np.indices(ink.shape)
    period = max(2, int(round(1.0 / amount)))
    drop = (
        (xx * 17 + yy * 31 + mask.width * 7 + mask.height * 11) % period
    ) == 0
    if preserve_top:
        drop &= yy >= max(1, int(round(mask.height * 0.32)))
    arr[edge & drop] = 0
    return Image.fromarray(arr.astype(np.uint8), "L")


class BitmapFont:
    def __init__(
        self,
        atlas_path: str,
        advance_ratio: float | None = None,
        *,
        thin: float = 0.0,
        vscale: float = 1.0,
    ):
        data = np.load(atlas_path)
        self.glyphs = {
            chr(int(k[1:])): np.asarray(data[k])
            for k in data.files
            if k.startswith("c")
        }
        # baseline offset per char (glyph bottom relative to the row baseline;
        # caps 0, hyphen/= negative=above, descenders positive=below).
        self.offsets = {
            chr(int(k[1:])): int(data[k])
            for k in data.files
            if k.startswith("o")
        }
        caps = [self.glyphs[c].shape[0] for c in _CAP_REF if c in self.glyphs]
        self.cap_h = float(np.median(caps)) if caps else 20.0
        # Monospace advance: derive from the actual glyph widths (bitMatrix is a
        # CONDENSED face) -- the widest letters + one thin gap -- not the padded
        # chart cell. Override via advance_ratio if given.
        if advance_ratio is None:
            wide = [
                self.glyphs[c].shape[1]
                for c in "MWHNUABDOR"
                if c in self.glyphs
            ]
            gw = float(np.median(wide)) if wide else self.cap_h * 0.7
            advance_ratio = (gw + 2.0) / self.cap_h
        self._advance_ratio = advance_ratio
        self.thin = max(0.0, min(0.9, float(thin or 0.0)))
        # I2 cap-height correction: vertical-only glyph scale, applied about
        # the baseline AFTER cap_px sizing. Layout (cell advance, line pitch,
        # condense) is intentionally untouched -- the measured Costco gap is
        # glyphs ~17% too TALL at correct pitch (GOLD_STANDARD.md I2), so the
        # fix must be vertical-only. 1.0 (default) is byte-identical.
        self.vscale = max(0.5, min(1.5, float(vscale or 1.0)))
        self._cache: dict[tuple[str, int], tuple] = {}

    def advance(self, cap_px: float) -> float:
        return cap_px * self._advance_ratio

    def glyph(self, ch: str, cap_px: int):
        """Return (PIL glyph, height_px, baseline_offset_px) scaled to ``cap_px``, or None.

        baseline_offset_px is added to the row baseline to place the glyph BOTTOM
        (0 = sits on the baseline; negative = above, e.g. a hyphen; positive =
        descender below).
        """
        g = self.glyphs.get(ch)
        if g is None:
            return None
        key = (ch, int(cap_px), round(self.thin, 3), round(self.vscale, 4))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        scale = cap_px / self.cap_h
        h = max(1, int(round(g.shape[0] * scale)))
        w = max(1, int(round(g.shape[1] * scale)))
        # NEAREST, not the default bicubic: bitMatrix is a hard bitmap face, and
        # scaling an enlarged heading (SELF-CHECKOUT, TOTAL) with a smoothing
        # filter rounds its square dot-matrix strokes into a generic bold blob --
        # losing the very letterform that identifies the font. Nearest keeps the
        # crisp blocky pixels; the paper-texture pass adds thermal bleed on top.
        im = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8)).resize(
            (w, h), Image.NEAREST
        )
        im = thin_ink_mask(im, self.thin, preserve_top=preserve_top_arc(ch))
        off = int(round(self.offsets.get(ch, 0) * scale))
        if self.vscale != 1.0:
            # Vertical-only resize of the FINAL (thinned) mask; NEAREST keeps
            # the hard bitmap dots. Baseline offsets scale with the glyph so
            # descenders/hyphens keep their relative position.
            h = max(1, int(round(im.height * self.vscale)))
            im = im.resize((im.width, h), Image.NEAREST)
            off = int(round(off * self.vscale))
        self._cache[key] = (im, h, off)
        return self._cache[key]

    def has(self, ch: str) -> bool:
        return ch in self.glyphs
