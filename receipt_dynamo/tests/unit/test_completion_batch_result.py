from datetime import datetime

import pytest

from receipt_dynamo.constants import BatchStatus, PassNumber, ValidationStatus
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
    item_to_completion_batch_result,
)


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
        status=BatchStatus.PENDING.value,
        validated_at=datetime(2024, 1, 1, 10, 0, 0),
    )


# === BASIC CONSTRUCTION AND ROUNDTRIP ===


@pytest.mark.unit
def test_completion_batch_result_valid(example_completion_batch_result):
    assert example_completion_batch_result.status == "PENDING"


@pytest.mark.unit
def test_completion_batch_result_roundtrip(example_completion_batch_result):
    item = example_completion_batch_result.to_item()
    restored = item_to_completion_batch_result(item)
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
    clone = item_to_completion_batch_result(
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


# === INVALID CONSTRUCTION (TYPE CHECKS) ===


@pytest.mark.unit
@pytest.mark.parametrize(
    "field, bad_value, err_msg",
    [
        ("receipt_id", "1001", "receipt_id must be int, got str"),
        ("line_id", "4", "line_id must be int, got str"),
        ("word_id", "7", "word_id must be int, got str"),
        ("original_label", 123, "original_label must be str, got int"),
        (
            "gpt_suggested_label",
            123,
            "gpt_suggested_label must be str, NoneType, got int",
        ),
        ("status", 1.5, "status must be str, BatchStatus, got float"),
        (
            "validated_at",
            "not-a-datetime",
            "validated_at must be datetime, got str",
        ),
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
        status=BatchStatus.PENDING.value,
        validated_at=datetime.now(),
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
            status="UNKNOWN",
            validated_at=datetime.now(),
        )


# === PARSE FAILURES ===


@pytest.mark.unit
def test_completion_batch_result_missing_required_keys():
    with pytest.raises(ValueError, match="missing keys"):
        item_to_completion_batch_result({})


@pytest.mark.unit
def test_completion_batch_result_invalid_date_format():
    item = {
        "PK": {"S": "BATCH#batch-id"},
        "SK": {"S": "RESULT#RECEIPT#1001#LINE#4#WORD#7#LABEL#TOTAL"},
        "original_label": {"S": "TOTAL"},
        "gpt_suggested_label": {"S": "TOTAL"},
        "status": {"S": "PENDING"},
        "validated_at": {"S": "not-a-date"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to CompletionBatchResult"
    ):
        item_to_completion_batch_result(item)
