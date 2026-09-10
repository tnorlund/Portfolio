"""Unit tests for ReceiptFactOverride and the summary's provenance slot."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.receipt_fact_override import (
    OVERRIDABLE_FACT_FIELDS,
    ReceiptFactOverride,
    apply_fact_override,
    fact_reference_field,
    item_to_receipt_fact_override,
    normalize_changed_at,
    normalize_fact_date,
    receipt_fact_override_key,
)
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

pytestmark = [pytest.mark.unit]

IMAGE_ID = "b7eecdb7-9eaf-47c0-941a-b576604c2e9d"
BANK = "Chase statement 2026-09-01 $47.18"


def override(**changes) -> ReceiptFactOverride:
    fields = dict(
        image_id=IMAGE_ID,
        receipt_id=1,
        revision=1,
        date="2026-09-01",
        date_reference=BANK,
        changed_at="2026-09-10T00:00:00+00:00",
    )
    fields.update(changes)
    return ReceiptFactOverride(**fields)


def test_key_and_type_mirror_the_summary_partition():
    item = override().to_item()

    assert item["PK"] == {"S": f"IMAGE#{IMAGE_ID}"}
    assert item["SK"] == {"S": "RECEIPT#00001#FACT_OVERRIDE"}
    assert item["TYPE"] == {"S": "RECEIPT_FACT_OVERRIDE"}
    assert not any(key.startswith("GSI") for key in item)
    assert receipt_fact_override_key(IMAGE_ID, 1) == override().key


def test_round_trip_preserves_every_field():
    original = override(
        merchant_name="Trader Joe's",
        merchant_name_reference="cropped header",
        revision=4,
    )

    restored = item_to_receipt_fact_override(original.to_item())

    assert restored == original
    assert restored.facts == {
        "date": "2026-09-01",
        "merchant_name": "Trader Joe's",
    }
    assert restored.references == {
        "date": BANK,
        "merchant_name": "cropped header",
    }
    assert restored.to_dict()["source"] == "owner"


def test_unstated_fact_and_its_reference_are_null_and_read_back_as_none():
    item = override().to_item()

    assert item["merchant_name"] == {"NULL": True}
    assert item["merchant_name_reference"] == {"NULL": True}
    restored = item_to_receipt_fact_override(item)
    assert restored.merchant_name is None
    assert restored.merchant_name_reference is None


def test_retracted_override_with_no_facts_is_valid():
    retracted = override(date=None, date_reference=None, revision=5)

    assert retracted.facts == {}
    assert retracted.references == {}
    assert item_to_receipt_fact_override(retracted.to_item()) == retracted


def test_changed_at_defaults_to_aware_utc_now():
    value = override(changed_at=None).changed_at

    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert value.endswith("+00:00")


def test_changed_at_is_normalised_to_utc():
    value = override(changed_at="2026-09-10T08:30:00-07:00").changed_at

    assert value == "2026-09-10T15:30:00+00:00"


@pytest.mark.parametrize(
    "changes",
    [
        {"date_reference": ""},
        {"date_reference": "   "},
        {"date_reference": None},
        {"date": None},
        {"merchant_name": "Costco"},
        {"merchant_name": None, "merchant_name_reference": "x"},
        {"revision": 0},
        {"revision": True},
        {"revision": "1"},
        {"source": "model"},
        {"date": "09/01/2026"},
        {"date": "2026-9-1"},
        {"date": "20260901"},
        {"date": "2026-02-30"},
        {"date": "2026-09-01T00:00:00"},
        {"merchant_name": "", "merchant_name_reference": "x"},
        {"merchant_name": 7, "merchant_name_reference": "x"},
        {"changed_at": "2026-09-10T00:00:00"},
        {"changed_at": "not a timestamp"},
        {"receipt_id": 0},
        {"image_id": "not-a-uuid"},
    ],
)
def test_invalid_fields_are_rejected(changes):
    with pytest.raises(ValueError):
        override(**changes)


def test_validation_error_is_a_value_error_subclass():
    with pytest.raises(EntityValidationError):
        override(date_reference=None)


def test_whitespace_is_trimmed():
    stated = override(
        date_reference="  why  ",
        merchant_name="  Costco  ",
        merchant_name_reference=" logo ",
    )

    assert stated.date_reference == "why"
    assert stated.merchant_name == "Costco"
    assert stated.merchant_name_reference == "logo"


def test_with_fact_states_one_fact_and_keeps_the_other_provenance():
    stated = override()

    updated = stated.with_fact(
        "merchant_name", "Costco", "Costco logo", revision=2
    )

    assert updated.revision == 2
    assert updated.date == "2026-09-01"
    assert updated.date_reference == BANK
    assert updated.merchant_name == "Costco"
    assert updated.merchant_name_reference == "Costco logo"
    assert updated.changed_at != stated.changed_at
    assert stated.merchant_name is None  # the original is untouched


def test_with_fact_retracts_without_touching_the_other_fact():
    stated = override(
        merchant_name="Costco", merchant_name_reference="Costco logo"
    )

    retracted = stated.with_fact("date", None, "ignored", revision=2)

    assert retracted.facts == {"merchant_name": "Costco"}
    assert retracted.date_reference is None
    assert retracted.merchant_name_reference == "Costco logo"


def test_with_fact_rejects_unknown_fact_and_missing_reference():
    with pytest.raises(EntityValidationError):
        override().with_fact("grand_total", "1", "x", revision=2)
    with pytest.raises(EntityValidationError):
        override().with_fact("merchant_name", "Costco", None, revision=2)


def test_fact_reference_field_names():
    assert fact_reference_field("date") == "date_reference"
    assert fact_reference_field("merchant_name") == "merchant_name_reference"
    with pytest.raises(EntityValidationError):
        fact_reference_field("total")


def test_normalize_fact_date_accepts_date_objects():
    assert normalize_fact_date(date(2026, 9, 1)) == "2026-09-01"
    assert normalize_fact_date(datetime(2026, 9, 1, 13, 5)) == "2026-09-01"


def test_normalize_changed_at_rejects_naive_datetimes():
    with pytest.raises(EntityValidationError):
        normalize_changed_at(datetime(2026, 9, 10))
    assert normalize_changed_at(
        datetime(2026, 9, 10, tzinfo=timezone.utc)
    ).endswith("+00:00")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("revision"),
        lambda item: item.update(TYPE={"S": "RECEIPT_SUMMARY"}),
        lambda item: item.update(SK={"S": "RECEIPT#00001#SUMMARY"}),
        lambda item: item.update(PK={"S": "RECEIPT#x"}),
        lambda item: item.update(revision={"N": "abc"}),
        lambda item: item.update(date_reference={"NULL": True}),
    ],
)
def test_from_item_rejects_malformed_items(mutation):
    item = override().to_item()
    mutation(item)

    with pytest.raises(EntityValidationError):
        item_to_receipt_fact_override(item)


def test_replace_re_validates():
    with pytest.raises(EntityValidationError):
        replace(override(), date_reference=None)


def test_hash_is_the_receipt_identity():
    assert hash(override()) == hash(override(revision=9, date="2026-01-01"))


def test_facts_follow_the_application_order():
    stated = override(merchant_name="X", merchant_name_reference="y")

    assert tuple(stated.facts) == OVERRIDABLE_FACT_FIELDS


def summary(**changes) -> ReceiptSummary:
    fields = dict(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name="Academy LA",
        date=datetime(2026, 8, 30),
        totals=MonetaryTotals(grand_total=47.18),
    )
    fields.update(changes)
    return ReceiptSummary(**fields)


def test_apply_fact_override_without_override_returns_the_input():
    base = summary()

    assert apply_fact_override(base, None) == (base, [])


def test_apply_fact_override_with_retracted_row_changes_nothing():
    base = summary()
    retracted = override(date=None, date_reference=None)

    assert apply_fact_override(base, retracted) == (base, [])


def test_apply_fact_override_lets_stated_facts_win():
    base = summary()

    result, applied = apply_fact_override(
        base,
        override(merchant_name="Trader Joe's", merchant_name_reference="x"),
    )

    assert applied == ["date", "merchant_name"]
    assert result.date == datetime(2026, 9, 1)
    assert result.merchant_name == "Trader Joe's"
    assert result.grand_total == 47.18  # unstated facts untouched
    assert base.date == datetime(2026, 8, 30)  # input not mutated


def test_apply_fact_override_is_deterministic():
    stated = override(
        merchant_name="Trader Joe's", merchant_name_reference="x"
    )

    first = apply_fact_override(summary(), stated)
    second = apply_fact_override(summary(), stated)

    assert first == second


def summary_record(**changes) -> ReceiptSummaryRecord:
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(image_id=IMAGE_ID, receipt_id=1),
        timestamp_computed="2026-09-10T00:00:00+00:00",
        **changes,
    )


def test_summary_record_without_overrides_is_byte_identical():
    record = summary_record()

    assert record.overrides_applied == []
    assert "overrides_applied" not in record.to_item()
    assert record.to_dict()["overrides_applied"] == []
    restored = ReceiptSummaryRecord.from_item(record.to_item())
    assert restored.overrides_applied == []


def test_summary_record_round_trips_overrides_applied():
    record = summary_record(overrides_applied=["date", "merchant_name"])

    item = record.to_item()
    assert item["overrides_applied"] == {
        "L": [{"S": "date"}, {"S": "merchant_name"}]
    }
    assert ReceiptSummaryRecord.from_item(item) == record
    assert record.to_dict()["overrides_applied"] == ["date", "merchant_name"]


@pytest.mark.parametrize(
    "value", [["total"], ["date", "date"], "date", [None]]
)
def test_summary_record_rejects_unknown_overrides(value):
    with pytest.raises(ValueError):
        summary_record(overrides_applied=value)


def test_summary_record_from_summary_copies_the_list():
    applied = ["date"]
    record = ReceiptSummaryRecord.from_summary(
        ReceiptSummary(image_id=IMAGE_ID, receipt_id=1), applied
    )
    applied.append("merchant_name")

    assert record.overrides_applied == ["date"]
