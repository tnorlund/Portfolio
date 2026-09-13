"""Export -> JSON -> copy-reconstruction round trip for promoted entities.

The dev->prod copy rebuilds entities with ``Class(**exported_dict)``. That
is only valid for FLAT dataclasses: ``asdict()`` turns a nested dataclass
into a plain dict and the constructor will not rebuild it. It also
assumes the class used to rebuild is the class that was read.

Both assumptions broke in production. ReceiptSummaryRecord WRAPS a
ReceiptSummary (rebuilt with the wrong class), which in turn holds a
MonetaryTotals (rebuilt as a bare dict). Every image in a 749-image
promotion failed, after 661 prod partitions had already been deleted.

These tests exercise the SAME helper the copier calls, and round-trip the
flat entities for real rather than inspecting annotations -- an optional
or container-held nested dataclass (``x: Details | None``,
``x: list[Details]``) is invisible to ``is_dataclass(field.type)`` but
still breaks the rebuild.
"""

import json
import types
import typing
from dataclasses import asdict, fields, is_dataclass

import pytest

from receipt_dynamo.data.export_image import (
    datetime_handler,
    receipt_summary_record_from_export,
)
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
CREATED = "2026-09-11T10:19:00+00:00"


def _export(entity):
    """Exactly what export_image writes, and what the copier reads back."""
    return json.loads(json.dumps(asdict(entity), default=datetime_handler))


def _sample_summary_record():
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=1,
            totals=MonetaryTotals(
                grand_total=12.99, subtotal=11.99, tax=1.0, tip=None
            ),
        ),
        timestamp_computed=CREATED,
        overrides_applied=[],
    )


FLAT_ENTITIES = [
    ReceiptRow(
        receipt_id=1,
        image_id=IMAGE_ID,
        row_id=1,
        line_ids=[1, 2],
        grouping_version="v1",
        y_min=0.1,
        y_max=0.2,
        x_min=0.0,
        x_max=1.0,
        created_at=CREATED,
    ),
    ReceiptSection(
        receipt_id=1,
        image_id=IMAGE_ID,
        section_type="ITEMS",
        line_ids=[1, 2],
        created_at=CREATED,
    ),
    ReceiptLineItem(
        receipt_id=1,
        image_id=IMAGE_ID,
        item_index=0,
        name="BYO SANDWICH",
        price="11.99",
        line_ids=[1],
        extractor_version="v1",
        extracted_at=CREATED,
    ),
]


def test_summary_record_roundtrips_through_the_shared_helper():
    """The copier's own reconstruction, not a copy of it."""
    record = _sample_summary_record()
    rebuilt = receipt_summary_record_from_export(_export(record))

    assert isinstance(rebuilt, ReceiptSummaryRecord)
    assert isinstance(rebuilt.summary, ReceiptSummary)
    assert isinstance(rebuilt.summary.totals, MonetaryTotals)
    assert rebuilt.summary.totals.grand_total == pytest.approx(12.99)
    assert rebuilt.summary.totals.subtotal == pytest.approx(11.99)
    assert rebuilt.summary.totals.tax == pytest.approx(1.0)
    assert rebuilt.summary.totals.tip is None
    assert rebuilt.summary.image_id == IMAGE_ID
    assert rebuilt.summary.receipt_id == 1


def test_both_production_failures_are_reproduced():
    """Pins the two ways the copier actually broke in production."""
    raw = _export(_sample_summary_record())

    # rebuilding the RECORD dict as a SUMMARY
    with pytest.raises(TypeError):
        ReceiptSummary(**raw)

    # rebuilding the inner summary without restoring MonetaryTotals
    with pytest.raises(ValueError):
        ReceiptSummary(**raw["summary"])


@pytest.mark.parametrize(
    "entity", FLAT_ENTITIES, ids=lambda e: type(e).__name__
)
def test_flat_entities_survive_a_real_kwargs_rebuild(entity):
    """Round-trip for real; annotation inspection misses too much.

    A nested dataclass behind ``| None`` or inside ``list[...]`` would
    pass a type-annotation check but still come back as a dict here.
    """
    rebuilt = type(entity)(**_export(entity))

    assert rebuilt == entity, (
        f"{type(entity).__name__} no longer survives Class(**asdict(x)). "
        "If it gained a nested dataclass field, the dev->prod copy needs "
        "explicit reconstruction for it, like "
        "receipt_summary_record_from_export."
    )


def _nested_dataclass_types(tp) -> set:
    """Every dataclass reachable through a field annotation.

    Walks Optional/Union, list/tuple/dict/set args and string forward refs
    so ``Details | None`` and ``list[Details]`` are found, not just a bare
    ``Details`` annotation.
    """
    found = set()
    if isinstance(tp, str):
        return found
    if is_dataclass(tp):
        found.add(tp)
        return found
    origin = typing.get_origin(tp)
    if origin is typing.Union or isinstance(tp, types.UnionType):
        for arg in typing.get_args(tp):
            found |= _nested_dataclass_types(arg)
    elif origin is not None:
        for arg in typing.get_args(tp):
            found |= _nested_dataclass_types(arg)
    return found


@pytest.mark.parametrize(
    "entity_cls",
    [ReceiptRow, ReceiptSection, ReceiptLineItem],
    ids=lambda c: c.__name__,
)
def test_flat_entities_declare_no_nested_dataclass_anywhere(entity_cls):
    """Static complement to the round trip above.

    The round trip only exercises fields the fixture populates, so an
    optional or container-held nested dataclass left at its empty default
    would pass it. This walks the resolved annotations instead and fails
    the moment such a field is declared, populated or not.
    """
    hints = typing.get_type_hints(entity_cls)
    nested = {
        f.name: sorted(
            t.__name__ for t in _nested_dataclass_types(hints[f.name])
        )
        for f in fields(entity_cls)
        if _nested_dataclass_types(hints.get(f.name, f.type))
    }
    assert not nested, (
        f"{entity_cls.__name__} declares nested dataclass field(s) {nested}; "
        "Class(**asdict(x)) will not rebuild them -- add explicit "
        "reconstruction like receipt_summary_record_from_export and copy "
        "it into copy_dynamodb_dev_to_prod / import_image."
    )


def test_nested_type_walker_sees_optional_and_container_forms():
    """Guard the guard: the walker must catch the shapes Codex named."""
    assert _nested_dataclass_types(MonetaryTotals | None) == {MonetaryTotals}
    assert _nested_dataclass_types(list[MonetaryTotals]) == {MonetaryTotals}
    assert _nested_dataclass_types(dict[str, MonetaryTotals]) == {
        MonetaryTotals
    }
    assert _nested_dataclass_types(typing.Optional[list[MonetaryTotals]]) == {
        MonetaryTotals
    }
    assert _nested_dataclass_types(int | None) == set()
