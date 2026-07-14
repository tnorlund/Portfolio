"""Contracts for :class:`ReceiptValidationResult`."""

from copy import deepcopy
from datetime import datetime

import pytest

from receipt_dynamo import (
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2023-05-15T10:30:00"


@pytest.fixture(name="result_kwargs")
def _result_kwargs() -> dict:
    return {
        "receipt_id": 1,
        "image_id": IMAGE_ID,
        "field_name": "payment_info",
        "result_index": 0,
        "type": "warning",
        "message": "Card number may be incomplete.",
        "reasoning": "The detected card suffix is short.",
        "field": "card_number",
        "expected_value": "****1234",
        "actual_value": "***1234",
        "validation_timestamp": TIMESTAMP,
        "metadata": {
            "confidence": 0.85,
            "checks": [True, 2, None],
        },
    }


@pytest.fixture(name="result")
def _result(result_kwargs: dict) -> ReceiptValidationResult:
    return ReceiptValidationResult(**result_kwargs)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("receipt_id", True, "receipt_id must be an integer"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("image_id", 1, "uuid must be a string"),
        ("image_id", "invalid", "uuid must be a valid UUID"),
        ("field_name", "", "field_name must not be empty"),
        ("result_index", True, "result_index must be an integer"),
        ("result_index", -1, "result_index must be non-negative"),
        ("type", "", "type must not be empty"),
        ("message", 1, "message must be a string"),
        ("reasoning", "", "reasoning must not be empty"),
        ("field", 1, "field must be a string or None"),
        ("expected_value", 1, "expected_value must be a string or None"),
        ("actual_value", 1, "actual_value must be a string or None"),
        ("validation_timestamp", "invalid", "valid ISO format timestamp"),
        ("metadata", [], "metadata must be a dictionary"),
    ],
)
def test_initialization_rejects_invalid_fields(
    result_kwargs: dict, field: str, value: object, message: str
):
    result_kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        ReceiptValidationResult(**result_kwargs)


def test_datetime_timestamp_and_metadata_copy(result_kwargs: dict):
    metadata = result_kwargs["metadata"]
    result_kwargs["validation_timestamp"] = datetime.fromisoformat(TIMESTAMP)
    result = ReceiptValidationResult(**result_kwargs)

    metadata["confidence"] = 0.1

    assert result.validation_timestamp == TIMESTAMP
    assert result.metadata["confidence"] == 0.85


def test_minimal_optional_fields_are_omitted(result_kwargs: dict):
    for field in (
        "field",
        "expected_value",
        "actual_value",
        "validation_timestamp",
        "metadata",
    ):
        result_kwargs[field] = None
    result = ReceiptValidationResult(**result_kwargs)
    item = result.to_item()

    assert result.validation_timestamp is not None
    assert item["validation_timestamp"] == {"S": result.validation_timestamp}
    assert item["GSI1SK"] == {
        "S": (
            f"VALIDATION#{result.validation_timestamp}#"
            "CATEGORY#payment_info#RESULT"
        )
    }
    assert "None" not in item["GSI1SK"]["S"]
    assert item["metadata"] == {"M": {}}
    assert "field" not in item
    assert "expected_value" not in item
    assert "actual_value" not in item


def test_keys_and_item_schema(result: ReceiptValidationResult):
    item = result.to_item()

    assert result.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {
            "S": (
                "RECEIPT#00001#ANALYSIS#VALIDATION#CATEGORY#payment_info#"
                "RESULT#0"
            )
        },
    }
    assert result.gsi1_key["GSI1SK"] == {
        "S": f"VALIDATION#{TIMESTAMP}#CATEGORY#payment_info#RESULT"
    }
    assert result.gsi3_key["GSI3PK"] == {"S": "RESULT_TYPE#warning"}
    assert item["TYPE"] == {"S": "RECEIPT_VALIDATION_RESULT"}
    assert item["metadata"]["M"]["checks"]["L"] == [
        {"BOOL": True},
        {"N": "2"},
        {"NULL": True},
    ]


def test_round_trip_and_converter(result: ReceiptValidationResult):
    item = result.to_item()

    assert ReceiptValidationResult.from_item(item) == result
    assert item_to_receipt_validation_result(item) == result


def test_from_item_requires_record_fields(result: ReceiptValidationResult):
    item = result.to_item()
    del item["message"]

    with pytest.raises(ValueError, match="missing required keys"):
        ReceiptValidationResult.from_item(item)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_serialization_rejects_non_finite_metadata(
    result_kwargs: dict, value: float
):
    result_kwargs["metadata"] = {"score": value}
    result = ReceiptValidationResult(**result_kwargs)

    with pytest.raises(ValueError, match="numbers must be finite"):
        result.to_item()


def test_value_semantics(result: ReceiptValidationResult):
    assert result == deepcopy(result)
    assert result != "not a result"
    with pytest.raises(TypeError):
        hash(result)
    assert "result_index=0" in repr(result)
