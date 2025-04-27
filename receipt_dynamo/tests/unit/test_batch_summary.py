import pytest
from datetime import datetime
from receipt_dynamo.entities.batch_summary import (
    BatchSummary,
    itemToBatchSummary,
)
from receipt_dynamo.constants import BatchStatus, BatchType


@pytest.fixture
def example_batch_summary():
    return BatchSummary(
        batch_id="abc123",
        batch_type=BatchType.EMBEDDING.value,
        openai_batch_id="openai-xyz",
        submitted_at=datetime(2024, 1, 1, 12, 0, 0),
        status=BatchStatus.PENDING.value,
        word_count=10,
        result_file_id="file-456",
        receipt_refs=[
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1001),
            ("9d32fa91-1d2f-4b3a-a88c-9e7ab3aeee4b", 1002),
        ],
    )


# === VALID ===


@pytest.mark.unit
def test_batch_summary_init_valid(example_batch_summary):
    assert example_batch_summary.batch_id == "abc123"
    assert example_batch_summary.batch_type == BatchType.EMBEDDING.value
    assert example_batch_summary.openai_batch_id == "openai-xyz"
    assert example_batch_summary.submitted_at == datetime(2024, 1, 1, 12, 0, 0)
    assert example_batch_summary.status == BatchStatus.PENDING.value
    assert example_batch_summary.word_count == 10
    assert example_batch_summary.result_file_id == "file-456"
    assert example_batch_summary.receipt_refs == [
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1001),
        ("9d32fa91-1d2f-4b3a-a88c-9e7ab3aeee4b", 1002),
    ]


@pytest.mark.unit
def test_batch_summary_to_item_and_back(example_batch_summary):
    item = example_batch_summary.to_item()
    reconstructed = itemToBatchSummary(item)
    assert reconstructed == example_batch_summary


# === INVALID CONSTRUCTION ===


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None])
def test_batch_summary_invalid_batch_id_type(bad_value):
    with pytest.raises(ValueError, match="batch_id must be a string"):
        BatchSummary(
            batch_id=bad_value,
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id="openai-xyz",
            submitted_at=datetime.now(),
            status=BatchStatus.PENDING.value,
            word_count=10,
            result_file_id="file-456",
            receipt_refs=[("img", 1)],
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None])
def test_batch_summary_invalid_openai_batch_id_type(bad_value):
    with pytest.raises(ValueError, match="openai_batch_id must be a string"):
        BatchSummary(
            batch_id="abc",
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id=bad_value,
            submitted_at=datetime.now(),
            status=BatchStatus.PENDING.value,
            word_count=10,
            result_file_id="file-456",
            receipt_refs=[("img", 1)],
        )


@pytest.mark.unit
def test_batch_summary_invalid_submitted_at_type():
    with pytest.raises(
        ValueError, match="submitted_at must be a datetime object"
    ):
        BatchSummary(
            batch_id="abc",
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id="openai-xyz",
            submitted_at="not-a-datetime",
            status=BatchStatus.PENDING.value,
            word_count=10,
            result_file_id="file-456",
            receipt_refs=[("img", 1)],
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", ["ten", None])
def test_batch_summary_invalid_word_count_type(bad_value):
    with pytest.raises(ValueError, match="word_count must be an integer"):
        BatchSummary(
            batch_id="abc",
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id="openai-xyz",
            submitted_at=datetime.now(),
            status=BatchStatus.PENDING.value,
            word_count=bad_value,
            result_file_id="file-456",
            receipt_refs=[("img", 1)],
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None])
def test_batch_summary_invalid_result_file_id_type(bad_value):
    with pytest.raises(ValueError, match="result_file_id must be a string"):
        BatchSummary(
            batch_id="abc",
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id="openai-xyz",
            submitted_at=datetime.now(),
            status=BatchStatus.PENDING.value,
            word_count=10,
            result_file_id=bad_value,
            receipt_refs=[("img", 1)],
        )


@pytest.mark.unit
def test_batch_summary_invalid_batch_type():
    with pytest.raises(ValueError, match="Invalid batch type"):
        BatchSummary(
            batch_id="abc",
            batch_type="NOT_A_TYPE",
            openai_batch_id="id",
            submitted_at=datetime.now(),
            status=BatchStatus.PENDING.value,
            word_count=1,
            result_file_id="r",
            receipt_refs=[("x", 1)],
        )


@pytest.mark.unit
def test_batch_summary_invalid_status():
    with pytest.raises(ValueError, match="Invalid status"):
        BatchSummary(
            batch_id="abc",
            batch_type=BatchType.COMPLETION.value,
            openai_batch_id="id",
            submitted_at=datetime.now(),
            status="UNKNOWN",
            word_count=1,
            result_file_id="r",
            receipt_refs=[("x", 1)],
        )


@pytest.mark.unit
def test_batch_summary_status_not_string():
    with pytest.raises(
        ValueError,
        match="status must be either a BatchStatus enum or a string; got int",
    ):
        BatchSummary(
            batch_id="abc123",
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id="openai-xyz",
            submitted_at=datetime.now(),
            status=123,
            word_count=10,
            result_file_id="file-456",
            receipt_refs=[("img", 1)],
        )


@pytest.mark.unit
def test_batch_summary_invalid_receipt_refs_type():
    with pytest.raises(ValueError, match="receipt_refs must be a list"):
        BatchSummary(
            batch_id="abc",
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id="xyz",
            submitted_at=datetime.now(),
            status=BatchStatus.COMPLETED.value,
            word_count=2,
            result_file_id="f",
            receipt_refs="not-a-list",
        )


# === PARSING FAILURE ===


@pytest.mark.unit
def test_batch_summary_missing_keys():
    with pytest.raises(ValueError, match="missing keys"):
        itemToBatchSummary({})


@pytest.mark.unit
def test_batch_summary_invalid_dynamodb_format():
    item = {
        "PK": {"S": "BATCH#abc123"},
        "SK": {"S": "STATUS"},
        "batch_type": {"S": "EMBEDDING"},
        "openai_batch_id": {"S": "openai-xyz"},
        "submitted_at": {"S": "invalid-timestamp"},
        "status": {"S": "PENDING"},
        "word_count": {"N": "10"},
        "result_file_id": {"S": "file-456"},
        "receipt_refs": {"L": []},
    }
    with pytest.raises(
        ValueError, match="Error converting item to BatchSummary"
    ):
        itemToBatchSummary(item)


# === EQUALITY, HASHING, STR, ITER ===


@pytest.mark.unit
def test_batch_summary_eq_and_hash(example_batch_summary):
    duplicate = itemToBatchSummary(example_batch_summary.to_item())
    assert duplicate == example_batch_summary
    assert hash(duplicate) == hash(example_batch_summary)
    assert example_batch_summary != "not-a-batch"


@pytest.mark.unit
def test_batch_summary_str(example_batch_summary):
    assert str(example_batch_summary) == repr(example_batch_summary)


@pytest.mark.unit
def test_batch_summary_iter(example_batch_summary):
    keys = dict(example_batch_summary)
    assert keys["batch_id"] == example_batch_summary.batch_id
    assert keys["receipt_refs"] == example_batch_summary.receipt_refs
    # Test end to end serialization and deserialization
    example_batch_summary_item = BatchSummary(**dict(example_batch_summary))
