"""Contracts for :class:`ReceiptValidationCategory`."""

from copy import deepcopy

import pytest

from receipt_dynamo import (
    ReceiptValidationCategory,
    item_to_receipt_validation_category,
)

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2023-05-15T10:30:00"


@pytest.fixture(name="category_kwargs")
def _category_kwargs() -> dict:
    return {
        "receipt_id": 1,
        "image_id": IMAGE_ID,
        "field_name": "payment_info",
        "field_category": "creditcard",
        "status": "valid",
        "reasoning": "All payment information is valid.",
        "result_summary": {"total": 3, "valid": 3, "invalid": 0},
        "validation_timestamp": TIMESTAMP,
        "metadata": {
            "source_info": {"model": "validation-v1"},
            "confidence": 0.95,
            "flags": [True, False],
        },
    }


@pytest.fixture(name="category")
def _category(category_kwargs: dict) -> ReceiptValidationCategory:
    return ReceiptValidationCategory(**category_kwargs)


def test_initialization_copies_mutable_inputs(category_kwargs: dict):
    counts = category_kwargs["result_summary"]
    metadata = category_kwargs["metadata"]
    category = ReceiptValidationCategory(**category_kwargs)

    counts["total"] = 99
    metadata["confidence"] = 0.1

    assert category.result_summary["total"] == 3
    assert category.metadata["confidence"] == 0.95


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("receipt_id", True, "receipt_id must be an integer"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("image_id", 1, "uuid must be a string"),
        ("image_id", "invalid", "uuid must be a valid UUID"),
        ("field_name", "", "field_name must not be empty"),
        ("field_category", 1, "field_category must be a string"),
        ("status", "", "status must not be empty"),
        ("reasoning", None, "reasoning must be a string"),
        ("result_summary", [], "result_summary must be a dictionary"),
        ("validation_timestamp", "invalid", "valid ISO format timestamp"),
        ("metadata", [], "metadata must be a dictionary"),
    ],
)
def test_initialization_rejects_invalid_fields(
    category_kwargs: dict, field: str, value: object, message: str
):
    category_kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        ReceiptValidationCategory(**category_kwargs)


@pytest.mark.parametrize(
    ("counts", "message"),
    [
        ({"": 1}, "result_summary key must not be empty"),
        ({"valid": True}, "values must be integers"),
        ({"valid": 1.5}, "values must be integers"),
        ({"valid": -1}, "values must be non-negative"),
    ],
)
def test_result_summary_contract(
    category_kwargs: dict, counts: dict, message: str
):
    category_kwargs["result_summary"] = counts
    with pytest.raises(ValueError, match=message):
        ReceiptValidationCategory(**category_kwargs)


def test_default_timestamp_and_metadata_are_independent(category_kwargs: dict):
    category_kwargs.update(validation_timestamp=None, metadata=None)
    first = ReceiptValidationCategory(**category_kwargs)
    second = ReceiptValidationCategory(**category_kwargs)

    first.metadata["changed"] = True

    assert first.validation_timestamp
    assert second.metadata == {}


def test_keys_and_item_schema(category: ReceiptValidationCategory):
    item = category.to_item()

    assert category.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001#ANALYSIS#VALIDATION#CATEGORY#payment_info"},
    }
    assert category.gsi1_key["GSI1SK"] == {
        "S": f"VALIDATION#{TIMESTAMP}#CATEGORY#payment_info"
    }
    assert category.gsi3_key["GSI3SK"] == {
        "S": f"IMAGE#{IMAGE_ID}#RECEIPT#00001"
    }
    assert item["TYPE"] == {"S": "RECEIPT_VALIDATION_CATEGORY"}
    assert item["metadata"]["M"]["flags"]["L"] == [
        {"BOOL": True},
        {"BOOL": False},
    ]


def test_round_trip_and_converters(category: ReceiptValidationCategory):
    item = category.to_item()

    assert ReceiptValidationCategory.from_item(item) == category
    assert item_to_receipt_validation_category(item) == category


def test_from_item_requires_record_fields(category: ReceiptValidationCategory):
    item = category.to_item()
    del item["status"]

    with pytest.raises(ValueError, match="missing required keys"):
        ReceiptValidationCategory.from_item(item)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"field_category": {"N": "1"}}, "field_category"),
        ({"PK": {"N": "invalid"}}, "PK"),
        (
            {
                "SK": {"S": "ANALYSIS#VALIDATION#CATEGORY#payment_info"},
                "receipt_id": {"S": "1"},
            },
            "receipt_id",
        ),
    ],
)
def test_converter_rejects_malformed_scalar_attributes(
    category: ReceiptValidationCategory, mutation: dict, message: str
):
    item = category.to_item()
    item.update(mutation)

    with pytest.raises(ValueError, match=message):
        item_to_receipt_validation_category(item)


def test_value_semantics(category: ReceiptValidationCategory):
    assert category == deepcopy(category)
    assert category != "not a category"
    with pytest.raises(TypeError):
        hash(category)
    assert "field_name=payment_info" in repr(category)
