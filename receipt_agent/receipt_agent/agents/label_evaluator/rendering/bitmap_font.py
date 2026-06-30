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


class BitmapFont:
    def __init__(self, atlas_path: str, advance_ratio: float | None = None):
        data = np.load(atlas_path)
        self.glyphs = {chr(int(k[1:])): np.asarray(data[k])
                       for k in data.files if k.startswith("c")}
        # baseline offset per char (glyph bottom relative to the row baseline;
        # caps 0, hyphen/= negative=above, descenders positive=below).
        self.offsets = {chr(int(k[1:])): int(data[k])
                        for k in data.files if k.startswith("o")}
        caps = [self.glyphs[c].shape[0] for c in _CAP_REF if c in self.glyphs]
        self.cap_h = float(np.median(caps)) if caps else 20.0
        # Monospace advance: derive from the actual glyph widths (bitMatrix is a
        # CONDENSED face) -- the widest letters + one thin gap -- not the padded
        # chart cell. Override via advance_ratio if given.
        if advance_ratio is None:
            wide = [self.glyphs[c].shape[1] for c in "MWHNUABDOR" if c in self.glyphs]
            gw = float(np.median(wide)) if wide else self.cap_h * 0.7
            advance_ratio = (gw + 2.0) / self.cap_h
        self._advance_ratio = advance_ratio
        self._cache: dict[tuple[str, int], tuple] = {}

    def advance(self, cap_px: float) -> float:
        return cap_px * self._advance_ratio

    def glyph(self, ch: str, cap_px: int):
        """Return (PIL glyph, height_px, baseline_offset_px) scaled to ``cap_px``, or None.

        baseline_offset_px is added to the row baseline to place the glyph BOTTOM
        (0 = sits on the baseline; negative = above, e.g. a hyphen; positive =
        descender below).
        """
        src = ch
        g = self.glyphs.get(ch)
        if g is None and ch.isalpha():
            src = ch.upper() if ch.upper() in self.glyphs else ch.lower()
            g = self.glyphs.get(src)
        if g is None:
            return None
        key = (ch, int(cap_px))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        scale = cap_px / self.cap_h
        h = max(1, int(round(g.shape[0] * scale)))
        w = max(1, int(round(g.shape[1] * scale)))
        im = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8)).resize((w, h))
        off = int(round(self.offsets.get(src, 0) * scale))
        self._cache[key] = (im, h, off)
        return self._cache[key]

    def has(self, ch: str) -> bool:
        return ch in self.glyphs or (ch.isalpha() and (
            ch.upper() in self.glyphs or ch.lower() in self.glyphs))
