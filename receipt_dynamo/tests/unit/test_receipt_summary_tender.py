"""Unit tests for the tender / bank-match fields on ReceiptSummary and
ReceiptSummaryRecord.

These fields are optional and non-breaking: items written before the
fields existed must round-trip unchanged, and records without tender
data must serialize byte-identically to the pre-tender format.
"""

from datetime import datetime

import pytest

from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
    item_to_receipt_summary_record,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


@pytest.fixture
def tender_summary() -> ReceiptSummary:
    return ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=7,
        merchant_name="Sprouts Farmers Market",
        date=datetime(2025, 5, 28),
        totals=MonetaryTotals(grand_total=47.18, tax=1.23),
        item_count=4,
        tender_class="card",
        card_network="MASTERCARD",
        card_last4="7645",
        ledger="apple",
        bank_amount=47.18,
        bank_match_confidence=0.95,
        bank_date=datetime(2025, 5, 29),
    )


@pytest.mark.unit
def test_round_trip_with_tender_fields(tender_summary):
    record = ReceiptSummaryRecord.from_summary(tender_summary)
    item = record.to_item()

    assert item["tender_class"] == {"S": "card"}
    assert item["card_network"] == {"S": "MASTERCARD"}
    assert item["card_last4"] == {"S": "7645"}
    assert item["ledger"] == {"S": "apple"}
    assert item["bank_amount"] == {"N": "47.18"}
    assert item["bank_match_confidence"] == {"N": "0.95"}
    assert item["bank_date"] == {"S": "2025-05-29T00:00:00"}

    restored = ReceiptSummaryRecord.from_item(item)
    assert restored.tender_class == "card"
    assert restored.card_network == "MASTERCARD"
    assert restored.card_last4 == "7645"
    assert restored.ledger == "apple"
    assert restored.bank_amount == 47.18
    assert restored.bank_match_confidence == 0.95
    assert restored.bank_date == datetime(2025, 5, 29)
    assert restored.summary == tender_summary


@pytest.mark.unit
def test_round_trip_without_tender_fields():
    """A summary with no tender data serializes without the new keys."""
    summary = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name="Vons",
        totals=MonetaryTotals(grand_total=12.34),
    )
    item = ReceiptSummaryRecord.from_summary(summary).to_item()
    for key in (
        "tender_class",
        "card_network",
        "card_last4",
        "ledger",
        "bank_amount",
        "bank_match_confidence",
        "bank_date",
    ):
        assert key not in item

    restored = item_to_receipt_summary_record(item)
    assert restored.tender_class is None
    assert restored.card_network is None
    assert restored.card_last4 is None
    assert restored.ledger is None
    assert restored.bank_amount is None
    assert restored.bank_match_confidence is None
    assert restored.bank_date is None
    assert restored.effective_date is None
    assert restored.date_source is None


@pytest.mark.unit
def test_from_item_tolerates_pre_tender_items():
    """Items written before the tender fields existed still parse."""
    summary = ReceiptSummary(image_id=IMAGE_ID, receipt_id=2)
    item = ReceiptSummaryRecord.from_summary(summary).to_item()
    # simulate a legacy item: strip anything tender-shaped (none written)
    restored = ReceiptSummaryRecord.from_item(item)
    assert restored.summary.tender_class is None
    assert restored.summary.ledger is None


@pytest.mark.unit
def test_cash_tender_round_trip():
    summary = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=3,
        tender_class="cash",
        ledger="none",
    )
    item = ReceiptSummaryRecord.from_summary(summary).to_item()
    restored = ReceiptSummaryRecord.from_item(item)
    assert restored.tender_class == "cash"
    assert restored.ledger == "none"
    assert restored.card_network is None
    assert restored.card_last4 is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"tender_class": "check"},
        {"tender_class": "CARD"},
        {"card_last4": "12345"},
        {"card_last4": "12a4"},
        {"card_last4": 7645},
        {"ledger": "amex"},
        {"bank_amount": float("nan")},
        {"bank_amount": True},
        {"bank_match_confidence": 1.5},
        {"bank_match_confidence": -0.1},
        {"bank_date": "2025-05-29"},
        {"bank_date": 20250529},
    ],
)
def test_invalid_tender_fields_rejected(kwargs):
    with pytest.raises(ValueError):
        ReceiptSummary(image_id=IMAGE_ID, receipt_id=4, **kwargs)


@pytest.mark.unit
def test_from_word_labels_and_words_accepts_tender_kwargs():
    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=5,
        merchant_name="Costco",
        word_labels=[],
        words=[],
        tender_class="card",
        card_network="VISA",
        card_last4="1454",
        ledger="chase",
        bank_amount=101.44,
        bank_match_confidence=1.0,
        bank_date=datetime(2026, 8, 17),
    )
    assert summary.tender_class == "card"
    assert summary.card_network == "VISA"
    assert summary.card_last4 == "1454"
    assert summary.ledger == "chase"
    assert summary.bank_amount == 101.44
    assert summary.bank_match_confidence == 1.0
    assert summary.bank_date == datetime(2026, 8, 17)


@pytest.mark.unit
def test_to_dict_includes_tender_fields(tender_summary):
    d = tender_summary.to_dict()
    assert d["tender_class"] == "card"
    assert d["card_network"] == "MASTERCARD"
    assert d["card_last4"] == "7645"
    assert d["ledger"] == "apple"
    assert d["bank_amount"] == 47.18
    assert d["bank_match_confidence"] == 0.95
    assert d["bank_date"] == "2025-05-29T00:00:00"
    # the printed date wins over the bank date when both are known
    assert d["date"] == "2025-05-28T00:00:00"
    assert d["effective_date"] == "2025-05-28T00:00:00"
    assert d["date_source"] == "label"


@pytest.mark.unit
def test_effective_date_falls_back_to_bank_date():
    """No legible printed date -> the matched bank date is the date."""
    summary = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=6,
        merchant_name="Marufuku Ramen",
        totals=MonetaryTotals(grand_total=31.96),
        ledger="chase",
        bank_amount=37.27,
        bank_match_confidence=1.0,
        bank_date=datetime(2026, 4, 16),
    )
    assert summary.date is None
    assert summary.effective_date == datetime(2026, 4, 16)
    assert summary.date_source == "bank"
    d = summary.to_dict()
    assert d["date"] is None
    assert d["effective_date"] == "2026-04-16T00:00:00"
    assert d["date_source"] == "bank"

    record = ReceiptSummaryRecord.from_item(
        ReceiptSummaryRecord.from_summary(summary).to_item()
    )
    assert record.date is None
    assert record.effective_date == datetime(2026, 4, 16)
    assert record.date_source == "bank"


@pytest.mark.unit
def test_bank_date_is_an_offline_field():
    from receipt_dynamo.entities.receipt_summary_record import (
        OFFLINE_BANK_FIELDS,
    )

    assert "bank_date" in OFFLINE_BANK_FIELDS
