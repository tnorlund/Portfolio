"""Export -> JSON -> copy-reconstruction round trip for promoted entities.

The dev->prod copy rebuilds entities with ``Class(**exported_dict)``. That
is only valid for FLAT dataclasses: ``asdict()`` turns a nested dataclass
into a plain dict, and the constructor will not rebuild it. It also
assumes the class used to rebuild is the class that was read.

Both assumptions broke in production. ReceiptSummaryRecord WRAPS a
ReceiptSummary (rebuilt with the wrong class), which in turn holds a
MonetaryTotals (rebuilt as a bare dict). Every image in a 749-image
promotion failed, after 661 prod partitions had already been deleted.

These tests pin the round trip for the entity that has nesting, and
assert the flat ones really are flat so a future nested field fails here
instead of mid-promotion.
"""

import json
from dataclasses import asdict, fields, is_dataclass

import pytest

from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_dynamo.entities.receipt_row import ReceiptRow
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _rebuild_summary_record(raw):
    """Mirror of the reconstruction in copy_dynamodb_dev_to_prod."""
    inner = dict(raw["summary"])
    if isinstance(inner.get("totals"), dict):
        inner["totals"] = MonetaryTotals(**inner["totals"])
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(**inner),
        timestamp_computed=raw.get("timestamp_computed"),
        overrides_applied=raw.get("overrides_applied") or [],
    )


def test_summary_record_survives_export_json_roundtrip():
    record = ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=1,
            totals=MonetaryTotals(
                grand_total=12.99, subtotal=11.99, tax=1.0, tip=None
            ),
        ),
        timestamp_computed="2026-09-11T10:19:00+00:00",
        overrides_applied=[],
    )

    # export path: asdict -> json -> load
    raw = json.loads(json.dumps(asdict(record), default=str))
    assert isinstance(raw["summary"]["totals"], dict), (
        "asdict must flatten the nested MonetaryTotals; if this changes, "
        "the reconstruction below needs revisiting"
    )

    rebuilt = _rebuild_summary_record(raw)

    assert isinstance(rebuilt, ReceiptSummaryRecord)
    assert isinstance(rebuilt.summary, ReceiptSummary)
    assert isinstance(rebuilt.summary.totals, MonetaryTotals)
    assert rebuilt.summary.totals.grand_total == pytest.approx(12.99)
    assert rebuilt.summary.totals.tax == pytest.approx(1.0)
    assert rebuilt.summary.image_id == IMAGE_ID


def test_rebuilding_the_inner_summary_is_rejected():
    """Guards the exact production failure: wrong class, nested dict."""
    record = ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=1,
            totals=MonetaryTotals(grand_total=1.0),
        ),
        timestamp_computed=None,
        overrides_applied=[],
    )
    raw = json.loads(json.dumps(asdict(record), default=str))

    # what the broken copy did: rebuild the RECORD dict as a SUMMARY
    with pytest.raises(TypeError):
        ReceiptSummary(**raw)

    # and rebuilding the inner summary without restoring MonetaryTotals
    with pytest.raises(ValueError):
        ReceiptSummary(**raw["summary"])


@pytest.mark.parametrize(
    "entity_cls", [ReceiptRow, ReceiptSection, ReceiptLineItem]
)
def test_promoted_entities_are_flat_enough_for_kwargs_rebuild(entity_cls):
    """Class(**asdict(x)) is only safe while no field is a dataclass."""
    nested = [
        f.name
        for f in fields(entity_cls)
        if is_dataclass(f.type) or "Totals" in str(f.type)
    ]
    assert not nested, (
        f"{entity_cls.__name__} gained nested dataclass field(s) {nested}. "
        "The dev->prod copy rebuilds it with Class(**dict), which will not "
        "reconstruct them -- add explicit reconstruction first."
    )
