"""Pure NumPy merchant/typeface fingerprint matching."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

import numpy as np

from receipt_chroma.glyph_matching import normalize_glyph, shifted_iou


@dataclass(frozen=True)
class TypefaceFingerprint:
    """One calibrated upload fingerprint result."""

    merchant_candidates: tuple[str, ...]
    typeface: str | None
    confidence: float
    letter_count: int
    atlas_scores: dict[str, float]


def decode_glyph_rows(rows: Sequence[str] | str) -> np.ndarray:
    """Decode fixed-width hexadecimal bitmap rows into a boolean mask."""

    if isinstance(rows, str):
        if len(rows) % 8:
            raise ValueError(
                "compact glyph data must contain 8 hex digits per row"
            )
        rows = [rows[index : index + 8] for index in range(0, len(rows), 8)]
    if not rows:
        raise ValueError("glyph rows must not be empty")
    width = len(rows[0]) * 4
    if width <= 0 or any(len(row) * 4 != width for row in rows):
        raise ValueError("glyph rows must have one consistent width")
    result: np.ndarray = np.zeros((len(rows), width), dtype=bool)
    for y, row in enumerate(rows):
        value = int(row, 16)
        for x in range(width):
            result[y, x] = bool(value & (1 << (width - x - 1)))
    return result


def load_registry_atlases(
    registry: Mapping[str, Any],
) -> dict[str, dict[str, np.ndarray]]:
    """Decode a JSON registry into in-memory NumPy atlases."""

    return {
        name: {
            char: decode_glyph_rows(rows)
            for char, rows in entry["glyphs"].items()
        }
        for name, entry in registry["atlases"].items()
    }


def _empirical_confidence(
    value: float, distribution: Sequence[float]
) -> float:
    values: np.ndarray = np.asarray(distribution, dtype=float)
    if values.size == 0:
        return 0.0
    below = int((values < value).sum())
    equal = int((values == value).sum())
    return float((below + (equal + 1) / 2.0) / (values.size + 1))


def match_typeface(
    letter_crops: Sequence[tuple[str, np.ndarray]],
    registry: Mapping[str, Any],
) -> TypefaceFingerprint:
    """Score an upload against JSON registry atlases with shifted IoU.

    Acceptance, ambiguity, evidence, and confidence distributions are read
    from the crop-calibrated registry; the matcher contains no hand-set score
    cutoffs.
    """

    calibration = registry["calibration"]
    normalized = [
        (char, normalize_glyph(mask))
        for char, mask in letter_crops
        if len(char) == 1 and char.strip() and np.asarray(mask).any()
    ]
    atlases = load_registry_atlases(registry)
    atlas_scores: dict[str, float] = {}
    atlas_counts: dict[str, int] = {}
    for name, atlas in atlases.items():
        scores = [
            shifted_iou(mask, atlas[char])
            for char, mask in normalized
            if char in atlas
        ]
        if scores:
            atlas_scores[name] = float(median(scores))
            atlas_counts[name] = len(scores)

    evidence_floor = int(calibration["minimum_letter_crops"])
    eligible = {
        name: score
        for name, score in atlas_scores.items()
        if atlas_counts[name] >= evidence_floor
    }
    if not eligible:
        return TypefaceFingerprint(
            (), None, 0.0, len(normalized), atlas_scores
        )

    typeface_scores: dict[str, float] = defaultdict(float)
    for name, score in eligible.items():
        typeface = str(registry["atlases"][name]["typeface"])
        typeface_scores[typeface] = max(typeface_scores[typeface], score)
    ranked = sorted(
        typeface_scores.items(), key=lambda item: (-item[1], item[0])
    )
    best_typeface, best_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - runner_score
    if best_score < float(calibration["minimum_winner_iou"]):
        return TypefaceFingerprint(
            (), None, 0.0, len(normalized), atlas_scores
        )

    ambiguity_band = float(calibration["typeface_ambiguity_band"])
    plausible_typefaces = {
        typeface
        for typeface, score in ranked
        if best_score - score <= ambiguity_band
    }
    merchants = sorted(
        {
            merchant
            for entry in registry["atlases"].values()
            if entry["typeface"] in plausible_typefaces
            for merchant in entry.get("merchant_candidates", [])
        }
    )
    score_confidence = _empirical_confidence(
        best_score, calibration["winner_iou_distribution"]
    )
    margin_confidence = _empirical_confidence(
        margin, calibration["winner_margin_distribution"]
    )
    confidence = float((score_confidence + margin_confidence) / 2.0)
    return TypefaceFingerprint(
        tuple(merchants),
        best_typeface,
        confidence,
        len(normalized),
        atlas_scores,
    )


__all__ = [
    "TypefaceFingerprint",
    "decode_glyph_rows",
    "load_registry_atlases",
    "match_typeface",
]
