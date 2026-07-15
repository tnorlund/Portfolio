"""Upload adapter tests for calibrated typeface fingerprinting."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image
from receipt_chroma import (
    compute_atlas_registry_sha256,
    compute_typeface_registry_sha256,
    load_registry_atlases,
    match_typeface,
    normalize_glyph,
    score_typeface_sources,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

from receipt_upload.typeface_fingerprint import (
    crosscheck_places,
    fingerprint_receipt,
    load_typeface_registry,
    persist_empty_receipt_fingerprint,
    persist_receipt_fingerprint,
)


def _encode(mask: np.ndarray) -> list[str]:
    return [
        f"{sum(int(value) << (31 - x) for x, value in enumerate(row)):08x}"
        for row in mask
    ]


def _decode_fixture_crop(encoded: dict) -> np.ndarray:
    height, width = encoded["shape"]
    bits = np.unpackbits(
        np.frombuffer(bytes.fromhex(encoded["packed_bits"]), dtype=np.uint8),
        bitorder="big",
    )
    return bits[: height * width].reshape((height, width)).astype(bool)


def _crop_digest(crops: list[tuple[str, np.ndarray]]) -> str:
    members = []
    for char, mask in crops:
        digest = hashlib.sha256()
        digest.update(char.encode())
        digest.update(np.packbits(normalize_glyph(mask)).tobytes())
        members.append(digest.digest())
    return hashlib.sha256(b"".join(sorted(members))).hexdigest()


def _registry() -> dict:
    vertical = np.zeros((32, 32), dtype=bool)
    vertical[:, 13:19] = True
    registry = {
        "schema_version": 2,
        "calibration": {
            "calibration_id": "synthetic-v2",
            "minimum_distinct_characters": 1,
            "minimum_distinct_characters_derivation": "synthetic test",
        },
        "atlases": {
            "ROM:test": {
                "kind": "rom",
                "enabled": True,
                "disabled_reason": None,
                "acceptance_threshold": 0.1,
                "acceptance_threshold_derivation": (
                    "midpoint-between-calibration-max-impostor-and-min-genuine"
                ),
                "genuine_score_distribution": [0.2, 0.5],
                "impostor_score_distribution": [0.0],
                "holdout_genuine_score_distribution": [0.4],
                "holdout_impostor_score_distribution": [0.0],
                "calibration_receipt_hashes": [
                    "test-cal-genuine-1",
                    "test-cal-genuine-2",
                    "test-cal-impostor",
                ],
                "holdout_receipt_hashes": [
                    "test-hold-genuine",
                    "test-hold-impostor",
                ],
                "typeface": "test",
                "merchant_candidates": ["Smith's"],
                "glyphs": {"I": _encode(vertical)},
            }
        },
    }
    registry["atlas_registry_sha256"] = compute_atlas_registry_sha256(registry)
    registry["registry_sha256"] = compute_typeface_registry_sha256(registry)
    return registry


def _letter():
    return SimpleNamespace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        text="I",
        top_left={"x": 0.35, "y": 0.85},
        top_right={"x": 0.55, "y": 0.85},
        bottom_left={"x": 0.35, "y": 0.20},
        bottom_right={"x": 0.55, "y": 0.20},
    )


def test_fingerprint_receipt_extracts_vision_coordinate_crop() -> None:
    pixels = np.full((100, 100), 255, dtype=np.uint8)
    pixels[20:80, 43:48] = 0
    result = fingerprint_receipt(
        Image.fromarray(pixels), [_letter()], registry=_registry()
    )

    assert result.typeface == "test"
    assert result.merchant_candidates == ["Smith's"]
    assert result.letter_count == 1
    assert result.matched_letter_count == 1
    assert result.distinct_character_count == 1
    assert result.confidence_basis
    assert result.model_source == "upload-determinism-v2"


def test_persist_receipt_fingerprint_writes_abstention_without_image() -> None:
    dynamo = Mock()
    dynamo.get_receipt_typeface_fingerprint.side_effect = EntityNotFoundError
    fingerprint = persist_receipt_fingerprint(dynamo, None, [_letter()])

    assert fingerprint is not None
    assert fingerprint.typeface is None
    assert fingerprint.letter_count == 0
    dynamo.put_receipt_typeface_fingerprint.assert_called_once_with(
        fingerprint
    )


def test_empty_fingerprint_replaces_stale_evidence_and_preserves_places() -> (
    None
):
    pixels = np.full((100, 100), 255, dtype=np.uint8)
    pixels[20:80, 43:48] = 0
    previous = fingerprint_receipt(
        Image.fromarray(pixels), [_letter()], registry=_registry()
    )
    crosscheck_places(previous, "Costco Wholesale")
    dynamo = Mock()
    stored = previous

    def atomic_evidence_update(fingerprint):
        nonlocal stored
        stored = replace(
            fingerprint,
            places_merchant_name=stored.places_merchant_name,
            places_agreement="UNAVAILABLE",
        )

    def conditional_places_update(
        fingerprint, *, expected_places_merchant_name=None
    ):
        nonlocal stored
        assert stored.places_merchant_name == expected_places_merchant_name
        stored = fingerprint

    dynamo.put_receipt_typeface_fingerprint.side_effect = (
        atomic_evidence_update
    )
    dynamo.get_receipt_typeface_fingerprint.side_effect = lambda *_args: stored
    dynamo.update_receipt_typeface_places.side_effect = (
        conditional_places_update
    )

    fingerprint = persist_empty_receipt_fingerprint(
        dynamo,
        previous.image_id,
        previous.receipt_id,
        registry=_registry(),
    )

    assert fingerprint.letter_count == 0
    assert fingerprint.abstention_reason == "NO_USABLE_CROPS"
    assert fingerprint.places_merchant_name == "Costco Wholesale"
    assert fingerprint.places_agreement == "UNAVAILABLE"
    dynamo.put_receipt_typeface_fingerprint.assert_called_once()
    dynamo.update_receipt_typeface_places.assert_called_once_with(
        fingerprint,
        expected_places_merchant_name="Costco Wholesale",
    )


def test_places_disagreement_is_annotated_without_overriding() -> None:
    pixels = np.full((100, 100), 255, dtype=np.uint8)
    pixels[20:80, 43:48] = 0
    fingerprint = fingerprint_receipt(
        Image.fromarray(pixels), [_letter()], registry=_registry()
    )
    original = (fingerprint.typeface, list(fingerprint.merchant_candidates))

    crosscheck_places(fingerprint, "Costco Wholesale")

    assert fingerprint.places_agreement == "DISAGREEMENT"
    assert fingerprint.places_merchant_name == "Costco Wholesale"
    assert (fingerprint.typeface, fingerprint.merchant_candidates) == original


def test_generated_registry_records_calibration_and_anti_copy_gate() -> None:
    registry_path = (
        Path(__file__).parents[1]
        / "receipt_upload/assets/typeface_registry_v2.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    measurement_path = (
        Path(__file__).parents[2] / "synthesis_loop/rom_font_manifests/"
        "typeface_runtime_calibration_v2.json"
    )
    measurement = json.loads(measurement_path.read_text(encoding="utf-8"))
    measurement_hash = hashlib.sha256(
        json.dumps(measurement, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert registry["schema_version"] == 2
    assert registry["gates"]["anti_copy"]["status"] == "PASS"
    assert registry["calibration"]["measurement_sha256"] == measurement_hash
    assert registry["calibration"]["minimum_distinct_characters"] == 6
    assert all(
        not entry["enabled"]
        and entry["acceptance_threshold"] is None
        and entry["disabled_reason"]
        for entry in registry["atlases"].values()
    )
    assert len(registry["atlases"]) == 15
    assert not (registry_path.parent / "typeface_registry_v1.json").exists()
    assert not list(registry_path.parent.glob("*.npz"))


def test_measurement_rejects_missing_source_scores(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    from scripts.evaluate_typeface_registry import (  # pylint: disable=import-outside-toplevel
        ReceiptCase,
        _source_evidence,
    )

    cases = [
        ReceiptCase(
            key="genuine",
            merchant="Test",
            true_sources=("ROM:test",),
            split="calibration",
            scores={},
        ),
        ReceiptCase(
            key="impostor",
            merchant="Foreign",
            true_sources=(),
            split="calibration",
            scores={"ROM:test": 0.1},
        ),
    ]

    with pytest.raises(RuntimeError, match="lacks calibration genuine scores"):
        _source_evidence("ROM:test", cases)


def test_real_exact_runtime_fixtures_abstain_without_separation() -> None:
    fixture_path = (
        Path(__file__).parent / "fixtures/typeface_real_crops_v2.json"
    )
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    registry = load_typeface_registry()
    measurement_path = (
        Path(__file__).parents[2]
        / "synthesis_loop/rom_font_manifests/typeface_runtime_calibration_v2.json"
    )
    measurement = json.loads(measurement_path.read_text(encoding="utf-8"))

    assert (
        fixtures["measurement_sha256"]
        == hashlib.sha256(
            json.dumps(
                measurement, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert (
        fixtures["atlas_registry_sha256"] == registry["atlas_registry_sha256"]
    )

    for fixture in fixtures["cases"]:
        crops = [
            (char, _decode_fixture_crop(encoded))
            for char, crop_population in fixture["glyphs"].items()
            for encoded in crop_population
        ]
        result = match_typeface(crops, registry)
        _count, evidence = score_typeface_sources(crops, registry)
        actual_scores = {
            name: source.score for name, source in evidence.items()
        }
        binding = measurement["runtime_fixture_cases"][fixture["case_id"]]

        assert _crop_digest(crops) == fixture["crop_digest"]
        assert binding["crop_digest"] == fixture["crop_digest"]
        assert (
            binding["source_scores_sha256"]
            == fixture["expected_source_scores_sha256"]
        )
        assert (
            fixture["expected_source_scores_sha256"]
            == hashlib.sha256(
                json.dumps(
                    fixture["expected_source_scores"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        assert result.typeface == fixture["expected_typeface"]
        assert result.merchant_candidates == ()
        assert result.typeface_candidates == ()
        assert result.confidence == 0
        assert (
            result.abstention_reason == fixture["expected_abstention_reason"]
        )
        assert actual_scores.keys() == fixture["expected_source_scores"].keys()
        assert all(
            abs(actual_scores[name] - expected) <= 1e-12
            for name, expected in fixture["expected_source_scores"].items()
        )
        assert (
            max(actual_scores, key=actual_scores.__getitem__)
            == fixture["expected_top_source"]
        )


def test_runtime_registry_decodes_atlases_once_as_read_only() -> None:
    load_typeface_registry.cache_clear()
    first = load_typeface_registry()
    second = load_typeface_registry()
    first_atlases = load_registry_atlases(first)
    second_atlases = load_registry_atlases(second)
    first_mask = first_atlases["MERCHANT:costco"]["A"]

    assert first is second
    assert first_atlases is second_atlases
    assert first_mask.flags.writeable is False


def test_ocr_image_packages_canonical_glyphstudio_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (
        root / "infra/upload_images/container_ocr/Dockerfile"
    ).read_text(encoding="utf-8")
    infrastructure = (root / "infra/upload_images/infra.py").read_text(
        encoding="utf-8"
    )

    assert "COPY tools/glyph-studio/py/ /tmp/glyphstudio_pkg/" in dockerfile
    assert "pip install --no-cache-dir /tmp/glyphstudio_pkg" in dockerfile
    assert '"/tmp/receipt_chroma[typeface]"' in dockerfile
    assert '"receipt_upload/receipt_upload/assets"' in infrastructure
    assert '"tools/glyph-studio/py"' in infrastructure
