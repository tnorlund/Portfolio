"""Upload adapter tests for calibrated typeface fingerprinting."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image

from receipt_upload.typeface_fingerprint import (
    crosscheck_places,
    fingerprint_receipt,
    persist_receipt_fingerprint,
)


def _encode(mask: np.ndarray) -> list[str]:
    return [
        f"{sum(int(value) << (31 - x) for x, value in enumerate(row)):08x}"
        for row in mask
    ]


def _registry() -> dict:
    vertical = np.zeros((32, 32), dtype=bool)
    vertical[:, 13:19] = True
    return {
        "calibration": {
            "minimum_letter_crops": 1,
            "minimum_winner_iou": 0.1,
            "typeface_ambiguity_band": 0.01,
            "winner_iou_distribution": [0.1, 0.5],
            "winner_margin_distribution": [0.1, 0.5],
        },
        "atlases": {
            "ROM:test": {
                "typeface": "test",
                "merchant_candidates": ["Smith's"],
                "glyphs": {"I": _encode(vertical)},
            }
        },
    }


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
    assert result.model_source == "upload-determinism-v1"


def test_persist_receipt_fingerprint_writes_abstention_without_image() -> None:
    dynamo = Mock()
    fingerprint = persist_receipt_fingerprint(dynamo, None, [_letter()])

    assert fingerprint is not None
    assert fingerprint.typeface is None
    assert fingerprint.letter_count == 0
    dynamo.put_receipt_typeface_fingerprint.assert_called_once_with(
        fingerprint
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
        / "receipt_upload/assets/typeface_registry_v1.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert registry["gates"]["anti_copy"]["status"] == "PASS"
    assert registry["calibration"]["source"].endswith(
        "validation_results.json"
    )
    assert (
        "derivation"
        in registry["calibration"]["minimum_letter_crops_derivation"]
        or registry["calibration"]["minimum_letter_crops_derivation"]
    )
    assert len(registry["atlases"]) == 15
    assert not list(registry_path.parent.glob("*.npz"))
