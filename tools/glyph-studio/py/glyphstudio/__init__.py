"""Glyph Studio — parametric stroke-skeleton fonts for synthetic receipts.

Sources are per-glyph JSON stroke skeletons in cap units (y-up, baseline 0,
cap height 1000). The compiler rasterizes dots along strokes and emits the
exact ``.glyphs.npz`` contract the receipt renderer's ``BitmapFont`` consumes
(``c{cp}`` uint8 ink bitmaps + ``o{cp}`` int16 baseline offsets, caps at
REF_CAP=60px). The tracer seeds skeletons from the real-receipt letterform
corpus (``*.samples.npz``) so fonts start faithful and get hand-polished.
"""

REF_CAP = 60
CAP_UNITS = 1000.0

# The renderer derives cap height / advance from these glyph sets
# (receipt_agent/.../rendering/bitmap_font.py) — keep in sync.
CAP_REF_CHARS = "ABDEFGHKLMNPRSTUVXZ"
ADVANCE_REF_CHARS = "MWHNUABDOR"
