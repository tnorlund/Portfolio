"""Tests for the pure NumPy typeface matcher."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from glyphstudio.family_cluster import normalize_glyph as studio_normalize
from glyphstudio.glyph_score import shifted_iou_stack as studio_stack
from glyphstudio.typography import clean_letter_mask as studio_clean
from glyphstudio.typography import shifted_iou as studio_iou

from receipt_chroma import (
    TypefaceFingerprint,
    clean_letter_mask,
    compute_atlas_registry_sha256,
    compute_typeface_registry_sha256,
    load_registry_atlases,
    match_typeface,
    normalize_glyph,
    shifted_iou,
    shifted_iou_stack,
)
from receipt_chroma.merchant_fingerprint import (
    TypefaceSourceScore,
    _sources_are_ambiguous,
    validate_typeface_registry,
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
    registry = {
        "schema_version": 2,
        "calibration": {
            "calibration_id": "synthetic-v2",
            "minimum_distinct_characters": 2,
            "minimum_distinct_characters_derivation": "synthetic test",
        },
        "atlases": {
            "ROM:first": {
                "kind": "rom",
                "enabled": True,
                "disabled_reason": None,
                "acceptance_threshold": 0.3,
                "acceptance_threshold_derivation": (
                    "midpoint-between-calibration-max-impostor-and-min-genuine"
                ),
                "genuine_score_distribution": [0.4, 0.6, 0.8],
                "impostor_score_distribution": [0.0, 0.1, 0.2],
                "holdout_genuine_score_distribution": [0.5],
                "holdout_impostor_score_distribution": [0.1],
                "calibration_receipt_hashes": [
                    f"first-cal-{index}" for index in range(6)
                ],
                "holdout_receipt_hashes": [
                    "first-hold-genuine",
                    "first-hold-impostor",
                ],
                "typeface": "first",
                "merchant_candidates": ["First Store"],
                "glyphs": {"A": _encode(first), "B": _encode(first)},
            },
            "ROM:second": {
                "kind": "rom",
                "enabled": True,
                "disabled_reason": None,
                "acceptance_threshold": 0.3,
                "acceptance_threshold_derivation": (
                    "midpoint-between-calibration-max-impostor-and-min-genuine"
                ),
                "genuine_score_distribution": [0.4, 0.6, 0.8],
                "impostor_score_distribution": [0.0, 0.1, 0.2],
                "holdout_genuine_score_distribution": [0.5],
                "holdout_impostor_score_distribution": [0.1],
                "calibration_receipt_hashes": [
                    f"second-cal-{index}" for index in range(6)
                ],
                "holdout_receipt_hashes": [
                    "second-hold-genuine",
                    "second-hold-impostor",
                ],
                "typeface": "second",
                "merchant_candidates": ["Second Store"],
                "glyphs": {"A": _encode(second), "B": _encode(second)},
            },
        },
    }
    _seal(registry)
    return registry, first, second


def _seal(registry: dict) -> None:
    registry["atlas_registry_sha256"] = compute_atlas_registry_sha256(registry)
    registry["registry_sha256"] = compute_typeface_registry_sha256(registry)


def test_match_typeface_is_deterministic_and_distribution_calibrated() -> None:
    registry, first, _ = _registry()
    result = match_typeface([("A", first), ("B", first)], registry)

    assert isinstance(result, TypefaceFingerprint)
    assert result.typeface == "first"
    assert result.merchant_candidates == ("First Store",)
    assert result.typeface_candidates == ("first",)
    assert result.letter_count == 2
    assert result.matched_letter_count == 2
    assert result.distinct_character_count == 2
    assert result.abstention_reason is None
    assert 0 < result.confidence <= 1
    assert result == match_typeface([("B", first), ("A", first)], registry)


def test_match_typeface_abstains_below_distinct_character_floor() -> None:
    registry, first, _ = _registry()
    result = match_typeface([("A", first)], registry)

    assert result.typeface is None
    assert result.merchant_candidates == ()
    assert result.confidence == 0
    assert result.abstention_reason == "INSUFFICIENT_DISTINCT_CHARACTERS"


def test_repeated_single_letterform_never_becomes_evidence() -> None:
    registry, first, _ = _registry()

    result = match_typeface([("A", first)] * 600, registry)

    assert result.typeface is None
    assert result.letter_count == 600
    assert result.distinct_character_count == 1
    assert result.abstention_reason == "INSUFFICIENT_DISTINCT_CHARACTERS"


def test_significance_in_the_opposite_direction_remains_ambiguous() -> None:
    chars = "ABCDEFGHIJK"
    left_values = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.9]
    right_values = [
        0.1,
        0.81,
        0.71,
        0.61,
        0.51,
        0.41,
        0.31,
        0.21,
        0.11,
        0.06,
        0.9,
    ]
    left = TypefaceSourceScore(
        score=float(np.median(left_values)),
        per_character=dict(zip(chars, left_values, strict=True)),
        matched_letter_count=len(chars),
    )
    right = TypefaceSourceScore(
        score=float(np.median(right_values)),
        per_character=dict(zip(chars, right_values, strict=True)),
        matched_letter_count=len(chars),
    )

    assert left.score > right.score
    assert _sources_are_ambiguous(left, right, minimum_distinct=6)


def test_same_typeface_sources_do_not_invent_source_confidence() -> None:
    registry, first, _ = _registry()
    registry["atlases"]["ROM:second"] = {
        **registry["atlases"]["ROM:first"],
        "merchant_candidates": ["Second Store"],
    }
    _seal(registry)

    result = match_typeface([("A", first), ("B", first)], registry)

    assert result.typeface_candidates == ("first",)
    assert result.typeface is None
    assert result.confidence == 0
    assert result.abstention_reason == "AMBIGUOUS_SOURCES"


def test_enabled_source_requires_measured_separation() -> None:
    registry, _, _ = _registry()
    registry["atlases"]["ROM:first"]["impostor_score_distribution"][-1] = 0.5

    with pytest.raises(ValueError, match="distributions overlap"):
        validate_typeface_registry(registry)


def test_disabled_source_cannot_carry_an_acceptance_threshold() -> None:
    registry, _, _ = _registry()
    entry = deepcopy(registry["atlases"]["ROM:first"])
    entry["enabled"] = False
    entry["disabled_reason"] = "calibration distributions overlap"
    registry["atlases"] = {"ROM:first": entry}

    with pytest.raises(ValueError, match="threshold must be null"):
        validate_typeface_registry(registry)


def test_registry_rejects_glyph_tampering_against_measurement_hash() -> None:
    registry, _, _ = _registry()
    registry["atlases"]["ROM:first"]["glyphs"]["A"] = "0" * 256

    with pytest.raises(ValueError, match="atlas hash"):
        validate_typeface_registry(registry)


def test_cached_registry_configuration_mutation_is_revalidated() -> None:
    registry, first, _ = _registry()
    registry["calibration"]["minimum_distinct_characters"] = 1

    with pytest.raises(ValueError, match="registry hash"):
        match_typeface([("A", first), ("B", first)], registry)


def test_decoded_atlas_cache_is_content_addressed_and_deeply_immutable() -> (
    None
):
    registry, first, _ = _registry()
    atlases = load_registry_atlases(registry)
    first_result = match_typeface([("A", first), ("B", first)], registry)

    with pytest.raises(TypeError):
        atlases["ROM:first"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        atlases["ROM:first"]["A"] = first  # type: ignore[index]
    with pytest.raises(ValueError):
        atlases["ROM:first"]["A"].setflags(write=True)

    registry["_decoded_atlases"] = {"ROM:first": {"A": np.zeros((32, 32))}}
    assert load_registry_atlases(registry) is atlases
    assert (
        match_typeface([("A", first), ("B", first)], registry) == first_result
    )


def test_registry_rejects_incomplete_score_populations() -> None:
    registry, _, _ = _registry()
    registry["atlases"]["ROM:first"]["calibration_receipt_hashes"].pop()
    _seal(registry)

    with pytest.raises(
        ValueError, match="calibration evidence count mismatch"
    ):
        validate_typeface_registry(registry)


def test_glyph_primitives_are_canonical_glyphstudio_exports() -> None:
    assert clean_letter_mask is studio_clean
    assert normalize_glyph is studio_normalize
    assert shifted_iou is studio_iou
    assert shifted_iou_stack is studio_stack
