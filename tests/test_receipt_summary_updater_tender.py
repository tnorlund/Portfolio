"""Tests for tender-field wiring in the receipt summary updater Lambda.

The updater must (1) classify tender from the receipt's payment zone on
every recompute and (2) carry over the OFFLINE-computed bank-match
fields (ledger, bank_amount, bank_match_confidence) from the stored
summary instead of clobbering them.
"""

from types import SimpleNamespace

# isort: off
# receipt_upload and infra are first-party to isort in jobs that do not
# install them (receipt_agent) and third-party in jobs that do
# (repository tests), so the two CI jobs demand opposite groupings for
# this block. Pin it rather than let one of them fail on every push.
import pytest
from infra.receipt_summary_updater import summary_processor

from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

# isort: on

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
RECEIPT_ID = 7


def _word(line_id, word_id, text):
    return SimpleNamespace(line_id=line_id, word_id=word_id, text=text)


def _label(line_id, word_id, label, status="VALID"):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status=status,
    )


class FakeClient:
    """Minimal DynamoClient stand-in for update_receipt_summary."""

    def __init__(self, existing_summary=None, receipt_exists=True):
        self.existing_summary = existing_summary
        self.receipt_exists = receipt_exists
        self.upserted = []
        self.deleted_summaries = []
        self.lines = [
            {"line_id": 1, "text": "TOTAL 47.18", "y": 0.5},
            {"line_id": 2, "text": "VISA US DEBIT", "y": 0.4},
            {"line_id": 3, "text": "************1454", "y": 0.35},
        ]
        self.sections = [
            {"section_type": "PAYMENT", "line_ids": [2, 3]},
        ]
        self.words = [_word(1, 2, "47.18")]
        self.word_labels = [_label(1, 2, "GRAND_TOTAL")]

    def get_receipt(self, image_id, receipt_id):
        if not self.receipt_exists:
            raise EntityNotFoundError("no receipt")
        return SimpleNamespace(image_id=image_id, receipt_id=receipt_id)

    def delete_receipt_summary(self, record):
        self.deleted_summaries.append(record)
        self.existing_summary = None

    def list_receipt_word_labels_for_receipt(
        self, image_id, receipt_id, last_evaluated_key=None
    ):
        return self.word_labels, None

    def list_receipt_words_from_receipt(self, image_id, receipt_id):
        return self.words

    def list_receipt_lines_from_receipt(self, image_id, receipt_id):
        return self.lines

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return self.sections

    def get_receipt_place(self, image_id, receipt_id):
        raise EntityNotFoundError("no place")

    def get_receipt_summary(self, image_id, receipt_id):
        if self.existing_summary is None:
            raise EntityNotFoundError("no summary")
        return ReceiptSummaryRecord.from_summary(self.existing_summary)

    def upsert_receipt_summary(self, record):
        self.upserted.append(record)


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(summary_processor, "dynamo_client", client)
    return client


def test_recompute_classifies_tender(fake_client):
    result = summary_processor.update_receipt_summary(IMAGE_ID, RECEIPT_ID)

    assert len(fake_client.upserted) == 1
    record = fake_client.upserted[0]
    assert record.tender_class == "card"
    assert record.card_network == "VISA"
    assert record.card_last4 == "1454"
    # bank fields stay unset: no stored summary to carry them from
    assert record.ledger is None
    assert record.bank_amount is None
    assert record.bank_match_confidence is None
    assert result["tender_class"] == "card"
    assert result["card_last4"] == "1454"


def test_recompute_preserves_offline_bank_fields(monkeypatch):
    existing = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        totals=MonetaryTotals(grand_total=47.18),
        tender_class="card",
        card_network="VISA",
        card_last4="1454",
        ledger="chase",
        bank_amount=47.18,
        bank_match_confidence=0.95,
    )
    client = FakeClient(existing_summary=existing)
    monkeypatch.setattr(summary_processor, "dynamo_client", client)

    summary_processor.update_receipt_summary(IMAGE_ID, RECEIPT_ID)

    record = client.upserted[0]
    # tender is recomputed fresh from the payment zone
    assert record.tender_class == "card"
    assert record.card_network == "VISA"
    assert record.card_last4 == "1454"
    # offline bank-match fields are carried over, not clobbered
    assert record.ledger == "chase"
    assert record.bank_amount == 47.18
    assert record.bank_match_confidence == 0.95


def test_skips_regen_and_sweeps_orphan_when_parent_deleted(monkeypatch):
    """A re-segmentation apply deletes the source receipt; the child-row
    deletion events must NOT resurrect its summary. The updater skips the
    recompute and deletes any freshly re-created orphan summary row."""
    existing = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        totals=MonetaryTotals(grand_total=47.18),
    )
    client = FakeClient(existing_summary=existing, receipt_exists=False)
    monkeypatch.setattr(summary_processor, "dynamo_client", client)

    result = summary_processor.update_receipt_summary(IMAGE_ID, RECEIPT_ID)

    assert result["skipped"] == "parent receipt deleted"
    assert result["orphan_summary_deleted"] is True
    assert client.upserted == []
    assert len(client.deleted_summaries) == 1


def test_skips_regen_when_parent_deleted_and_no_orphan(monkeypatch):
    """Parent gone and no summary row present: skip without writing."""
    client = FakeClient(receipt_exists=False)
    monkeypatch.setattr(summary_processor, "dynamo_client", client)

    result = summary_processor.update_receipt_summary(IMAGE_ID, RECEIPT_ID)

    assert result["skipped"] == "parent receipt deleted"
    assert result["orphan_summary_deleted"] is False
    assert client.upserted == []
    assert client.deleted_summaries == []


def test_cash_receipt_classified(monkeypatch):
    client = FakeClient()
    client.lines = [
        {"line_id": 1, "text": "CASH 25.00", "y": 0.4},
        {"line_id": 2, "text": "CHANGE 1.78", "y": 0.35},
    ]
    client.sections = [{"section_type": "PAYMENT", "line_ids": [1, 2]}]
    monkeypatch.setattr(summary_processor, "dynamo_client", client)

    summary_processor.update_receipt_summary(IMAGE_ID, RECEIPT_ID)

    record = client.upserted[0]
    assert record.tender_class == "cash"
    assert record.card_network is None
    assert record.card_last4 is None
