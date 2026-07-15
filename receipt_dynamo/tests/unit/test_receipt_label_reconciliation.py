"""ReceiptLabelReconciliation serialization contract."""

from datetime import datetime, timezone

from receipt_dynamo.entities import (
    ReceiptLabelReconciliation,
    item_to_receipt_label_reconciliation,
)
import receipt_dynamo


def test_receipt_label_reconciliation_round_trip():
    artifact = ReceiptLabelReconciliation(
        image_id="00000000-0000-4000-8000-0000000000d3",
        receipt_id=3,
        corrections=[
            {
                "event_id": "abc",
                "provenance": "section-rule",
                "confidence": 0.95,
                "conflict": True,
            }
        ],
        checks=[{"check_id": "audit", "status": "CONFLICT"}],
        validation_status="NEEDS_REVIEW",
        model_source="upload-determinism-v1",
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )

    item = artifact.to_item()
    restored = item_to_receipt_label_reconciliation(item)

    assert restored == artifact
    assert item["conflict_count"] == {"N": "1"}
    assert item["SK"]["S"] == "RECEIPT#00003#LABEL_RECONCILIATION"


def test_receipt_label_reconciliation_is_public():
    assert "ReceiptLabelReconciliation" in receipt_dynamo.__all__
    assert "item_to_receipt_label_reconciliation" in receipt_dynamo.__all__
