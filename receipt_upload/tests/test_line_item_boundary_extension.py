"""Reconciliation-gated ITEMS boundary extension tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

# isort: off
# Editable local packages land in different groups across the two CI venvs.
from infra.receipt_line_item_updater import line_item_processor
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_upload.line_items.geometry import (
    propose_items_boundary_extension,
)

# isort: on

IMAGE_ID = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"


def _word(line_id: int, word_id: int, text: str, x: float, y: float):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": 0.02,
    }


def _row(line_id: int, y: float):
    return SimpleNamespace(row_id=line_id, line_ids=[line_id], y_min=y)


def _items_section(
    line_ids: list[int] | None = None, validation_status: str = "VALID"
) -> ReceiptSection:
    ids = line_ids or [1, 2]
    return ReceiptSection(
        receipt_id=1,
        image_id=IMAGE_ID,
        section_type="ITEMS",
        line_ids=ids,
        row_ids=ids,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model_source="section-seed-v0",
        validation_status=validation_status,
    )


def _product_words(
    third_name: str = "ORANGES", third_price: str = "4.00"
) -> list[dict]:
    return [
        _word(1, 1, "APPLES", 0.05, 0.10),
        _word(1, 2, "3.00", 0.80, 0.10),
        _word(2, 1, "BANANAS", 0.05, 0.15),
        _word(2, 2, "2.00", 0.80, 0.15),
        _word(3, 1, third_name, 0.05, 0.20),
        _word(3, 2, third_price, 0.80, 0.20),
    ]


def _proposal(words: list[dict], subtotal: float):
    return propose_items_boundary_extension(
        words=words,
        summary={"subtotal": subtotal, "tax": None, "grand_total": None},
        current_line_ids={1, 2},
        sections=[_items_section()],
        rows=[_row(1, 0.10), _row(2, 0.15), _row(3, 0.20)],
        current_row_ids=[1, 2],
    )


def test_extension_accepted_when_delta_shrinks_and_status_improves():
    proposal = _proposal(_product_words(), subtotal=9.00)

    assert proposal is not None
    assert proposal["added_line_ids"] == [3]
    assert proposal["before"]["status"] == "mismatch"
    assert proposal["after"]["status"] == "match"
    assert abs(proposal["after"]["delta"]) < abs(proposal["before"]["delta"])


def test_extension_refused_when_delta_grows():
    assert _proposal(_product_words(third_price="50.00"), 9.00) is None


def test_extension_refused_when_status_does_not_improve():
    # 5 -> 9 shrinks the shortfall against 20, but both verdicts mismatch.
    assert _proposal(_product_words(), 20.00) is None


def test_extension_refused_for_settlement_row():
    # The amount closes the gap arithmetically, but TOTAL is a settlement
    # band and therefore cannot be an adjacent priced-product candidate.
    assert _proposal(_product_words(third_name="TOTAL"), 9.00) is None


def test_extension_can_cross_shrinking_mismatch_before_final_match():
    words = _product_words(third_price="4.00") + [
        _word(4, 1, "PEARS", 0.05, 0.25),
        _word(4, 2, "6.00", 0.80, 0.25),
    ]
    proposal = propose_items_boundary_extension(
        words=words,
        summary={"subtotal": 15.00, "tax": None, "grand_total": None},
        current_line_ids={1, 2},
        sections=[_items_section()],
        rows=[
            _row(1, 0.10),
            _row(2, 0.15),
            _row(3, 0.20),
            _row(4, 0.25),
        ],
        current_row_ids=[1, 2],
    )

    assert proposal is not None
    assert proposal["added_line_ids"] == [3, 4]
    assert proposal["before"]["status"] == "mismatch"
    assert proposal["after"]["status"] == "match"


class _FakeDynamo:
    def __init__(self, validation_status: str = "VALID"):
        self.section = _items_section(validation_status=validation_status)
        self.updated_sections = []
        self.line_items = []

    def list_receipt_words_from_receipt(self, _image_id, _receipt_id):
        return [
            SimpleNamespace(
                line_id=word["line_id"],
                word_id=word["word_id"],
                text=word["text"],
                bounding_box={
                    "x": word["x"],
                    "y": word["y_mid"] - word["h"] / 2,
                    "height": word["h"],
                },
            )
            for word in _product_words()
        ]

    def get_receipt_sections_from_receipt(self, _image_id, _receipt_id):
        return [self.section]

    def get_receipt_summary(self, _image_id, _receipt_id):
        return SimpleNamespace(
            summary=SimpleNamespace(
                subtotal=9.00,
                tax=0.72,
                grand_total=9.72,
                merchant_name="Test Mart",
            )
        )

    def get_receipt_rows_from_receipt(self, _image_id, _receipt_id):
        return [_row(1, 0.10), _row(2, 0.15), _row(3, 0.20)]

    def get_receipt_line_items_from_receipt(self, _image_id, _receipt_id):
        # No worker-written rows: the consistency checker is a no-op and the
        # recompute is persisted exactly as it was before the Mac worker
        # became a producer.
        return list(self.line_items)

    def update_receipt_section(self, section):
        self.updated_sections.append(section)

    def delete_receipt_line_items_for_receipt(self, _image_id, _receipt_id):
        return 0

    def add_receipt_line_items(self, line_items):
        self.line_items.extend(line_items)


def test_automatic_extension_preserves_valid_status_and_stamps_provenance(
    monkeypatch,
):
    fake = _FakeDynamo(validation_status="VALID")
    monkeypatch.setattr(line_item_processor, "dynamo_client", fake)
    monkeypatch.delenv("TRIGGER_REOCR_FUNCTION_NAME", raising=False)

    result = line_item_processor.update_receipt_line_items(IMAGE_ID, 1)

    assert result["reconciliation"] == "match"
    assert result["section_extension"]["added_line_ids"] == [3]
    assert len(fake.updated_sections) == 1
    updated = fake.updated_sections[0]
    assert updated.validation_status == "VALID"
    assert updated.line_ids == [1, 2, 3]
    assert updated.row_ids == [1, 2, 3]
    assert updated.model_source.endswith("+zone-gap-extend-v1")
    assert all(
        item.source_section_status == "VALID" for item in fake.line_items
    )
