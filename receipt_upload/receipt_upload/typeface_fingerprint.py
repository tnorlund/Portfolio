"""Upload-time extraction and persistence for merchant glyph fingerprints."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import resources
from typing import Protocol, Sequence

import numpy as np
from PIL import Image
from receipt_chroma import clean_letter_mask, match_typeface
from receipt_dynamo.entities import (
    ReceiptLetter,
    ReceiptTypefaceFingerprint,
)

MODEL_SOURCE = "upload-determinism-v1"


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


def load_typeface_registry() -> dict:
    """Load the generated JSON registry from the installed upload package."""

    asset = resources.files("receipt_upload").joinpath(
        "assets/typeface_registry_v1.json"
    )
    return json.loads(asset.read_text(encoding="utf-8"))


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
    crops = []
    if image is not None:
        for letter in letters:
            crop = _letter_crop(image, letter)
            if crop is not None:
                crops.append((letter.text, crop))
    result = match_typeface(crops, registry or load_typeface_registry())
    return ReceiptTypefaceFingerprint(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_candidates=list(result.merchant_candidates),
        typeface=result.typeface,
        confidence=result.confidence,
        letter_count=result.letter_count,
        atlas_scores=result.atlas_scores,
        model_source=MODEL_SOURCE,
        created_at=datetime.now(timezone.utc),
    )


def persist_receipt_fingerprint(
    dynamo: FingerprintDynamoClient,
    image: Image.Image | None,
    letters: Sequence[ReceiptLetter],
) -> ReceiptTypefaceFingerprint | None:
    """Persist one fingerprint when receipt letters exist."""

    if not letters:
        return None
    fingerprint = fingerprint_receipt(image, letters)
    dynamo.put_receipt_typeface_fingerprint(fingerprint)
    return fingerprint


def _merchant_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    ignored = {"company", "co", "corporation", "corp", "inc", "llc"}
    return " ".join(
        token for token in normalized.split() if token not in ignored
    )


def crosscheck_places(
    fingerprint: ReceiptTypefaceFingerprint,
    places_merchant_name: str | None,
) -> ReceiptTypefaceFingerprint:
    """Annotate, but never replace, glyph and Places proposals."""

    fingerprint.places_merchant_name = places_merchant_name
    if not places_merchant_name or not fingerprint.merchant_candidates:
        fingerprint.places_agreement = "UNAVAILABLE"
        return fingerprint
    place = _merchant_key(places_merchant_name)
    candidates = [
        _merchant_key(name) for name in fingerprint.merchant_candidates
    ]
    fingerprint.places_agreement = (
        "MATCH"
        if any(
            place == candidate
            or place.startswith(candidate + " ")
            or candidate.startswith(place + " ")
            for candidate in candidates
        )
        else "DISAGREEMENT"
    )
    return fingerprint


__all__ = [
    "MODEL_SOURCE",
    "crosscheck_places",
    "fingerprint_receipt",
    "load_typeface_registry",
    "persist_receipt_fingerprint",
]
