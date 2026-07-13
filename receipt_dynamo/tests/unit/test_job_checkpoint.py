# pylint: disable=redefined-outer-name,protected-access
"""Unit tests for JobCheckpoint entity."""

import uuid
from datetime import datetime, timezone

import pytest

from receipt_dynamo.entities.dynamodb_utils import (
    parse_dynamodb_map,
    parse_dynamodb_value,
)
from receipt_dynamo.entities.job_checkpoint import (
    JobCheckpoint,
    item_to_job_checkpoint,
)


def checkpoint_kwargs(**overrides):
    """Return valid JobCheckpoint kwargs with optional overrides."""
    job_id = overrides.pop("job_id", str(uuid.uuid4()))
    timestamp = overrides.pop(
        "timestamp", datetime.now(timezone.utc).isoformat()
    )
    kwargs = {
        "job_id": job_id,
        "timestamp": timestamp,
        "s3_bucket": "my-checkpoint-bucket",
        "s3_key": f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        "size_bytes": 1024000,
        "step": 1000,
        "epoch": 5,
    }
    return {**kwargs, **overrides}


def make_checkpoint(**overrides):
    """Build a JobCheckpoint with sensible defaults."""
    return JobCheckpoint(**checkpoint_kwargs(**overrides))


@pytest.fixture
def example_job_checkpoint():
    """Returns an example JobCheckpoint object with all fields populated."""
    return make_checkpoint(
        metrics={
            "loss": 0.1234,
            "accuracy": 0.9876,
            "detailed": {"val_loss": 0.2345},
        },
        is_best=True,
    )


@pytest.fixture
def example_job_checkpoint_minimal():
    """Returns an example JobCheckpoint object with minimal fields."""
    return make_checkpoint(size_bytes=512000, step=500, epoch=2)


@pytest.mark.unit
def test_job_checkpoint_init_valid(example_job_checkpoint):
    """Test that a JobCheckpoint can be created with valid parameters."""
    assert isinstance(example_job_checkpoint, JobCheckpoint)
    assert dict(example_job_checkpoint) == {
        "job_id": example_job_checkpoint.job_id,
        "timestamp": example_job_checkpoint.timestamp,
        "s3_bucket": "my-checkpoint-bucket",
        "s3_key": example_job_checkpoint.s3_key,
        "size_bytes": 1024000,
        "step": 1000,
        "epoch": 5,
        "model_state": True,
        "optimizer_state": True,
        "metrics": {
            "loss": 0.1234,
            "accuracy": 0.9876,
            "detailed": {"val_loss": 0.2345},
        },
        "is_best": True,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": "invalid-uuid"}, "uuid must be a valid UUID"),
        ({"job_id": None}, "uuid must be a string"),
        ({"job_id": 123}, "uuid must be a string"),
        ({"timestamp": ""}, "timestamp must be a non-empty string"),
        ({"timestamp": None}, "timestamp must be a non-empty string"),
        ({"timestamp": 123}, "timestamp must be a non-empty string"),
        ({"s3_bucket": ""}, "s3_bucket must be a non-empty string"),
        ({"s3_bucket": None}, "s3_bucket must be a non-empty string"),
        ({"s3_bucket": 123}, "s3_bucket must be a non-empty string"),
        ({"s3_key": ""}, "s3_key must be a non-empty string"),
        ({"s3_key": None}, "s3_key must be a non-empty string"),
        ({"s3_key": 123}, "s3_key must be a non-empty string"),
        ({"size_bytes": -1}, "size_bytes must be a non-negative integer"),
        ({"size_bytes": None}, "size_bytes must be a non-negative integer"),
        ({"size_bytes": "1024"}, "size_bytes must be a non-negative integer"),
        ({"step": -1}, "step must be a non-negative integer"),
        ({"step": None}, "step must be a non-negative integer"),
        ({"step": "1000"}, "step must be a non-negative integer"),
        ({"epoch": -1}, "epoch must be a non-negative integer"),
        ({"epoch": None}, "epoch must be a non-negative integer"),
        ({"epoch": "5"}, "epoch must be a non-negative integer"),
        ({"model_state": "True"}, "model_state must be a boolean"),
        ({"model_state": 1}, "model_state must be a boolean"),
        ({"optimizer_state": "True"}, "optimizer_state must be a boolean"),
        ({"optimizer_state": 1}, "optimizer_state must be a boolean"),
        ({"metrics": "not a dict"}, "metrics must be a dictionary"),
        ({"metrics": [1, 2, 3]}, "metrics must be a dictionary"),
        ({"is_best": "True"}, "is_best must be a boolean"),
        ({"is_best": 1}, "is_best must be a boolean"),
    ],
)
def test_job_checkpoint_init_invalid_fields(overrides, match):
    """Test that invalid constructor fields raise expected errors."""
    with pytest.raises(ValueError, match=match):
        make_checkpoint(**overrides)


@pytest.mark.unit
def test_job_checkpoint_keys(example_job_checkpoint):
    """Test key helpers."""
    assert example_job_checkpoint.key == {
        "PK": {"S": f"JOB#{example_job_checkpoint.job_id}"},
        "SK": {"S": f"CHECKPOINT#{example_job_checkpoint.timestamp}"},
    }
    assert example_job_checkpoint.gsi1_key() == {
        "GSI1PK": {"S": "CHECKPOINT"},
        "GSI1SK": {
            "S": f"JOB#{example_job_checkpoint.job_id}#{example_job_checkpoint.timestamp}"
        },
    }


@pytest.mark.unit
def test_job_checkpoint_to_item(
    example_job_checkpoint, example_job_checkpoint_minimal
):
    """Test that a JobCheckpoint generates the correct DynamoDB item."""
    item = example_job_checkpoint.to_item()
    assert item["PK"] == {"S": f"JOB#{example_job_checkpoint.job_id}"}
    assert item["SK"] == {
        "S": f"CHECKPOINT#{example_job_checkpoint.timestamp}"
    }
    assert item["GSI1PK"] == {"S": "CHECKPOINT"}
    assert item["GSI1SK"] == {
        "S": f"JOB#{example_job_checkpoint.job_id}#{example_job_checkpoint.timestamp}"
    }
    for field in ("job_id", "timestamp", "s3_bucket", "s3_key"):
        assert item[field]["S"] == getattr(example_job_checkpoint, field)
    for field in ("size_bytes", "step", "epoch"):
        assert item[field]["N"] == str(getattr(example_job_checkpoint, field))
    assert item["TYPE"] == {"S": "JOB_CHECKPOINT"}
    assert item["model_state"] == {"BOOL": True}
    assert item["optimizer_state"] == {"BOOL": True}
    assert item["is_best"] == {"BOOL": True}
    assert item["metrics"] == {
        "M": {
            "loss": {"N": "0.1234"},
            "accuracy": {"N": "0.9876"},
            "detailed": {"M": {"val_loss": {"N": "0.2345"}}},
        }
    }

    item_minimal = example_job_checkpoint_minimal.to_item()
    assert "metrics" not in item_minimal
    assert item_minimal["is_best"] == {"BOOL": False}
    assert item_minimal["model_state"] == {"BOOL": True}
    assert item_minimal["optimizer_state"] == {"BOOL": True}


@pytest.mark.unit
def test_job_checkpoint_dict_to_dynamodb_map():
    """Test the _dict_to_dynamodb_map method."""
    result = make_checkpoint()._dict_to_dynamodb_map(
        {
            "string": "value",
            "number": 123,
            "float": 123.45,
            "bool": True,
            "null": None,
            "list": [1, "two", True, None, {"nested": "object"}],
            "nested": {
                "inner": "value",
                "innerNum": 456,
                "innerBool": False,
                "innerList": [7, 8, 9],
            },
        }
    )

    assert result["string"] == {"S": "value"}
    assert result["number"] == {"N": "123"}
    assert result["float"] == {"N": "123.45"}
    assert result["bool"] == {"BOOL": True}
    assert result["null"] == {"NULL": True}
    assert result["list"]["L"] == [
        {"N": "1"},
        {"S": "two"},
        {"BOOL": True},
        {"NULL": True},
        {"M": {"nested": {"S": "object"}}},
    ]
    assert result["nested"]["M"]["inner"] == {"S": "value"}
    assert result["nested"]["M"]["innerNum"] == {"N": "456"}
    assert result["nested"]["M"]["innerBool"] == {"BOOL": False}
    assert result["nested"]["M"]["innerList"]["L"][0] == {"N": "7"}


@pytest.mark.unit
def test_job_checkpoint_repr(example_job_checkpoint):
    """Test that a JobCheckpoint generates the correct representation."""
    repr_str = repr(example_job_checkpoint)
    for expected in [
        "JobCheckpoint(",
        f"job_id='{example_job_checkpoint.job_id}'",
        f"timestamp='{example_job_checkpoint.timestamp}'",
        "s3_bucket='my-checkpoint-bucket'",
        f"s3_key='{example_job_checkpoint.s3_key}'",
        "size_bytes=1024000",
        "step=1000",
        "epoch=5",
        "model_state=True",
        "optimizer_state=True",
        "is_best=True",
        "metrics=",
    ]:
        assert expected in repr_str


@pytest.mark.unit
def test_job_checkpoint_iter(example_job_checkpoint):
    """Test that a JobCheckpoint can be iterated over."""
    assert dict(example_job_checkpoint) == {
        "job_id": example_job_checkpoint.job_id,
        "timestamp": example_job_checkpoint.timestamp,
        "s3_bucket": example_job_checkpoint.s3_bucket,
        "s3_key": example_job_checkpoint.s3_key,
        "size_bytes": example_job_checkpoint.size_bytes,
        "step": example_job_checkpoint.step,
        "epoch": example_job_checkpoint.epoch,
        "model_state": example_job_checkpoint.model_state,
        "optimizer_state": example_job_checkpoint.optimizer_state,
        "metrics": example_job_checkpoint.metrics,
        "is_best": example_job_checkpoint.is_best,
    }


@pytest.mark.unit
def test_job_checkpoint_eq():
    """Test that JobCheckpoint equality and hashing work correctly."""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    baseline = make_checkpoint(
        job_id=job_id,
        timestamp=timestamp,
        metrics={"loss": 0.1234},
        is_best=True,
    )
    same = make_checkpoint(
        job_id=job_id,
        timestamp=timestamp,
        metrics={"loss": 0.1234},
        is_best=True,
    )
    different = make_checkpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="different-bucket",
        metrics={"loss": 0.1234},
        is_best=True,
    )

    assert baseline == same
    assert baseline != different
    assert baseline != "not a checkpoint"
    assert len({baseline, same, different}) == 2


@pytest.mark.unit
def test_parse_dynamodb_map():
    """Test the _parse_dynamodb_map function."""
    result = parse_dynamodb_map(
        {
            "string": {"S": "value"},
            "number": {"N": "123"},
            "float": {"N": "123.45"},
            "bool": {"BOOL": True},
            "null": {"NULL": True},
            "map": {"M": {"inner": {"S": "value"}, "innerNum": {"N": "456"}}},
            "list": {"L": [{"S": "item1"}, {"N": "789"}, {"BOOL": False}]},
        }
    )
    assert result == {
        "string": "value",
        "number": 123,
        "float": 123.45,
        "bool": True,
        "null": None,
        "map": {"inner": "value", "innerNum": 456},
        "list": ["item1", 789, False],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"S": "value"}, "value"),
        ({"N": "123"}, 123),
        ({"N": "123.45"}, 123.45),
        ({"BOOL": True}, True),
        ({"NULL": True}, None),
        ({"L": [{"S": "item1"}, {"N": "123"}]}, ["item1", 123]),
        (
            {"M": {"key": {"S": "value"}, "num": {"N": "123"}}},
            {"key": "value", "num": 123},
        ),
        ({}, None),
    ],
)
def test_parse_dynamodb_value(value, expected):
    """Test the parse_dynamodb_value function."""
    assert parse_dynamodb_value(value) == expected


@pytest.mark.unit
def test_item_to_job_checkpoint(
    example_job_checkpoint, example_job_checkpoint_minimal
):
    """Test that a DynamoDB item can be converted to a JobCheckpoint."""
    assert (
        item_to_job_checkpoint(example_job_checkpoint.to_item())
        == example_job_checkpoint
    )

    checkpoint_minimal = item_to_job_checkpoint(
        example_job_checkpoint_minimal.to_item()
    )
    assert checkpoint_minimal.job_id == example_job_checkpoint_minimal.job_id
    assert (
        checkpoint_minimal.timestamp
        == example_job_checkpoint_minimal.timestamp
    )
    assert checkpoint_minimal.metrics == {}
    assert checkpoint_minimal.is_best is False

    with pytest.raises(
        ValueError, match="Error converting item to JobCheckpoint"
    ):
        item_to_job_checkpoint({"job_id": {"S": "invalid-job-id"}})
