"""The stream stage as a CONSISTENCY CHECKER over worker-written rows.

Phase 2 of moving line-item production onto the Mac worker: the worker
decodes sections + items on device and the ingest handler persists them, so
by the time a summary change fires this Lambda the rows may already exist.
It must then COMPARE rather than blindly overwrite.

Pinned here:
  1. no worker rows -> byte-identical to the pre-worker behavior
  2. a strictly better worker result is preserved, not clobbered
  3. a worse (or unrankable) worker result loses to the recompute
  4. divergence is logged under a single queryable marker either way
"""

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from infra.receipt_line_item_updater import line_item_processor
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem

from receipt_upload.line_items.provenance import (
    SWIFT_WORKER_EXTRACTOR_VERSION,
)

IMAGE_ID = "11111111-2222-4333-8444-555555555555"
RECEIPT_ID = 1
CLOUD_EXTRACTOR_VERSION = line_item_processor.EXTRACTOR_VERSION


# ---------------------------------------------------------------------------
# Fixture receipt: three priced product rows summing to 9.00, which is also
# the printed subtotal -- so the cloud recompute reconciles to "match".
# ---------------------------------------------------------------------------

_PRODUCTS = [("APPLES", 3.00), ("BREAD", 4.00), ("MILK", 2.00)]


def _word(line_id, word_id, text, x, y):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": 0.01,
    }


def _product_words():
    words = []
    for index, (name, price) in enumerate(_PRODUCTS, start=1):
        y = 0.10 + index * 0.05
        words.append(_word(index, 1, name, 0.10, y))
        words.append(_word(index, 2, f"{price:.2f}", 0.80, y))
    return words


def _items_section():
    return SimpleNamespace(
        section_type="ITEMS",
        line_ids=[1, 2, 3],
        row_ids=[1, 2, 3],
        validation_status="PENDING",
        model_source="swift-worker-v1",
    )


def _worker_row(item_index, name, price, extractor_version=None):
    return ReceiptLineItem(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        item_index=item_index,
        name=name,
        price=f"{price:.2f}",
        line_ids=[item_index + 1],
        extractor_version=(
            extractor_version or SWIFT_WORKER_EXTRACTOR_VERSION
        ),
        extracted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        reconciliation_status="no-baseline",
        source_model_source="swift-worker-v1",
        source_section_status="PENDING",
    )


class _FakeDynamo:
    """Enough DynamoClient surface for one receipt's recompute."""

    def __init__(self, stored_line_items=None, subtotal=9.00):
        self.stored = list(stored_line_items or [])
        self.subtotal = subtotal
        self.written = []
        self.deleted = 0

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
        return [_items_section()]

    def get_receipt_summary(self, _image_id, _receipt_id):
        return SimpleNamespace(
            summary=SimpleNamespace(
                subtotal=self.subtotal,
                tax=0.72,
                grand_total=round(self.subtotal + 0.72, 2),
                merchant_name="Test Mart",
            )
        )

    def get_receipt_rows_from_receipt(self, _image_id, _receipt_id):
        return []

    def update_receipt_section(self, _section):
        raise AssertionError("no boundary extension expected")

    def get_receipt_line_items_from_receipt(self, _image_id, _receipt_id):
        return list(self.stored)

    def delete_receipt_line_items_for_receipt(self, _image_id, _receipt_id):
        self.deleted = len(self.stored)
        return self.deleted

    def add_receipt_line_items(self, line_items):
        self.written.extend(line_items)


@pytest.fixture
def run(monkeypatch):
    def _run(fake):
        monkeypatch.setattr(line_item_processor, "dynamo_client", fake)
        monkeypatch.delenv("TRIGGER_REOCR_FUNCTION_NAME", raising=False)
        return line_item_processor.update_receipt_line_items(
            IMAGE_ID, RECEIPT_ID
        )

    return _run


def _divergence_logs(caplog):
    return [
        json.loads(
            record.getMessage().split(
                line_item_processor.DIVERGENCE_MARKER + " ", 1
            )[1]
        )
        for record in caplog.records
        if line_item_processor.DIVERGENCE_MARKER in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# 1. Back-compat: nothing worker-written -> nothing changes
# ---------------------------------------------------------------------------


def test_no_worker_rows_keeps_the_pre_worker_behavior(run, caplog):
    caplog.set_level(logging.INFO)
    fake = _FakeDynamo()

    result = run(fake)

    assert result["reconciliation"] == "match"
    assert result["worker_divergence"] is None
    assert [item.name for item in fake.written] == [
        name for name, _ in _PRODUCTS
    ]
    assert all(
        item.extractor_version == CLOUD_EXTRACTOR_VERSION
        for item in fake.written
    )
    assert _divergence_logs(caplog) == []


def test_prior_cloud_rows_are_not_mistaken_for_worker_rows(run, caplog):
    """A previous recompute's own rows must not trip the checker."""
    caplog.set_level(logging.INFO)
    stale = [
        _worker_row(
            index,
            name,
            price,
            extractor_version=CLOUD_EXTRACTOR_VERSION,
        )
        for index, (name, price) in enumerate(_PRODUCTS)
    ]
    fake = _FakeDynamo(stored_line_items=stale)

    result = run(fake)

    assert result["worker_divergence"] is None
    assert _divergence_logs(caplog) == []


# ---------------------------------------------------------------------------
# 2. A strictly better worker result survives the recompute
# ---------------------------------------------------------------------------


def test_strictly_better_worker_rows_are_not_clobbered(run, caplog):
    """Worker reconciles to match; the recompute misses an item entirely.

    A printed subtotal of 12.00 makes the cloud's three-item decode a
    mismatch (delta -3.00) while the worker's four-item decode lands on the
    baseline exactly -- smaller |delta| AND a better status, the two things
    ``items_boundary_extension_guard`` demands.
    """
    caplog.set_level(logging.INFO)
    worker = [
        _worker_row(index, name, price)
        for index, (name, price) in enumerate(_PRODUCTS + [("EGGS", 3.00)])
    ]
    fake = _FakeDynamo(stored_line_items=worker, subtotal=12.00)

    result = run(fake)

    assert result["reconciliation"] == "match"
    assert [item.name for item in fake.written] == [
        "APPLES",
        "BREAD",
        "MILK",
        "EGGS",
    ]
    # Preserved rows keep the worker's provenance...
    assert all(
        item.extractor_version == SWIFT_WORKER_EXTRACTOR_VERSION
        for item in fake.written
    )
    # ...and gain the context only the cloud has.
    assert all(item.merchant_name == "Test Mart" for item in fake.written)
    assert all(item.reconciliation_status == "match" for item in fake.written)
    assert all(item.baseline_figures_agreeing for item in fake.written)

    record = result["worker_divergence"]
    assert record["decision"] == "keep-worker"
    assert record["divergent"] == 1
    assert record["worker_status"] == "match"
    assert record["cloud_status"] == "mismatch"
    assert record["worker_count"] == 4
    assert record["cloud_count"] == 3
    assert record["worker_extractor_version"] == (
        SWIFT_WORKER_EXTRACTOR_VERSION
    )
    assert _divergence_logs(caplog) == [record]


# ---------------------------------------------------------------------------
# 3. A worse (or unrankable) worker result loses
# ---------------------------------------------------------------------------


def test_worse_worker_rows_lose_to_the_recompute(run, caplog):
    caplog.set_level(logging.INFO)
    worker = [_worker_row(0, "APPLES", 3.00)]
    fake = _FakeDynamo(stored_line_items=worker)

    result = run(fake)

    assert result["reconciliation"] == "match"
    assert [item.name for item in fake.written] == [
        name for name, _ in _PRODUCTS
    ]
    assert all(
        item.extractor_version == CLOUD_EXTRACTOR_VERSION
        for item in fake.written
    )
    record = result["worker_divergence"]
    assert record["decision"] == "keep-recompute"
    assert record["divergent"] == 1
    assert record["cloud_status"] == "match"
    # The guard refuses to displace an already-matching recompute, and says
    # so -- reusing the ITEMS-boundary wording verbatim.
    assert "already reconciles" in record["guard_reason"]


def test_agreeing_worker_rows_are_logged_but_not_flagged(run, caplog):
    """Identical decodes still emit the marker, with divergent = 0.

    The recompute wins by default (it carries the graded reconciliation),
    but the line must exist so a CloudWatch query can measure the agreement
    rate, not just the failures.
    """
    caplog.set_level(logging.INFO)
    worker = [
        _worker_row(index, name, price)
        for index, (name, price) in enumerate(_PRODUCTS)
    ]
    fake = _FakeDynamo(stored_line_items=worker)

    result = run(fake)

    record = result["worker_divergence"]
    assert record["divergent"] == 0
    assert record["decision"] == "keep-recompute"
    assert record["name_mismatches"] == 0
    assert record["price_mismatches"] == 0
    logged = _divergence_logs(caplog)
    assert logged == [record]


def test_divergence_marker_is_greppable_json(run, caplog):
    """One marker, one JSON body -> a CloudWatch Insights filter."""
    caplog.set_level(logging.INFO)
    worker = [_worker_row(0, "APPLES", 3.00)]
    fake = _FakeDynamo(stored_line_items=worker)

    run(fake)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith(
            line_item_processor.DIVERGENCE_MARKER
        )
    ]
    assert len(messages) == 1
    prefix, body = messages[0].split(" ", 1)
    assert prefix == "LINE_ITEM_DIVERGENCE"
    payload = json.loads(body)
    assert set(payload) >= {
        "image_id",
        "receipt_id",
        "divergent",
        "decision",
        "worker_count",
        "cloud_count",
        "worker_status",
        "cloud_status",
        "worker_delta",
        "cloud_delta",
        "name_mismatches",
        "price_mismatches",
        "worker_extractor_version",
    }
