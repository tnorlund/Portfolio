"""Integration coverage for the D4 resolved-details access pattern."""

from datetime import datetime, timezone

import pytest

from receipt_dynamo import DynamoClient, Receipt, ReceiptResolvedDetails

IMAGE_ID = "00000000-0000-4000-8000-0000000000d4"


def _receipt() -> Receipt:
    return Receipt(
        receipt_id=1,
        image_id=IMAGE_ID,
        width=10,
        height=20,
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        sha256="sha256",
    )


def _resolved() -> ReceiptResolvedDetails:
    return ReceiptResolvedDetails(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant={
            "name": {
                "value": "Costco",
                "provenance": "fingerprint",
                "confidence": 0.96,
            }
        },
        transaction={},
        items=[],
        totals={},
        tender={},
        conflicts=[],
        validation_status="VALID",
        model_source="upload-determinism-v1",
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )


@pytest.mark.integration
def test_put_get_and_receipt_details_gsi4(dynamodb_table) -> None:
    client = DynamoClient(dynamodb_table)
    receipt = _receipt()
    resolved = _resolved()

    client.add_receipt(receipt)
    client.put_receipt_resolved_details(resolved)

    assert client.get_receipt_resolved_details(IMAGE_ID, 1) == resolved
    details = client.get_receipt_details(IMAGE_ID, 1)
    assert details.receipt == receipt
    assert details.resolved_details == resolved
