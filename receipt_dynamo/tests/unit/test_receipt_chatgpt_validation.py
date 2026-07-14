"""Contracts for :class:`ReceiptChatGPTValidation`."""

from copy import deepcopy

import pytest

from receipt_dynamo import (
    ReceiptChatGPTValidation,
    item_to_receipt_chat_gpt_validation,
)

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2023-05-15T10:30:00"


@pytest.fixture(name="validation_kwargs")
def _validation_kwargs() -> dict:
    return {
        "receipt_id": 1,
        "image_id": IMAGE_ID,
        "original_status": "suspect",
        "revised_status": "valid",
        "reasoning": "The corrected values are internally consistent.",
        "corrections": [
            {
                "field": "total",
                "original": "15.90",
                "corrected": "15.99",
                "accepted": True,
                "alternatives": [15.9, None],
            }
        ],
        "prompt": "Review this validation.",
        "response": "The validation is correct.",
        "timestamp": TIMESTAMP,
        "metadata": {
            "source_info": {"model": "gpt-4"},
            "confidence": 0.92,
            "attempts": [1, 2],
        },
    }


@pytest.fixture(name="validation")
def _validation(validation_kwargs: dict) -> ReceiptChatGPTValidation:
    return ReceiptChatGPTValidation(**validation_kwargs)


def test_initialization_copies_mutable_inputs(validation_kwargs: dict):
    corrections = validation_kwargs["corrections"]
    metadata = validation_kwargs["metadata"]
    validation = ReceiptChatGPTValidation(**validation_kwargs)

    corrections[0]["field"] = "mutated"
    metadata["confidence"] = 0.1

    assert validation.corrections[0]["field"] == "total"
    assert validation.metadata["confidence"] == 0.92


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("receipt_id", True, "receipt_id must be an integer"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("image_id", 123, "uuid must be a string"),
        ("image_id", "not-a-uuid", "uuid must be a valid UUID"),
        ("original_status", "", "original_status must not be empty"),
        ("revised_status", 1, "revised_status must be a string"),
        ("reasoning", None, "reasoning must be a string"),
        ("corrections", {}, "corrections must be a list"),
        ("corrections", ["invalid"], "must contain dictionaries"),
        ("prompt", "", "prompt must not be empty"),
        ("response", 1, "response must be a string"),
        ("timestamp", "yesterday", "valid ISO format timestamp"),
        ("metadata", [], "metadata must be a dictionary"),
    ],
)
def test_initialization_rejects_invalid_fields(
    validation_kwargs: dict, field: str, value: object, message: str
):
    validation_kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        ReceiptChatGPTValidation(**validation_kwargs)


def test_default_timestamp_and_metadata_are_independent(
    validation_kwargs: dict,
):
    validation_kwargs.update(timestamp=None, metadata=None)
    first = ReceiptChatGPTValidation(**validation_kwargs)
    second = ReceiptChatGPTValidation(**validation_kwargs)

    first.metadata["changed"] = True

    assert first.timestamp
    assert second.metadata == {}


def test_keys_and_item_schema(validation: ReceiptChatGPTValidation):
    item = validation.to_item()

    assert validation.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": f"RECEIPT#1#ANALYSIS#VALIDATION#CHATGPT#{TIMESTAMP}"},
    }
    assert validation.gsi1_key == {
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI1SK": {"S": f"VALIDATION_CHATGPT#{TIMESTAMP}"},
    }
    assert validation.gsi3_key == {
        "GSI3PK": {"S": "VALIDATION_STATUS#valid"},
        "GSI3SK": {"S": f"CHATGPT#{TIMESTAMP}"},
    }
    assert item["TYPE"] == {"S": "RECEIPT_CHATGPT_VALIDATION"}
    assert item["corrections"]["L"][0]["M"]["accepted"] == {"BOOL": True}
    assert item["metadata"]["M"]["confidence"] == {"N": "0.92"}


def test_round_trip_and_converter_preserve_nested_types(
    validation: ReceiptChatGPTValidation,
):
    item = validation.to_item()

    assert ReceiptChatGPTValidation.from_item(item) == validation
    assert item_to_receipt_chat_gpt_validation(item) == validation


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_serialization_rejects_non_finite_nested_numbers(
    validation_kwargs: dict, value: float
):
    validation_kwargs["metadata"] = {"score": value}
    validation = ReceiptChatGPTValidation(**validation_kwargs)

    with pytest.raises(ValueError, match="numbers must be finite"):
        validation.to_item()


def test_from_item_requires_record_fields(
    validation: ReceiptChatGPTValidation,
):
    item = validation.to_item()
    del item["prompt"]

    with pytest.raises(ValueError, match="missing required keys"):
        ReceiptChatGPTValidation.from_item(item)


def test_value_semantics(validation: ReceiptChatGPTValidation):
    assert validation == deepcopy(validation)
    assert validation != "not a validation"
    with pytest.raises(TypeError):
        hash(validation)
    assert "original_status=suspect" in repr(validation)
