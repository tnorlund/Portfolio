"""Compatibility facade for canonical glyphstudio matching primitives."""

from glyphstudio.family_cluster import normalize_glyph
from glyphstudio.glyph_score import shifted_iou_stack
from glyphstudio.typography import clean_letter_mask, shifted_iou

__all__ = [
    "clean_letter_mask",
    "normalize_glyph",
    "shifted_iou",
    "shifted_iou_stack",
]
