"""Upload-time extraction and persistence for merchant glyph fingerprints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Protocol, Sequence

import numpy as np
from PIL import Image
from receipt_chroma import (
    TypefaceFingerprint,
    clean_letter_mask,
    match_typeface,
    validate_typeface_registry,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities import (
    ReceiptLetter,
    ReceiptTypefaceFingerprint,
)

from receipt_upload.typeface_crosscheck import crosscheck_places

MODEL_SOURCE = "upload-determinism-v2"


class FingerprintDynamoClient(Protocol):
    """Narrow persistence surface used by upload and merchant workers."""

    def put_receipt_typeface_fingerprint(
        self, fingerprint: ReceiptTypefaceFingerprint
    ) -> None:
        """Persist one receipt fingerprint."""
        raise NotImplementedError

    def get_receipt_typeface_fingerprint(
        self, image_id: str, receipt_id: int
    ) -> ReceiptTypefaceFingerprint:
        """Load one receipt fingerprint."""
        raise NotImplementedError

    def update_receipt_typeface_places(
        self,
        fingerprint: ReceiptTypefaceFingerprint,
        *,
        expected_places_merchant_name: str | None = None,
    ) -> None:
        """Conditionally update only the Places crosscheck fields."""
        raise NotImplementedError


@lru_cache(maxsize=1)
def load_typeface_registry() -> dict:
    """Load the generated JSON registry from the installed upload package."""

    asset = resources.files("receipt_upload").joinpath(
        "assets/typeface_registry_v2.json"
    )
    registry = json.loads(asset.read_text(encoding="utf-8"))
    validate_typeface_registry(registry)
    return registry


def _otsu_ink(image: Image.Image) -> np.ndarray:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    histogram: np.ndarray = np.bincount(gray.ravel(), minlength=256).astype(
        float
    )
    count = gray.size
    if not count or np.count_nonzero(histogram) <= 1:
        return np.zeros(gray.shape, dtype=bool)
    weight_low: np.ndarray = np.cumsum(histogram)
    weight_high = count - weight_low
    mean_low: np.ndarray = np.cumsum(histogram * np.arange(256))
    total = mean_low[-1]
    denominator = weight_low * weight_high
    numerator = (total * weight_low - mean_low * count) ** 2
    variance = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 0,
    )
    threshold = int(np.argmax(variance))
    dark = gray <= threshold
    return dark if dark.mean() <= 0.5 else ~dark


def _letter_crop(
    image: Image.Image, letter: ReceiptLetter
) -> np.ndarray | None:
    points = (
        letter.top_left,
        letter.top_right,
        letter.bottom_left,
        letter.bottom_right,
    )
    xs = [float(point["x"]) for point in points]
    ys = [float(point["y"]) for point in points]
    normalized = max(map(abs, xs + ys), default=0.0) <= 1.5
    if normalized:
        left = int(np.floor(min(xs) * image.width))
        right = int(np.ceil(max(xs) * image.width))
        top = int(np.floor((1.0 - max(ys)) * image.height))
        bottom = int(np.ceil((1.0 - min(ys)) * image.height))
    else:
        left, right = int(np.floor(min(xs))), int(np.ceil(max(xs)))
        top, bottom = int(np.floor(min(ys))), int(np.ceil(max(ys)))
    left, right = max(0, left), min(image.width, right)
    top, bottom = max(0, top), min(image.height, bottom)
    if right <= left or bottom <= top:
        return None
    cleaned = clean_letter_mask(
        _otsu_ink(image.crop((left, top, right, bottom)))
    )
    return cleaned if cleaned.any() else None


def extract_letter_crops(
    image: Image.Image | None,
    letters: Sequence[ReceiptLetter],
) -> list[tuple[str, np.ndarray]]:
    """Run the exact production crop extractor without persistence."""

    if image is None:
        return []
    crops = []
    for letter in letters:
        crop = _letter_crop(image, letter)
        if crop is not None:
            crops.append((letter.text, crop))
    return crops


def fingerprint_receipt(
    image: Image.Image | None,
    letters: Sequence[ReceiptLetter],
    *,
    registry: dict | None = None,
) -> ReceiptTypefaceFingerprint:
    """Extract letter masks and return a calibrated receipt fingerprint."""

    if not letters:
        raise ValueError("letters must not be empty")
    image_id = letters[0].image_id
    receipt_id = letters[0].receipt_id
    if any(
        letter.image_id != image_id or letter.receipt_id != receipt_id
        for letter in letters
    ):
        raise ValueError("letters must belong to one receipt")
    crops = extract_letter_crops(image, letters)
    result = match_typeface(crops, registry or load_typeface_registry())
    return _fingerprint_entity(image_id, receipt_id, result)


def _fingerprint_entity(
    image_id: str,
    receipt_id: int,
    result: TypefaceFingerprint,
) -> ReceiptTypefaceFingerprint:
    """Convert a pure matcher result into the persisted receipt artifact."""

    return ReceiptTypefaceFingerprint(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_candidates=list(result.merchant_candidates),
        typeface_candidates=list(result.typeface_candidates),
        typeface=result.typeface,
        confidence=result.confidence,
        confidence_basis=result.confidence_basis,
        calibration_id=result.calibration_id,
        letter_count=result.letter_count,
        matched_letter_count=result.matched_letter_count,
        distinct_character_count=result.distinct_character_count,
        atlas_scores=result.atlas_scores,
        abstention_reason=result.abstention_reason,
        model_source=MODEL_SOURCE,
        created_at=datetime.now(timezone.utc),
    )


def _persist_fingerprint(
    dynamo: FingerprintDynamoClient,
    fingerprint: ReceiptTypefaceFingerprint,
) -> ReceiptTypefaceFingerprint:
    """Atomically replace evidence and reconcile retained Places provenance."""

    dynamo.put_receipt_typeface_fingerprint(fingerprint)
    for attempt in range(2):
        try:
            persisted = dynamo.get_receipt_typeface_fingerprint(
                fingerprint.image_id, fingerprint.receipt_id
            )
        except EntityNotFoundError:
            return fingerprint
        places_name = persisted.places_merchant_name
        if places_name is None:
            return persisted
        crosscheck_places(persisted, places_name)
        try:
            dynamo.update_receipt_typeface_places(
                persisted,
                expected_places_merchant_name=places_name,
            )
            return persisted
        except EntityNotFoundError:
            if attempt:
                raise
    raise AssertionError("unreachable")


def persist_receipt_fingerprint(
    dynamo: FingerprintDynamoClient,
    image: Image.Image | None,
    letters: Sequence[ReceiptLetter],
) -> ReceiptTypefaceFingerprint | None:
    """Persist one fingerprint when receipt letters exist."""

    if not letters:
        return None
    fingerprint = fingerprint_receipt(image, letters)
    return _persist_fingerprint(dynamo, fingerprint)


def persist_empty_receipt_fingerprint(
    dynamo: FingerprintDynamoClient,
    image_id: str,
    receipt_id: int,
    *,
    registry: dict | None = None,
) -> ReceiptTypefaceFingerprint:
    """Replace stale evidence when a receipt no longer has any letters."""

    result = match_typeface([], registry or load_typeface_registry())
    fingerprint = _fingerprint_entity(image_id, receipt_id, result)
    return _persist_fingerprint(dynamo, fingerprint)


__all__ = [
    "MODEL_SOURCE",
    "crosscheck_places",
    "extract_letter_crops",
    "fingerprint_receipt",
    "load_typeface_registry",
    "persist_empty_receipt_fingerprint",
    "persist_receipt_fingerprint",
]
