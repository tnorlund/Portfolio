"""scripts/backfill_tender_bank.py applies owner fact overrides on re-run.

It rebuilds every stored summary from the bank ledgers. Without the
shared override helper a re-run would re-put a summary computed from
labels alone, dropping the owner's date (or, when the stored value
already carried it, its overrides_applied attribution).
"""

from datetime import datetime

# isort: off
# scripts/ and receipt_dynamo are grouped differently by the CI jobs that
# do and do not install the local packages; pin the grouping.
from scripts.backfill_tender_bank import rebuild_summary_record

from receipt_dynamo.entities.receipt_fact_override import (
    ReceiptFactOverride,
)
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

# isort: on

IMAGE_ID = "b7eecdb7-9eaf-47c0-941a-b576604c2e9d"


class FakeClient:
    def __init__(self, override=None):
        self.override = override
        self.reads = []

    def get_receipt_fact_override(self, image_id, receipt_id):
        self.reads.append((image_id, receipt_id))
        return self.override


def _override() -> ReceiptFactOverride:
    return ReceiptFactOverride(
        image_id=IMAGE_ID,
        receipt_id=1,
        date="2026-09-01",
        date_reference="Chase statement 2026-09-01 $47.18",
    )


def _summary(**changes) -> ReceiptSummary:
    fields = dict(
        image_id=IMAGE_ID,
        receipt_id=1,
        totals=MonetaryTotals(grand_total=47.18),
        tender_class="card",
        card_network="VISA",
        card_last4="1454",
    )
    fields.update(changes)
    return ReceiptSummary(**fields)


def test_owner_date_is_applied_when_the_stored_summary_predates_it():
    client = FakeClient(_override())
    stored = ReceiptSummaryRecord.from_summary(_summary())  # no date yet
    updated = _summary(ledger="chase", bank_amount=47.18)

    record = rebuild_summary_record(client, stored, updated)

    assert client.reads == [(IMAGE_ID, 1)]
    assert record is not None
    assert record.date == datetime(2026, 9, 1)
    assert record.overrides_applied == ["date"]
    assert record.date_source == "owner"
    assert record.ledger == "chase"  # the bank fields still land


def test_rerun_keeps_the_owner_date_and_attribution():
    client = FakeClient(_override())
    stored = ReceiptSummaryRecord.from_summary(
        _summary(date=datetime(2026, 9, 1), ledger="chase", bank_amount=47.18),
        overrides_applied=["date"],
    )
    # The script rebuilds from the stored values: same date, same bank.
    updated = _summary(
        date=datetime(2026, 9, 1), ledger="chase", bank_amount=47.18
    )

    assert rebuild_summary_record(client, stored, updated) is None


def test_missing_attribution_alone_is_rewritten():
    client = FakeClient(_override())
    stored = ReceiptSummaryRecord.from_summary(
        _summary(date=datetime(2026, 9, 1))  # owner date, no attribution
    )

    record = rebuild_summary_record(
        client, stored, _summary(date=datetime(2026, 9, 1))
    )

    assert record is not None
    assert record.overrides_applied == ["date"]
    assert record.date_source == "owner"


def test_without_an_override_behaviour_is_unchanged():
    client = FakeClient()
    stored = ReceiptSummaryRecord.from_summary(_summary())

    assert rebuild_summary_record(client, stored, _summary()) is None
    record = rebuild_summary_record(
        client, stored, _summary(ledger="chase", bank_amount=47.18)
    )
    assert record is not None
    assert record.overrides_applied == []
    assert record.date_source is None
