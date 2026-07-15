"""Measured receipt typeface matching over canonical glyphstudio shapes."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from statistics import median
from types import MappingProxyType
from typing import Any

import numpy as np

from receipt_chroma.glyph_matching import normalize_glyph, shifted_iou

CONFIDENCE_BASIS = "source-relative-genuine-score-empirical-midrank"


@dataclass(frozen=True)
class TypefaceFingerprint:
    """One measured upload fingerprint result."""

    merchant_candidates: tuple[str, ...]
    typeface_candidates: tuple[str, ...]
    typeface: str | None
    confidence: float
    confidence_basis: str
    calibration_id: str
    letter_count: int
    matched_letter_count: int
    distinct_character_count: int
    atlas_scores: dict[str, float]
    abstention_reason: str | None


@dataclass(frozen=True)
class TypefaceSourceScore:
    """Distinct-character evidence for one atlas."""

    score: float
    per_character: dict[str, float]
    matched_letter_count: int


def compute_atlas_registry_sha256(registry: Mapping[str, Any]) -> str:
    """Hash the pinned atlas identity fields used by calibration."""

    atlases = registry.get("atlases")
    if not isinstance(atlases, Mapping):
        raise ValueError("typeface registry atlases must be a mapping")
    identity = {
        name: {
            field: entry[field]
            for field in ("kind", "typeface", "merchant_candidates", "glyphs")
        }
        for name, entry in atlases.items()
    }
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def compute_typeface_registry_sha256(registry: Mapping[str, Any]) -> str:
    """Hash all persisted runtime configuration, excluding decoded caches."""

    payload = {
        key: value
        for key, value in registry.items()
        if key != "registry_sha256" and not key.startswith("_")
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


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


@lru_cache(maxsize=8)
def _decode_registry_atlases(
    encoded_glyphs: str,
) -> Mapping[str, Mapping[str, np.ndarray]]:
    """Decode one content-addressed glyph payload into immutable mappings."""

    glyphs = json.loads(encoded_glyphs)
    decoded = {}
    for name, atlas in glyphs.items():
        immutable_atlas = {}
        for char, rows in atlas.items():
            mask = decode_glyph_rows(rows)
            immutable_atlas[char] = np.frombuffer(
                mask.tobytes(), dtype=np.bool_
            ).reshape(mask.shape)
        decoded[name] = MappingProxyType(immutable_atlas)
    return MappingProxyType(decoded)


def load_registry_atlases(
    registry: Mapping[str, Any],
) -> Mapping[str, Mapping[str, np.ndarray]]:
    """Decode hashed glyph JSON through a deeply immutable shared cache."""

    atlas_hash = registry.get("atlas_registry_sha256")
    if atlas_hash != compute_atlas_registry_sha256(registry):
        raise ValueError("typeface registry atlas hash does not match glyphs")
    glyphs = {
        name: entry["glyphs"] for name, entry in registry["atlases"].items()
    }
    encoded = json.dumps(glyphs, sort_keys=True, separators=(",", ":"))
    return _decode_registry_atlases(encoded)


def _score_distribution(entry: Mapping[str, Any], name: str) -> list[float]:
    raw = entry.get(name)
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list")
    values = []
    for value in raw:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise ValueError(f"{name} must contain finite scores in [0, 1]")
        values.append(float(value))
    return values


def _receipt_hashes(entry: Mapping[str, Any], name: str) -> set[str]:
    raw = entry.get(name)
    if not isinstance(raw, list) or any(
        not isinstance(value, str) or not value for value in raw
    ):
        raise ValueError(f"{name} must contain non-empty strings")
    return set(raw)


# Keep the complete cross-field schema contract visible in one validation pass.
def validate_typeface_registry(  # pylint: disable=too-many-statements
    registry: Mapping[str, Any],
) -> None:
    """Reject runtime registries that do not prove enabled sources."""

    if registry.get("schema_version") != 2:
        raise ValueError("typeface registry schema_version must be 2")
    calibration = registry.get("calibration")
    if not isinstance(calibration, Mapping):
        raise ValueError("typeface registry calibration must be a mapping")
    calibration_id = calibration.get("calibration_id")
    if not isinstance(calibration_id, str) or not calibration_id:
        raise ValueError("calibration_id must be a non-empty string")
    minimum_distinct = calibration.get("minimum_distinct_characters")
    if (
        isinstance(minimum_distinct, bool)
        or not isinstance(minimum_distinct, int)
        or minimum_distinct <= 0
    ):
        raise ValueError("minimum_distinct_characters must be positive")
    derivation = calibration.get("minimum_distinct_characters_derivation")
    if not isinstance(derivation, str) or not derivation:
        raise ValueError(
            "minimum_distinct_characters_derivation must be documented"
        )
    atlases = registry.get("atlases")
    if not isinstance(atlases, Mapping) or not atlases:
        raise ValueError(
            "typeface registry atlases must be a non-empty mapping"
        )
    for name, entry in atlases.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(entry, Mapping)
        ):
            raise ValueError("atlas entries must be named mappings")
        if not isinstance(entry.get("enabled"), bool):
            raise ValueError(f"{name} enabled must be boolean")
        if not isinstance(entry.get("typeface"), str) or not entry["typeface"]:
            raise ValueError(f"{name} typeface must be a non-empty string")
        candidates = entry.get("merchant_candidates")
        if not isinstance(candidates, list) or any(
            not isinstance(value, str) or not value for value in candidates
        ):
            raise ValueError(f"{name} merchant_candidates must be strings")
        glyphs = entry.get("glyphs")
        if not isinstance(glyphs, Mapping) or not glyphs:
            raise ValueError(f"{name} glyphs must be a non-empty mapping")
        genuine = _score_distribution(entry, "genuine_score_distribution")
        impostor = _score_distribution(entry, "impostor_score_distribution")
        holdout_genuine = _score_distribution(
            entry, "holdout_genuine_score_distribution"
        )
        holdout_impostor = _score_distribution(
            entry, "holdout_impostor_score_distribution"
        )
        calibration_hashes = _receipt_hashes(
            entry, "calibration_receipt_hashes"
        )
        holdout_hashes = _receipt_hashes(entry, "holdout_receipt_hashes")
        if len(genuine) + len(impostor) != len(calibration_hashes):
            raise ValueError(f"{name} calibration evidence count mismatch")
        if len(holdout_genuine) + len(holdout_impostor) != len(holdout_hashes):
            raise ValueError(f"{name} holdout evidence count mismatch")
        if calibration_hashes & holdout_hashes:
            raise ValueError(
                f"{name} calibration and holdout receipts overlap"
            )
        if not entry["enabled"]:
            if entry.get("acceptance_threshold") is not None:
                raise ValueError(f"{name} disabled threshold must be null")
            disabled_reason = entry.get("disabled_reason")
            if not isinstance(disabled_reason, str) or not disabled_reason:
                raise ValueError(f"{name} disabled_reason must be documented")
            continue

        if entry.get("disabled_reason") is not None:
            raise ValueError(
                f"{name} enabled source cannot have a disabled reason"
            )
        if not all((genuine, impostor, holdout_genuine, holdout_impostor)):
            raise ValueError(f"{name} enabled evidence must not be empty")
        max_impostor = max(impostor)
        min_genuine = min(genuine)
        if max_impostor >= min_genuine:
            raise ValueError(f"{name} calibration distributions overlap")
        threshold = entry.get("acceptance_threshold")
        expected = (max_impostor + min_genuine) / 2.0
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isclose(
                float(threshold),
                expected,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise ValueError(f"{name} threshold is not the measured midpoint")
        if entry.get("acceptance_threshold_derivation") != (
            "midpoint-between-calibration-max-impostor-and-min-genuine"
        ):
            raise ValueError(f"{name} threshold derivation is unsupported")
        if (
            min(holdout_genuine) < expected
            or max(holdout_impostor) >= expected
        ):
            raise ValueError(f"{name} untouched holdout does not pass")
        if not calibration_hashes or not holdout_hashes:
            raise ValueError(f"{name} receipt evidence must not be empty")
    atlas_hash = registry.get("atlas_registry_sha256")
    if atlas_hash != compute_atlas_registry_sha256(registry):
        raise ValueError("typeface registry atlas hash does not match glyphs")
    registry_hash = registry.get("registry_sha256")
    if registry_hash != compute_typeface_registry_sha256(registry):
        raise ValueError("typeface registry hash does not match configuration")


def _empirical_confidence(
    value: float, distribution: Sequence[float]
) -> float:
    values: np.ndarray = np.asarray(distribution, dtype=float)
    if values.size == 0:
        return 0.0
    below = int((values < value).sum())
    equal = int((values == value).sum())
    return float((below + (equal + 1) / 2.0) / (values.size + 1))


def _score_atlas(
    normalized: Sequence[tuple[str, np.ndarray]],
    atlas: Mapping[str, np.ndarray],
) -> TypefaceSourceScore | None:
    by_character: dict[str, list[float]] = defaultdict(list)
    for char, mask in normalized:
        if char in atlas:
            by_character[char].append(shifted_iou(mask, atlas[char]))
    if not by_character:
        return None
    per_character = {
        char: float(median(scores))
        for char, scores in sorted(by_character.items())
    }
    return TypefaceSourceScore(
        score=float(median(per_character.values())),
        per_character=per_character,
        matched_letter_count=sum(map(len, by_character.values())),
    )


def _two_sided_sign_test(
    left: Sequence[float], right: Sequence[float]
) -> float:
    """Exact two-sided sign-test p-value for paired character scores."""

    signs = [
        left_value > right_value
        for left_value, right_value in zip(left, right, strict=True)
        if left_value != right_value
    ]
    count = len(signs)
    if count == 0:
        return 1.0
    smaller = min(sum(signs), count - sum(signs))
    tail = sum(math.comb(count, value) for value in range(smaller + 1))
    return float(min(1.0, 2.0 * tail / (2**count)))


def _sources_are_ambiguous(
    left: TypefaceSourceScore,
    right: TypefaceSourceScore,
    minimum_distinct: int,
) -> bool:
    shared = sorted(left.per_character.keys() & right.per_character.keys())
    if len(shared) < minimum_distinct:
        return True
    left_values = [left.per_character[char] for char in shared]
    right_values = [right.per_character[char] for char in shared]
    left_wins = sum(
        left_value > right_value
        for left_value, right_value in zip(
            left_values, right_values, strict=True
        )
    )
    right_wins = sum(
        right_value > left_value
        for left_value, right_value in zip(
            left_values, right_values, strict=True
        )
    )
    if left_wins <= right_wins:
        return True
    probability = _two_sided_sign_test(
        left_values,
        right_values,
    )
    return probability >= 0.05


def _abstention(
    *,
    calibration_id: str,
    letter_count: int,
    atlas_scores: dict[str, float],
    reason: str,
    matched_letter_count: int = 0,
    distinct_character_count: int = 0,
) -> TypefaceFingerprint:
    return TypefaceFingerprint(
        merchant_candidates=(),
        typeface_candidates=(),
        typeface=None,
        confidence=0.0,
        confidence_basis=CONFIDENCE_BASIS,
        calibration_id=calibration_id,
        letter_count=letter_count,
        matched_letter_count=matched_letter_count,
        distinct_character_count=distinct_character_count,
        atlas_scores=atlas_scores,
        abstention_reason=reason,
    )


def score_typeface_sources(
    letter_crops: Sequence[tuple[str, np.ndarray]],
    registry: Mapping[str, Any],
) -> tuple[int, dict[str, TypefaceSourceScore]]:
    """Return normalized crop count and exact per-source runtime evidence."""

    normalized = [
        (char, normalize_glyph(mask))
        for char, mask in letter_crops
        if len(char) == 1 and char.strip() and np.asarray(mask).any()
    ]
    atlases = load_registry_atlases(registry)
    return len(normalized), {
        name: score
        for name, atlas in atlases.items()
        if (score := _score_atlas(normalized, atlas)) is not None
    }


def match_typeface(
    letter_crops: Sequence[tuple[str, np.ndarray]],
    registry: Mapping[str, Any],
) -> TypefaceFingerprint:
    """Match one receipt using measured source floors and distinct glyphs.

    Each distinct character contributes one median-IoU vote. A source is
    eligible only when its independently calibrated genuine and impostor
    distributions separate and the receipt clears that source's recorded
    floor. Source ties use an exact paired sign test rather than a fitted
    global ambiguity band.
    """

    validate_typeface_registry(registry)
    calibration = registry["calibration"]
    calibration_id = str(calibration["calibration_id"])
    minimum_distinct = int(calibration["minimum_distinct_characters"])
    letter_count, evidence = score_typeface_sources(letter_crops, registry)
    atlas_scores = {
        name: item.score for name, item in sorted(evidence.items())
    }
    if not letter_count:
        return _abstention(
            calibration_id=calibration_id,
            letter_count=letter_count,
            atlas_scores=atlas_scores,
            reason="NO_USABLE_CROPS",
        )

    eligible: dict[str, TypefaceSourceScore] = {}
    for name, item in evidence.items():
        entry = registry["atlases"][name]
        if not entry.get("enabled", False):
            continue
        if len(item.per_character) < minimum_distinct:
            continue
        if item.score >= float(entry["acceptance_threshold"]):
            eligible[name] = item
    best_support = max(
        evidence.values(),
        key=lambda item: (
            len(item.per_character),
            item.matched_letter_count,
            item.score,
        ),
        default=None,
    )
    if not eligible:
        reason = (
            "INSUFFICIENT_DISTINCT_CHARACTERS"
            if best_support is not None
            and len(best_support.per_character) < minimum_distinct
            else "NO_SEPARATED_SOURCE_MATCH"
        )
        return _abstention(
            calibration_id=calibration_id,
            letter_count=letter_count,
            atlas_scores=atlas_scores,
            reason=reason,
            matched_letter_count=(
                best_support.matched_letter_count if best_support else 0
            ),
            distinct_character_count=(
                len(best_support.per_character) if best_support else 0
            ),
        )

    ranked = sorted(
        eligible,
        key=lambda name: (-eligible[name].score, name),
    )
    winner = ranked[0]
    plausible = [winner]
    for name in ranked[1:]:
        if _sources_are_ambiguous(
            eligible[winner], eligible[name], minimum_distinct
        ):
            plausible.append(name)
    typeface_candidates = tuple(
        sorted(
            {str(registry["atlases"][name]["typeface"]) for name in plausible}
        )
    )
    merchant_candidates = tuple(
        sorted(
            {
                merchant
                for name in plausible
                for merchant in registry["atlases"][name].get(
                    "merchant_candidates", []
                )
            }
        )
    )
    source_resolved = len(plausible) == 1
    winner_entry = registry["atlases"][winner]
    confidence = (
        _empirical_confidence(
            eligible[winner].score,
            winner_entry["genuine_score_distribution"],
        )
        if source_resolved
        else 0.0
    )
    return TypefaceFingerprint(
        merchant_candidates=merchant_candidates,
        typeface_candidates=typeface_candidates,
        typeface=(typeface_candidates[0] if source_resolved else None),
        confidence=confidence,
        confidence_basis=CONFIDENCE_BASIS,
        calibration_id=calibration_id,
        letter_count=letter_count,
        matched_letter_count=eligible[winner].matched_letter_count,
        distinct_character_count=len(eligible[winner].per_character),
        atlas_scores=atlas_scores,
        abstention_reason=(None if source_resolved else "AMBIGUOUS_SOURCES"),
    )


__all__ = [
    "CONFIDENCE_BASIS",
    "TypefaceFingerprint",
    "TypefaceSourceScore",
    "compute_atlas_registry_sha256",
    "compute_typeface_registry_sha256",
    "decode_glyph_rows",
    "load_registry_atlases",
    "match_typeface",
    "score_typeface_sources",
    "validate_typeface_registry",
]
