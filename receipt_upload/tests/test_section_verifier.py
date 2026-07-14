"""Tests for the VALID-only, non-overriding KNN section verifier."""

from datetime import datetime, timezone

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptRow, ReceiptSection

from receipt_upload.section_assignment import MODEL_SOURCE
from receipt_upload.section_verifier import verify_receipt_sections

_UPLOAD = "00000000-0000-4000-8000-000000000001"
_NEIGHBOR = "00000000-0000-4000-8000-000000000002"


class FakeChroma:
    def query(self, **_kwargs):
        return {
            "metadatas": [
                [
                    {
                        "image_id": _NEIGHBOR,
                        "receipt_id": 2,
                        "row_line_ids": "[20]",
                    }
                ]
            ],
            "embeddings": [[[1.0, 0.0]]],
        }


class FakeDynamo:
    def __init__(self, upload, neighbor):
        self.upload = [upload]
        self.neighbor = [neighbor]
        self.updated = []

    def get_receipt_sections_from_receipt(self, image_id, _receipt_id):
        return self.neighbor if image_id == _NEIGHBOR else self.upload

    def update_receipt_section(self, section):
        self.updated.append(section)
        self.upload = [section]


def test_disagreement_is_recorded_without_overriding_sync_assignment() -> None:
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    row = ReceiptRow(
        image_id=_UPLOAD,
        receipt_id=1,
        row_id=1,
        line_ids=[1],
        grouping_version="visual-rows-v1",
        y_min=0.7,
        y_max=0.8,
        x_min=0.1,
        x_max=0.9,
        created_at=now,
    )
    proposed = ReceiptSection(
        image_id=_UPLOAD,
        receipt_id=1,
        section_type="ITEMS",
        line_ids=[1],
        row_ids=[1],
        created_at=now,
        validation_status="PENDING",
        model_source=MODEL_SOURCE,
    )
    valid_neighbor = ReceiptSection(
        image_id=_NEIGHBOR,
        receipt_id=2,
        section_type="TOTAL_LINE",
        line_ids=[20],
        created_at=now,
        validation_status="VALID",
    )
    dynamo = FakeDynamo(proposed, valid_neighbor)

    verified = verify_receipt_sections(
        FakeChroma(), dynamo, [row], [[1.0, 0.0]]
    )

    assert verified[0].section_type == "TOTAL_LINE"
    updated = dynamo.updated[0]
    assert updated.section_type == "ITEMS"
    assert updated.validation_status == ValidationStatus.PENDING.value
    assert updated.verification_status == "DISAGREED"
    assert updated.verification_section_type == "TOTAL_LINE"
    assert updated.disagreement_row_ids == [1]
