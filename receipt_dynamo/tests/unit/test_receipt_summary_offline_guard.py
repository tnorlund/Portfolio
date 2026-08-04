"""Unit tests for the offline-bank-field guard on summary upserts.

ledger / bank_amount / bank_match_confidence are computed OFFLINE from
the local Chase + Apple ledgers (scripts/backfill_tender_bank.py) and
cannot be re-derived in the cloud; bank_amount is half of the PROVEN
definition. A summary recompute that writes without carrying them over
destroys data only a laptop can restore — this wiped dev table-wide on
2026-08-04 (bank_amount 425 -> 3, dev PROVEN 281 -> 2). These tests pin
the pure detector and the upsert guard that makes it loud.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    OFFLINE_BANK_FIELDS,
    ReceiptSummaryRecord,
    offline_fields_cleared,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def make_record(
    *,
    ledger: str | None = None,
    bank_amount: float | None = None,
    bank_match_confidence: float | None = None,
) -> ReceiptSummaryRecord:
    summary = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=7,
        merchant_name="Sprouts Farmers Market",
        date=datetime(2025, 5, 28),
        totals=MonetaryTotals(grand_total=47.18, tax=1.23),
        item_count=4,
        ledger=ledger,
        bank_amount=bank_amount,
        bank_match_confidence=bank_match_confidence,
    )
    return ReceiptSummaryRecord.from_summary(summary)


BANKED = dict(ledger="apple", bank_amount=47.18, bank_match_confidence=0.95)


# === offline_fields_cleared (pure detector) ===


@pytest.mark.unit
def test_detects_all_offline_fields_cleared():
    cleared = offline_fields_cleared(make_record(), make_record(**BANKED))
    assert cleared == list(OFFLINE_BANK_FIELDS)


@pytest.mark.unit
def test_carry_over_clears_nothing():
    assert (
        offline_fields_cleared(make_record(**BANKED), make_record(**BANKED))
        == []
    )


@pytest.mark.unit
def test_never_populated_clears_nothing():
    assert offline_fields_cleared(make_record(), make_record()) == []


@pytest.mark.unit
def test_partial_clear_names_only_the_cleared_fields():
    new = make_record(ledger="apple")  # bank_amount + confidence nulled
    cleared = offline_fields_cleared(new, make_record(**BANKED))
    assert cleared == ["bank_amount", "bank_match_confidence"]


# === upsert guard wiring ===


def guarded_client(existing: ReceiptSummaryRecord | None) -> DynamoClient:
    """DynamoClient with stubbed I/O: get returns `existing`, writes record."""
    client = DynamoClient.__new__(DynamoClient)
    client.table_name = "test-table"
    client._client = MagicMock()
    client._batch_write_with_retry = MagicMock()
    if existing is None:
        client.get_receipt_summary = MagicMock(
            side_effect=EntityNotFoundError("not found")
        )
    else:
        client.get_receipt_summary = MagicMock(return_value=existing)
    return client


@pytest.mark.unit
def test_upsert_raises_when_clearing_stored_bank_fields():
    client = guarded_client(existing=make_record(**BANKED))
    with pytest.raises(ValueError, match="offline bank field"):
        client.upsert_receipt_summary(make_record())
    client._client.put_item.assert_not_called()


@pytest.mark.unit
def test_upsert_allows_clear_with_explicit_opt_in():
    client = guarded_client(existing=make_record(**BANKED))
    client.upsert_receipt_summary(
        make_record(), allow_offline_field_clear=True
    )
    client._client.put_item.assert_called_once()


@pytest.mark.unit
def test_upsert_passes_when_fields_carried_over():
    client = guarded_client(existing=make_record(**BANKED))
    client.upsert_receipt_summary(make_record(**BANKED))
    client._client.put_item.assert_called_once()


@pytest.mark.unit
def test_upsert_passes_when_no_stored_summary():
    client = guarded_client(existing=None)
    client.upsert_receipt_summary(make_record())
    client._client.put_item.assert_called_once()


@pytest.mark.unit
def test_batch_upsert_raises_when_any_record_clears_bank_fields():
    client = guarded_client(existing=make_record(**BANKED))
    with pytest.raises(ValueError, match="offline bank field"):
        client.upsert_receipt_summaries([make_record(**BANKED), make_record()])
    client._batch_write_with_retry.assert_not_called()


@pytest.mark.unit
def test_batch_upsert_passes_with_carry_over():
    client = guarded_client(existing=make_record(**BANKED))
    client.upsert_receipt_summaries([make_record(**BANKED)])
    client._batch_write_with_retry.assert_called_once()
