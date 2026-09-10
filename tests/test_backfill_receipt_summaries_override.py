"""The backfill script applies owner fact overrides like the Lambda does.

Every writer that recomputes a summary from labels must read the
receipt's ReceiptFactOverride through the DynamoClient accessor and let
its stated facts win; otherwise running the script after stating a fact
silently drops it again.
"""

from datetime import datetime
from types import SimpleNamespace

# isort: off
# scripts/ and receipt_dynamo are grouped differently by the CI jobs that
# do and do not install the local packages; pin the grouping.
from scripts.backfill_receipt_summaries import backfill_summaries

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
KEY = f"{IMAGE_ID}_1"


class FakeClient:
    """Minimal DynamoClient stand-in for backfill_summaries."""

    def __init__(self, override=None):
        self.override = override
        self.override_reads = []
        self.upserted = []
        self.existing = ReceiptSummaryRecord.from_summary(
            ReceiptSummary(
                image_id=IMAGE_ID,
                receipt_id=1,
                totals=MonetaryTotals(grand_total=47.18),
                ledger="chase",
                bank_amount=47.18,
                bank_match_confidence=1.0,
            )
        )

    def list_receipt_places(self, **kwargs):
        return [], None

    def list_receipt_summaries(self, **kwargs):
        return [self.existing], None

    def list_receipt_details(self, **kwargs):
        bundle = SimpleNamespace(
            receipt=SimpleNamespace(image_id=IMAGE_ID, receipt_id=1),
            word_labels=[],
            words=[],
        )
        return SimpleNamespace(bundles={KEY: bundle}, last_evaluated_key=None)

    def get_receipt_fact_override(self, image_id, receipt_id):
        self.override_reads.append((image_id, receipt_id))
        return self.override

    def upsert_receipt_summaries(self, records):
        self.upserted.extend(records)


def test_backfill_applies_the_owner_fact():
    client = FakeClient(
        ReceiptFactOverride(
            image_id=IMAGE_ID,
            receipt_id=1,
            date="2026-09-01",
            date_reference="Chase statement",
        )
    )

    stats = backfill_summaries(client, batch_size=1)

    assert client.override_reads == [(IMAGE_ID, 1)]
    record = client.upserted[0]
    assert record.date == datetime(2026, 9, 1)
    assert record.overrides_applied == ["date"]
    # Offline bank fields still carry over alongside the override.
    assert record.ledger == "chase"
    assert record.bank_amount == 47.18
    assert stats["summaries_with_override"] == 1
    assert stats["summaries_with_date"] == 1


def test_backfill_without_override_is_unchanged():
    client = FakeClient()

    stats = backfill_summaries(client, batch_size=1)

    record = client.upserted[0]
    assert record.date is None
    assert record.overrides_applied == []
    assert stats["summaries_with_override"] == 0


def test_backfill_dry_run_still_reads_the_override():
    client = FakeClient(
        ReceiptFactOverride(
            image_id=IMAGE_ID,
            receipt_id=1,
            merchant_name="Trader Joe's",
            merchant_name_reference="cropped header",
        )
    )

    stats = backfill_summaries(client, dry_run=True)

    assert client.upserted == []
    assert stats["summaries_with_override"] == 1
