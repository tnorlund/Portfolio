"""Dependency-light Places crosscheck for persisted typeface proposals."""

from __future__ import annotations

import re

from receipt_dynamo.entities import ReceiptTypefaceFingerprint


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


__all__ = ["crosscheck_places"]
