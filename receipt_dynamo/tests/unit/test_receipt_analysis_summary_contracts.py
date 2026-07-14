"""Contracts for receipt analysis aggregates and persisted summaries."""

from datetime import datetime

import pytest

from receipt_dynamo.entities.receipt_analysis import ReceiptAnalysis
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
    extract_amount,
    parse_date,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
    item_to_receipt_summary_record,
)

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2023-05-15T10:30:00+00:00"


def test_receipt_analysis_defaults_are_independent():
    first = ReceiptAnalysis(image_id=IMAGE_ID, receipt_id=1)
    second = ReceiptAnalysis(image_id=IMAGE_ID, receipt_id=1)

    first.validation_categories.append("mutated")

    assert second.validation_categories == []
    assert second.validation_results == []
    assert second.chatgpt_validations == []
    assert "available=no analyses" in repr(second)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"receipt_id": True}, "receipt_id must be an integer"),
        ({"receipt_id": 0}, "receipt_id must be positive"),
        ({"image_id": "invalid"}, "uuid must be a valid UUID"),
        (
            {"validation_categories": ["invalid"]},
            "validation_categories must contain",
        ),
        (
            {"validation_results": ["invalid"]},
            "validation_results must contain",
        ),
        (
            {"chatgpt_validations": ["invalid"]},
            "chatgpt_validations must contain",
        ),
    ],
)
def test_receipt_analysis_rejects_invalid_contracts(
    kwargs: dict, message: str
):
    params = {"image_id": IMAGE_ID, "receipt_id": 1, **kwargs}
    with pytest.raises(ValueError, match=message):
        ReceiptAnalysis(**params)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("TOTAL $1,234.56", 1234.56),
        ("SKU 100 TOTAL 12.99", 12.99),
        ("no amount", None),
        ("", None),
    ],
)
def test_extract_amount(text: str, expected: float | None):
    assert extract_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("05/15/2023", datetime(2023, 5, 15)),
        ("2023-05-15", datetime(2023, 5, 15)),
        ("05/15/49", datetime(2049, 5, 15)),
        ("05/15/50", datetime(1950, 5, 15)),
        ("02/30/2023", None),
    ],
)
def test_parse_date(text: str, expected: datetime | None):
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "value", [True, "1.0", float("nan"), float("inf"), -float("inf")]
)
def test_monetary_totals_reject_invalid_dynamo_numbers(value: object):
    with pytest.raises(ValueError, match="finite number or None"):
        MonetaryTotals(grand_total=value)


def test_receipt_summary_validates_and_serializes():
    summary = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name="Example Store",
        date=datetime(2023, 5, 15),
        totals=MonetaryTotals(
            grand_total=12,
            subtotal=10.0,
            tax=2.0,
            tip=None,
        ),
        item_count=2,
    )

    assert summary.key == f"{IMAGE_ID}_1"
    assert summary.grand_total == 12.0
    assert summary.to_dict() == {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "merchant_name": "Example Store",
        "date": "2023-05-15T00:00:00",
        "item_count": 2,
        "grand_total": 12.0,
        "subtotal": 10.0,
        "tax": 2.0,
        "tip": None,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("receipt_id", True, "receipt_id must be an integer"),
        ("merchant_name", 1, "merchant_name must be a string or None"),
        ("date", "2023-05-15", "date must be a datetime or None"),
        ("totals", {}, "totals must be a MonetaryTotals object"),
        ("item_count", True, "item_count must be an integer"),
        ("item_count", -1, "item_count must be non-negative"),
    ],
)
def test_receipt_summary_rejects_invalid_fields(
    field: str, value: object, message: str
):
    params = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "merchant_name": None,
        "date": None,
        "totals": MonetaryTotals(),
        "item_count": 0,
    }
    params[field] = value
    with pytest.raises(ValueError, match=message):
        ReceiptSummary(**params)


@pytest.fixture(name="record")
def _record() -> ReceiptSummaryRecord:
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=7,
            merchant_name="Example Store",
            date=datetime(2023, 5, 15),
            totals=MonetaryTotals(
                grand_total=12.5,
                subtotal=10,
                tax=2.5,
                tip=None,
            ),
            item_count=2,
        ),
        timestamp_computed=TIMESTAMP,
    )


def test_summary_record_keys_and_round_trip(record: ReceiptSummaryRecord):
    item = record.to_item()

    assert record.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00007#SUMMARY"},
    }
    assert record.gsi2_key() == {
        "GSI2PK": {"S": "RECEIPT_SUMMARY"},
        "GSI2SK": {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00007"},
    }
    assert record.gsi4_key()["GSI4SK"] == {"S": "5_SUMMARY"}
    assert item["TYPE"] == {"S": "RECEIPT_SUMMARY"}
    assert item["item_count"] == {"N": "2"}
    assert item["grand_total"] == {"N": "12.5"}
    assert item["tip"] == {"NULL": True}
    assert ReceiptSummaryRecord.from_item(item) == record
    assert item_to_receipt_summary_record(item) == record
    assert hash(item_to_receipt_summary_record(item)) == hash(record)


def test_summary_record_optional_values_round_trip():
    summary = ReceiptSummary(image_id=IMAGE_ID, receipt_id=1)
    record = ReceiptSummaryRecord(summary, timestamp_computed=TIMESTAMP)
    restored = ReceiptSummaryRecord.from_item(record.to_item())

    assert restored == record
    assert restored.merchant_name is None
    assert restored.date is None
    assert restored.grand_total is None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("TYPE", {"S": "WRONG"}), "TYPE must be RECEIPT_SUMMARY"),
        (("PK", {"S": "WRONG"}), "PK must match"),
        (("SK", {"S": "WRONG"}), "SK must match"),
        (("date", {"S": "invalid"}), "valid ISO timestamp"),
        (("grand_total", {"N": "invalid"}), "valid number"),
        (("grand_total", {"N": "NaN"}), "finite number or None"),
        (("item_count", {"N": "1.5"}), "valid integer"),
    ],
)
def test_summary_record_rejects_corrupt_items(
    record: ReceiptSummaryRecord,
    mutation: tuple[str, dict],
    message: str,
):
    item = record.to_item()
    item[mutation[0]] = mutation[1]

    with pytest.raises(ValueError, match=message):
        ReceiptSummaryRecord.from_item(item)


def test_summary_record_validates_constructor():
    with pytest.raises(ValueError, match="summary must be a ReceiptSummary"):
        ReceiptSummaryRecord(summary="invalid")

    summary = ReceiptSummary(image_id=IMAGE_ID, receipt_id=1)
    with pytest.raises(ValueError, match="valid ISO format timestamp"):
        ReceiptSummaryRecord(summary, timestamp_computed="invalid")
