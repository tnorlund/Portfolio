"""Contracts for :class:`ReceiptValidationSummary`."""

from copy import deepcopy
from datetime import datetime

import pytest

from receipt_dynamo import (
    ReceiptValidationSummary,
    item_to_receipt_validation_summary,
)

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2023-05-15T10:30:00"


@pytest.fixture(name="summary_kwargs")
def _summary_kwargs() -> dict:
    return {
        "receipt_id": 1,
        "image_id": IMAGE_ID,
        "overall_status": "valid",
        "overall_reasoning": "The receipt passed all checks.",
        "field_summary": {
            "business_identity": {
                "status": "valid",
                "count": 2,
                "has_errors": False,
                "checks": [True, 1, 0.5, None, {"nested": False}],
            }
        },
        "validation_timestamp": TIMESTAMP,
        "version": "1.0.0",
        "metadata": {
            "source_info": {"model": "test-model"},
            "processing_metrics": {"execution_time_ms": 150},
            "processing_history": [
                {"event_type": "started", "attempt": 1, "success": True}
            ],
        },
        "timestamp_added": datetime.fromisoformat(TIMESTAMP),
        "timestamp_updated": TIMESTAMP,
    }


@pytest.fixture(name="summary")
def _summary(summary_kwargs: dict) -> ReceiptValidationSummary:
    return ReceiptValidationSummary(**summary_kwargs)


def test_initialization_normalizes_and_copies_inputs(summary_kwargs: dict):
    field_summary = summary_kwargs["field_summary"]
    metadata = summary_kwargs["metadata"]
    summary = ReceiptValidationSummary(**summary_kwargs)

    field_summary["business_identity"]["count"] = 99
    metadata["source_info"]["model"] = "mutated"

    assert summary.field_summary["business_identity"]["count"] == 2
    assert summary.metadata["source_info"]["model"] == "test-model"
    assert summary.timestamp_added == TIMESTAMP
    assert summary.timestamp_updated == TIMESTAMP


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("receipt_id", True, "receipt_id must be an integer"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("image_id", 1, "uuid must be a string"),
        ("image_id", "invalid", "uuid must be a valid UUID"),
        ("overall_status", "", "overall_status must not be empty"),
        ("overall_reasoning", 1, "overall_reasoning must be a string"),
        ("field_summary", [], "field_summary must be a dictionary"),
        ("validation_timestamp", "invalid", "valid ISO format timestamp"),
        ("version", "", "version must not be empty"),
        ("metadata", [], "metadata must be a dictionary"),
        ("timestamp_added", "invalid", "valid ISO format timestamp"),
        ("timestamp_updated", 1, "datetime object or ISO format string"),
    ],
)
def test_initialization_rejects_invalid_fields(
    summary_kwargs: dict, field: str, value: object, message: str
):
    summary_kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        ReceiptValidationSummary(**summary_kwargs)


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {"processing_metrics": []},
            "processing_metrics must be a dictionary",
        ),
        ({"source_info": []}, "source_info must be a dictionary"),
        ({"processing_history": {}}, "processing_history must be a list"),
    ],
)
def test_metadata_structure_contract(
    summary_kwargs: dict, metadata: dict, message: str
):
    summary_kwargs["metadata"] = metadata
    with pytest.raises(ValueError, match=message):
        ReceiptValidationSummary(**summary_kwargs)


def test_default_metadata_is_independent(summary_kwargs: dict):
    summary_kwargs.update(
        metadata=None, timestamp_added=None, timestamp_updated=None
    )
    first = ReceiptValidationSummary(**summary_kwargs)
    second = ReceiptValidationSummary(**summary_kwargs)

    first.add_processing_metric("latency_ms", 25)
    first.add_history_event("completed", {"success": True})

    assert first.metadata["processing_metrics"] == {"latency_ms": 25}
    assert first.metadata["processing_history"][0]["success"] is True
    assert second.metadata == {
        "processing_metrics": {},
        "source_info": {},
        "processing_history": [],
    }


def test_keys_and_item_schema(summary: ReceiptValidationSummary):
    item = summary.to_item()

    assert summary.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001#ANALYSIS#VALIDATION"},
    }
    assert summary.gsi1_key()["GSI1SK"] == {"S": f"VALIDATION#{TIMESTAMP}"}
    assert summary.gsi2_key()["GSI2PK"] == {
        "S": "VALIDATION_SUMMARY_STATUS#valid"
    }
    assert summary.gsi3_key()["GSI3PK"] == {"S": "VALIDATION_STATUS#valid"}
    assert item["TYPE"] == {"S": "RECEIPT_VALIDATION_SUMMARY"}
    checks = item["field_summary"]["M"]["business_identity"]["M"]["checks"][
        "L"
    ]
    assert checks == [
        {"BOOL": True},
        {"N": "1"},
        {"N": "0.5"},
        {"NULL": True},
        {"M": {"nested": {"BOOL": False}}},
    ]


def test_round_trip_and_converter_preserve_nested_types(
    summary: ReceiptValidationSummary,
):
    item = summary.to_item()

    assert ReceiptValidationSummary.from_item(item) == summary
    assert item_to_receipt_validation_summary(item) == summary


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_serialization_rejects_non_finite_nested_numbers(
    summary_kwargs: dict, value: float
):
    summary_kwargs["metadata"]["processing_metrics"]["score"] = value
    summary = ReceiptValidationSummary(**summary_kwargs)

    with pytest.raises(ValueError, match="numbers must be finite"):
        summary.to_item()


def test_from_item_requires_record_fields(summary: ReceiptValidationSummary):
    item = summary.to_item()
    del item["overall_status"]

    with pytest.raises(ValueError, match="missing required keys"):
        item_to_receipt_validation_summary(item)


def test_from_item_revalidates_serialized_data(
    summary: ReceiptValidationSummary,
):
    item = summary.to_item()
    item["validation_timestamp"] = {"S": "not-a-timestamp"}

    with pytest.raises(ValueError, match="valid ISO format timestamp"):
        ReceiptValidationSummary.from_item(item)


def test_value_semantics(summary: ReceiptValidationSummary):
    assert summary == deepcopy(summary)
    assert summary != "not a summary"
    with pytest.raises(TypeError):
        hash(summary)
    assert "overall_status=valid" in repr(summary)
