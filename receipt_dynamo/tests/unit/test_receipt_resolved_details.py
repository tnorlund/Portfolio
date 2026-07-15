"""ReceiptResolvedDetails persistence and public API contracts."""

from datetime import datetime, timezone

import pytest
import receipt_dynamo
from receipt_dynamo.entities import (
    ReceiptResolvedDetails,
    item_to_receipt_resolved_details,
)


def _field(value, provenance="layoutlm", confidence=0.9):
    return {
        "value": value,
        "provenance": provenance,
        "confidence": confidence,
    }


def _details() -> ReceiptResolvedDetails:
    return ReceiptResolvedDetails(
        image_id="00000000-0000-4000-8000-0000000000d4",
        receipt_id=4,
        merchant={"name": _field("Vons", "fingerprint", 0.97)},
        transaction={"date": _field("2026-07-14")},
        items=[
            {
                "name": _field("Apples"),
                "quantity": _field(2, "section-rule", 0.88),
                "price": _field("1.50", "arithmetic", 1.0),
            }
        ],
        totals={"grand_total": _field("3.00", "arithmetic", 1.0)},
        tender={"type": _field("VISA", "template", 0.95)},
        conflicts=[],
        validation_status="VALID",
        model_source="upload-determinism-v1",
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )


def test_receipt_resolved_details_round_trip() -> None:
    details = _details()
    item = details.to_item()
    restored = item_to_receipt_resolved_details(item)

    assert restored == details
    assert item["TYPE"] == {"S": "RECEIPT_RESOLVED_DETAILS"}
    assert item["SK"] == {"S": "RECEIPT#00004#RESOLVED_DETAILS"}
    assert item["GSI4SK"] == {"S": "7_RESOLVED_DETAILS"}
    assert restored.to_document()["totals"]["grand_total"]["value"] == ("3.00")
    assert restored.field_count == 7


def test_receipt_resolved_details_requires_field_provenance() -> None:
    details = _details()
    details.totals = {"grand_total": {"value": "3.00"}}

    with pytest.raises(ValueError, match="provenance and confidence"):
        details.to_item()


def test_receipt_resolved_details_is_public() -> None:
    assert "ReceiptResolvedDetails" in receipt_dynamo.__all__
    assert "item_to_receipt_resolved_details" in receipt_dynamo.__all__
