"""Entity contract tests for receipt typeface fingerprints."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from receipt_dynamo.data._receipt_typeface_fingerprint import (
    _ReceiptTypefaceFingerprint,
)
from receipt_dynamo.entities import (
    ReceiptTypefaceFingerprint,
    item_to_receipt_typeface_fingerprint,
)


def _fingerprint() -> ReceiptTypefaceFingerprint:
    return ReceiptTypefaceFingerprint(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        merchant_candidates=["Vons", "Vons"],
        typeface_candidates=["bitMatrix-C1-heavy"],
        typeface="bitMatrix-C1-heavy",
        confidence=0.75,
        confidence_basis="source-relative-genuine-score-empirical-midrank",
        calibration_id="typeface-registry-v2-test",
        letter_count=612,
        matched_letter_count=600,
        distinct_character_count=52,
        atlas_scores={"ROM:bitMatrix-C1-heavy": 0.62},
        abstention_reason=None,
        model_source="upload-determinism-v2",
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        places_merchant_name="Vons",
        places_agreement="MATCH",
    )


def test_receipt_typeface_fingerprint_round_trip() -> None:
    fingerprint = _fingerprint()
    restored = item_to_receipt_typeface_fingerprint(fingerprint.to_item())

    assert restored == fingerprint
    assert restored.merchant_candidates == ["Vons"]
    assert restored.typeface_candidates == ["bitMatrix-C1-heavy"]
    assert restored.key["SK"]["S"].endswith("#TYPEFACE_FINGERPRINT")


def test_fingerprint_evidence_upsert_retains_name_and_resets_agreement() -> (
    None
):
    accessor = _ReceiptTypefaceFingerprint()
    accessor.table_name = "receipts"
    accessor._client = MagicMock()
    fingerprint = _fingerprint()

    accessor.put_receipt_typeface_fingerprint(fingerprint)

    transact_items = accessor._client.transact_write_items.call_args.kwargs[
        "TransactItems"
    ]
    condition = transact_items[0]["ConditionCheck"]
    update = transact_items[1]["Update"]
    assert condition == {
        "TableName": "receipts",
        "Key": {
            "PK": {"S": f"IMAGE#{fingerprint.image_id}"},
            "SK": {"S": f"RECEIPT#{fingerprint.receipt_id:05d}"},
        },
        "ConditionExpression": "attribute_exists(PK)",
    }
    assert update["Key"] == fingerprint.key
    assert (
        "#places_agreement = :places_unavailable" in update["UpdateExpression"]
    )
    assert (
        "places_merchant_name"
        not in update["ExpressionAttributeNames"].values()
    )
    assert [
        value
        for name, value in update["ExpressionAttributeNames"].items()
        if value == "places_agreement"
    ] == ["places_agreement"]
    accessor._client.put_item.assert_not_called()
    accessor._client.update_item.assert_not_called()


def test_delete_fingerprint_is_exact_and_idempotent() -> None:
    accessor = _ReceiptTypefaceFingerprint()
    accessor.table_name = "receipts"
    accessor._client = MagicMock()
    fingerprint = _fingerprint()

    accessor.delete_receipt_typeface_fingerprint(
        fingerprint.image_id, fingerprint.receipt_id
    )

    accessor._client.delete_item.assert_called_once_with(
        TableName="receipts",
        Key=fingerprint.key,
    )


def test_receipt_typeface_fingerprint_rejects_invalid_agreement() -> None:
    fingerprint = _fingerprint()
    fingerprint.places_agreement = "OVERRIDE"

    with pytest.raises(ValueError, match="places_agreement"):
        fingerprint.to_item()


def test_receipt_typeface_fingerprint_serialization_is_pure() -> None:
    fingerprint = _fingerprint()
    fingerprint.merchant_candidates.append("Albertsons")
    before = list(fingerprint.merchant_candidates)

    fingerprint.to_item()

    assert fingerprint.merchant_candidates == before


def test_receipt_typeface_fingerprint_rejects_wrong_discriminator() -> None:
    item = _fingerprint().to_item()
    item["TYPE"] = {"S": "RECEIPT"}

    with pytest.raises(ValueError, match="TYPE"):
        item_to_receipt_typeface_fingerprint(item)


def test_receipt_typeface_fingerprint_normalizes_malformed_shapes() -> None:
    item = _fingerprint().to_item()
    item["TYPE"] = "RECEIPT_TYPEFACE_FINGERPRINT"

    with pytest.raises(ValueError, match="Invalid ReceiptTypefaceFingerprint"):
        item_to_receipt_typeface_fingerprint(item)


def test_receipt_typeface_fingerprint_requires_timezone() -> None:
    fingerprint = _fingerprint()
    fingerprint.created_at = datetime(2026, 7, 14)

    with pytest.raises(ValueError, match="timezone"):
        fingerprint.to_item()


def test_places_update_is_narrow_and_optimistically_locked() -> None:
    accessor = _ReceiptTypefaceFingerprint()
    accessor.table_name = "receipts"
    accessor._client = MagicMock()
    fingerprint = _fingerprint()

    accessor.update_receipt_typeface_places(
        fingerprint, expected_places_merchant_name="Vons"
    )

    accessor._client.update_item.assert_called_once_with(
        TableName="receipts",
        Key=fingerprint.key,
        UpdateExpression=(
            "SET places_agreement = :agreement, "
            "places_merchant_name = :merchant_name"
        ),
        ConditionExpression=(
            "attribute_exists(PK) AND calibration_id = :calibration_id "
            "AND created_at = :created_at "
            "AND places_merchant_name = :expected_places_merchant_name"
        ),
        ExpressionAttributeValues={
            ":agreement": {"S": "MATCH"},
            ":calibration_id": {"S": fingerprint.calibration_id},
            ":created_at": {"S": fingerprint.created_at.isoformat()},
            ":merchant_name": {"S": "Vons"},
            ":expected_places_merchant_name": {"S": "Vons"},
        },
    )


def test_places_update_locks_absent_previous_name() -> None:
    accessor = _ReceiptTypefaceFingerprint()
    accessor.table_name = "receipts"
    accessor._client = MagicMock()
    fingerprint = _fingerprint()

    accessor.update_receipt_typeface_places(fingerprint)

    assert accessor._client.update_item.call_args.kwargs[
        "ConditionExpression"
    ].endswith("AND attribute_not_exists(places_merchant_name)")


def test_fingerprint_get_is_strongly_consistent() -> None:
    accessor = _ReceiptTypefaceFingerprint()
    accessor.table_name = "receipts"
    accessor._client = MagicMock()
    fingerprint = _fingerprint()
    accessor._client.get_item.return_value = {"Item": fingerprint.to_item()}

    assert (
        accessor.get_receipt_typeface_fingerprint(
            fingerprint.image_id, fingerprint.receipt_id
        )
        == fingerprint
    )

    assert accessor._client.get_item.call_args.kwargs["ConsistentRead"] is True
