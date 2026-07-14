"""Entity contract tests for receipt typeface fingerprints."""

from datetime import datetime, timezone

import pytest
from receipt_dynamo.entities import (
    ReceiptTypefaceFingerprint,
    item_to_receipt_typeface_fingerprint,
)


def _fingerprint() -> ReceiptTypefaceFingerprint:
    return ReceiptTypefaceFingerprint(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        merchant_candidates=["Vons", "Vons"],
        typeface="bitMatrix-C1-heavy",
        confidence=0.75,
        letter_count=612,
        atlas_scores={"ROM:bitMatrix-C1-heavy": 0.62},
        model_source="upload-determinism-v1",
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        places_merchant_name="Vons",
        places_agreement="MATCH",
    )


def test_receipt_typeface_fingerprint_round_trip() -> None:
    fingerprint = _fingerprint()
    restored = item_to_receipt_typeface_fingerprint(fingerprint.to_item())

    assert restored == fingerprint
    assert restored.merchant_candidates == ["Vons"]
    assert restored.key["SK"]["S"].endswith("#TYPEFACE_FINGERPRINT")


def test_receipt_typeface_fingerprint_rejects_invalid_agreement() -> None:
    fingerprint = _fingerprint()
    fingerprint.places_agreement = "OVERRIDE"

    with pytest.raises(ValueError, match="places_agreement"):
        fingerprint.to_item()
