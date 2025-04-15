import pytest
from datetime import datetime
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
    itemToCompletionBatchResult,
)
from receipt_dynamo.constants import ValidationStatus


@pytest.fixture
def example_completion_batch_result():
    return CompletionBatchResult(
        batch_id="42bffa3b-1a9e-4d2c-bb6a-08f0b2b5c123",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Yes, this is a valid UUIDv4 - it has 32 hex digits with hyphens in the correct positions (8-4-4-4-12) and version 4 UUID bits
        receipt_id=1001,
        line_id=4,
        word_id=7,
        original_label="TOTAL",
        gpt_suggested_label="TOTAL",
        label_confidence=0.98,
        label_changed=False,
        status=ValidationStatus.VALIDATED.value,
        validated_at=datetime(2024, 1, 1, 10, 0, 0),
        reasoning="GPT agreed with the label.",
        raw_prompt="Prompt example here...",
        raw_response="Response example here...",
        label_target="value",
    )


# === BASIC CONSTRUCTION AND ROUNDTRIP ===


@pytest.mark.unit
def test_completion_batch_result_valid(example_completion_batch_result):
    assert example_completion_batch_result.status == "VALIDATED"
    assert example_completion_batch_result.label_confidence == 0.98


@pytest.mark.unit
def test_completion_batch_result_roundtrip(example_completion_batch_result):
    item = example_completion_batch_result.to_item()
    restored = itemToCompletionBatchResult(item)
    assert restored == example_completion_batch_result


# === REPR, STR, EQ, HASH, ITER ===


@pytest.mark.unit
def test_completion_batch_result_repr(example_completion_batch_result):
    text = repr(example_completion_batch_result)
    assert text.startswith("CompletionBatchResult(")
    assert "original_label='TOTAL'" in text


@pytest.mark.unit
def test_completion_batch_result_str(example_completion_batch_result):
    assert str(example_completion_batch_result) == repr(
        example_completion_batch_result
    )


@pytest.mark.unit
def test_completion_batch_result_eq_and_hash(example_completion_batch_result):
    clone = itemToCompletionBatchResult(
        example_completion_batch_result.to_item()
    )
    assert clone == example_completion_batch_result
    assert hash(clone) == hash(example_completion_batch_result)
    assert example_completion_batch_result != "not-a-batch"


@pytest.mark.unit
def test_completion_batch_result_iter(example_completion_batch_result):
    fields = dict(example_completion_batch_result)
    assert fields["receipt_id"] == 1001
    assert fields["original_label"] == "TOTAL"
    assert fields["raw_prompt"].startswith("Prompt")


# === INVALID CONSTRUCTION (TYPE CHECKS) ===


@pytest.mark.unit
@pytest.mark.parametrize(
    "field, bad_value, err_msg",
    [
        ("receipt_id", "1001", "receipt_id must be an integer"),
        ("line_id", "4", "line_id must be an integer"),
        ("word_id", "7", "word_id must be an integer"),
        ("original_label", 123, "original_label must be a string"),
        ("gpt_suggested_label", 123, "gpt_suggested_label must be a string"),
        ("label_confidence", "0.9", "label_confidence must be a float"),
        ("label_changed", "False", "label_changed must be a boolean"),
        ("status", 1.5, "status must be a string"),
        (
            "validated_at",
            "not-a-datetime",
            "validated_at must be a datetime object",
        ),
        ("reasoning", 123, "reasoning must be a string"),
        ("raw_prompt", 123, "raw_prompt must be a string"),
        ("raw_response", ["array"], "raw_response must be a string"),
        ("label_target", 123, "label_target must be a string"),
    ],
)
def test_completion_batch_result_field_type_errors(field, bad_value, err_msg):
    kwargs = dict(
        batch_id="42bffa3b-1a9e-4d2c-bb6a-08f0b2b5c123",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1001,
        line_id=4,
        word_id=7,
        original_label="TOTAL",
        gpt_suggested_label="TOTAL",
        label_confidence=0.95,
        label_changed=False,
        status=ValidationStatus.VALIDATED.value,
        validated_at=datetime.now(),
        reasoning="Reasonable",
        raw_prompt="Prompt here",
        raw_response="Response here",
        label_target="value",
    )
    kwargs[field] = bad_value
    with pytest.raises(ValueError, match=err_msg):
        CompletionBatchResult(**kwargs)


@pytest.mark.unit
def test_completion_batch_result_invalid_status_value():
    with pytest.raises(ValueError, match="status must be one of"):
        CompletionBatchResult(
            batch_id="42bffa3b-1a9e-4d2c-bb6a-08f0b2b5c123",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1001,
            line_id=4,
            word_id=7,
            original_label="TOTAL",
            gpt_suggested_label="TOTAL",
            label_confidence=0.95,
            label_changed=False,
            status="UNKNOWN",
            validated_at=datetime.now(),
            reasoning="reasoning",
            raw_prompt="prompt",
            raw_response="response",
            label_target="value",
        )


# === PARSE FAILURES ===


@pytest.mark.unit
def test_completion_batch_result_missing_required_keys():
    with pytest.raises(ValueError, match="missing keys"):
        itemToCompletionBatchResult({})


@pytest.mark.unit
def test_completion_batch_result_invalid_date_format():
    item = {
        "PK": {"S": "BATCH#batch-id"},
        "SK": {"S": "RESULT#RECEIPT#1001#LINE#4#WORD#7#LABEL#TOTAL"},
        "original_label": {"S": "TOTAL"},
        "gpt_suggested_label": {"S": "TOTAL"},
        "label_confidence": {"N": "0.9"},
        "label_changed": {"BOOL": True},
        "status": {"S": "VALIDATED"},
        "validated_at": {"S": "not-a-date"},
        "reasoning": {"S": "reasoning"},
        "raw_prompt": {"S": "prompt"},
        "raw_response": {"S": "response"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to CompletionBatchResult"
    ):
        itemToCompletionBatchResult(item)
