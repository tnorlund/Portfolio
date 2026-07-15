"""Tests for the pure NumPy typeface matcher."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from receipt_chroma import (
    TypefaceFingerprint,
    match_typeface,
    shifted_iou,
    shifted_iou_stack,
)


def _encode(mask: np.ndarray) -> list[str]:
    return [
        f"{sum(int(value) << (31 - x) for x, value in enumerate(row)):08x}"
        for row in mask
    ]


def _registry() -> tuple[dict, np.ndarray, np.ndarray]:
    first = np.zeros((32, 32), dtype=bool)
    first[3:29, 14:18] = True
    second = np.zeros((32, 32), dtype=bool)
    second[14:18, 3:29] = True
    return (
        {
            "calibration": {
                "minimum_letter_crops": 2,
                "minimum_winner_iou": 0.4,
                "typeface_ambiguity_band": 0.01,
                "winner_iou_distribution": [0.4, 0.6, 0.8],
                "winner_margin_distribution": [0.1, 0.2, 0.3],
            },
            "atlases": {
                "ROM:first": {
                    "typeface": "first",
                    "merchant_candidates": ["First Store"],
                    "glyphs": {"A": _encode(first)},
                },
                "ROM:second": {
                    "typeface": "second",
                    "merchant_candidates": ["Second Store"],
                    "glyphs": {"A": _encode(second)},
                },
            },
        },
        first,
        second,
    )


def test_match_typeface_is_deterministic_and_distribution_calibrated() -> None:
    registry, first, _ = _registry()
    result = match_typeface([("A", first), ("A", first)], registry)

    assert isinstance(result, TypefaceFingerprint)
    assert result.typeface == "first"
    assert result.merchant_candidates == ("First Store",)
    assert result.letter_count == 2
    assert 0 < result.confidence <= 1
    assert result == match_typeface([("A", first), ("A", first)], registry)


def test_match_typeface_abstains_below_calibrated_crop_floor() -> None:
    registry, first, _ = _registry()
    result = match_typeface([("A", first)], registry)

    assert result.typeface is None
    assert result.merchant_candidates == ()
    assert result.confidence == 0


def test_mean_iou_breaks_median_tie_between_sibling_roms() -> None:
    shared = np.zeros((32, 32), dtype=bool)
    shared[3:29, 14:18] = True
    wrong_b = np.zeros((32, 32), dtype=bool)
    wrong_b[14:18, 3:29] = True
    right_b = np.eye(32, dtype=bool)
    registry = {
        "calibration": {
            "minimum_letter_crops": 4,
            "minimum_winner_iou": 0.4,
            "typeface_ambiguity_band": 0.01,
            "winner_iou_distribution": [0.4, 0.6, 0.8],
            "winner_margin_distribution": [0.1, 0.2, 0.3],
        },
        "atlases": {
            "ROM:a-wrong": {
                "typeface": "wrong",
                "merchant_candidates": [],
                "glyphs": {"A": _encode(shared), "B": _encode(wrong_b)},
            },
            "ROM:z-right": {
                "typeface": "right",
                "merchant_candidates": [],
                "glyphs": {"A": _encode(shared), "B": _encode(right_b)},
            },
        },
    }

    result = match_typeface(
        [("A", shared), ("A", shared), ("A", shared), ("B", right_b)],
        registry,
    )

    assert (
        result.atlas_scores["ROM:a-wrong"]
        == result.atlas_scores["ROM:z-right"]
    )
    assert result.typeface == "right"


def test_shifted_iou_matches_glyphstudio_calibrated_implementation() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "tools/glyph-studio/py"))
    from glyphstudio.glyph_score import shifted_iou_stack as studio_stack
    from glyphstudio.typography import shifted_iou as studio_iou

    registry, first, second = _registry()
    del registry
    references = np.stack([first, second])

    assert shifted_iou(first, second) == studio_iou(first, second)
    np.testing.assert_allclose(
        shifted_iou_stack(first, references),
        studio_stack(first, references),
    )
