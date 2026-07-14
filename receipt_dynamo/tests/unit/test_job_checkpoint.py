# pylint: disable=protected-access,redefined-outer-name
"""Unit tests for the JobCheckpoint entity."""

from typing import Any

import pytest

from receipt_dynamo.entities.job_checkpoint import (
    JobCheckpoint,
    item_to_job_checkpoint,
)

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2021-01-01T12:30:45+00:00"


def checkpoint_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return valid JobCheckpoint arguments with selected fields replaced."""
    return {
        **{
            "job_id": JOB_ID,
            "timestamp": TIMESTAMP,
            "s3_bucket": "my-checkpoint-bucket",
            "s3_key": f"jobs/{JOB_ID}/checkpoints/model.pt",
            "size_bytes": 1024000,
            "step": 1000,
            "epoch": 5,
            "model_state": True,
            "optimizer_state": True,
            "metrics": {"loss": 0.1234, "accuracy": 0.9876},
            "is_best": True,
        },
        **overrides,
    }


def make_checkpoint(**overrides: Any) -> JobCheckpoint:
    """Build a JobCheckpoint with stable defaults."""
    return JobCheckpoint(**checkpoint_kwargs(**overrides))


@pytest.fixture
def example_job_checkpoint() -> JobCheckpoint:
    """Return a fully populated JobCheckpoint."""
    return make_checkpoint()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": "invalid-uuid"}, "uuid must be a valid UUID"),
        ({"job_id": None}, "uuid must be a string"),
        ({"timestamp": ""}, "timestamp must be a non-empty string"),
        ({"timestamp": 123}, "timestamp must be a non-empty string"),
        ({"s3_bucket": ""}, "s3_bucket must be a non-empty string"),
        ({"s3_bucket": None}, "s3_bucket must be a non-empty string"),
        ({"s3_key": ""}, "s3_key must be a non-empty string"),
        ({"s3_key": 123}, "s3_key must be a non-empty string"),
        ({"size_bytes": -1}, "size_bytes must be a non-negative integer"),
        ({"size_bytes": "1"}, "size_bytes must be a non-negative integer"),
        ({"size_bytes": True}, "size_bytes must be a non-negative integer"),
        ({"step": -1}, "step must be a non-negative integer"),
        ({"step": 1.5}, "step must be a non-negative integer"),
        ({"step": False}, "step must be a non-negative integer"),
        ({"epoch": -1}, "epoch must be a non-negative integer"),
        ({"epoch": None}, "epoch must be a non-negative integer"),
        ({"epoch": True}, "epoch must be a non-negative integer"),
        ({"model_state": 1}, "model_state must be a boolean"),
        ({"optimizer_state": "True"}, "optimizer_state must be a boolean"),
        ({"metrics": []}, "metrics must be a dictionary"),
        ({"is_best": 1}, "is_best must be a boolean"),
    ],
)
def test_job_checkpoint_rejects_invalid_fields(
    overrides: dict[str, Any], match: str
) -> None:
    """Every typed field rejects values outside its exact boundary."""
    with pytest.raises(ValueError, match=match):
        make_checkpoint(**overrides)


@pytest.mark.unit
def test_job_checkpoint_default_metrics_are_not_shared() -> None:
    """Each checkpoint receives its own mutable default metrics map."""
    first = make_checkpoint(metrics=None)
    second = make_checkpoint(metrics=None)
    first.metrics["loss"] = 0.5
    assert second.metrics == {}


@pytest.mark.unit
def test_job_checkpoint_keys(example_job_checkpoint: JobCheckpoint) -> None:
    """Primary and secondary indexes encode job and checkpoint time."""
    assert example_job_checkpoint.key == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": f"CHECKPOINT#{TIMESTAMP}"},
    }
    assert example_job_checkpoint.gsi1_key() == {
        "GSI1PK": {"S": "CHECKPOINT"},
        "GSI1SK": {"S": f"JOB#{JOB_ID}#{TIMESTAMP}"},
    }


@pytest.mark.unit
def test_job_checkpoint_to_item(
    example_job_checkpoint: JobCheckpoint,
) -> None:
    """A populated checkpoint serializes all keys and attributes exactly."""
    assert example_job_checkpoint.to_item() == {
        **example_job_checkpoint.key,
        **example_job_checkpoint.gsi1_key(),
        "TYPE": {"S": "JOB_CHECKPOINT"},
        "job_id": {"S": JOB_ID},
        "timestamp": {"S": TIMESTAMP},
        "s3_bucket": {"S": "my-checkpoint-bucket"},
        "s3_key": {"S": f"jobs/{JOB_ID}/checkpoints/model.pt"},
        "size_bytes": {"N": "1024000"},
        "step": {"N": "1000"},
        "epoch": {"N": "5"},
        "model_state": {"BOOL": True},
        "optimizer_state": {"BOOL": True},
        "is_best": {"BOOL": True},
        "metrics": {
            "M": {"loss": {"N": "0.1234"}, "accuracy": {"N": "0.9876"}}
        },
    }


@pytest.mark.unit
def test_job_checkpoint_nested_metrics_round_trip() -> None:
    """Nested Dynamo maps preserve lists, booleans, nulls, and numbers."""
    metrics = {
        "validation": {"loss": 0.2, "converged": True},
        "history": [1, 0.5, None, {"accepted": False}],
    }
    checkpoint = make_checkpoint(metrics=metrics)
    item = checkpoint.to_item()
    assert item["metrics"]["M"]["history"]["L"][3] == {
        "M": {"accepted": {"BOOL": False}}
    }
    assert item_to_job_checkpoint(item) == checkpoint


@pytest.mark.unit
def test_job_checkpoint_minimal_round_trip() -> None:
    """An empty metrics map remains empty and is omitted from Dynamo."""
    checkpoint = make_checkpoint(metrics=None, is_best=False)
    item = checkpoint.to_item()
    assert "metrics" not in item
    assert item_to_job_checkpoint(item) == checkpoint


@pytest.mark.unit
def test_job_checkpoint_repr_and_iteration(
    example_job_checkpoint: JobCheckpoint,
) -> None:
    """Representation and iteration expose every dataclass field."""
    assert dict(example_job_checkpoint) == checkpoint_kwargs()
    representation = repr(example_job_checkpoint)
    for expected in (
        "JobCheckpoint(",
        f"job_id='{JOB_ID}'",
        f"timestamp='{TIMESTAMP}'",
        "s3_bucket='my-checkpoint-bucket'",
        "size_bytes=1024000",
        "step=1000",
        "epoch=5",
        "model_state=True",
        "optimizer_state=True",
        "is_best=True",
        "metrics=",
    ):
        assert expected in representation


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "4f52804b-2fad-4e00-92c8-b593da3a8ed3"),
        ("timestamp", "2021-01-02T12:30:45+00:00"),
        ("s3_bucket", "different-bucket"),
        ("s3_key", "different/model.pt"),
        ("size_bytes", 2),
        ("step", 2),
        ("epoch", 2),
        ("model_state", False),
        ("optimizer_state", False),
        ("metrics", {"loss": 0.5}),
        ("is_best", False),
    ],
)
def test_job_checkpoint_equality_detects_each_field(
    field: str, value: Any
) -> None:
    """Changing any field changes dataclass equality."""
    baseline = make_checkpoint()
    same = make_checkpoint()
    assert baseline == same
    assert hash(baseline) == hash(same)
    assert baseline != make_checkpoint(**{field: value})
    assert baseline != object()


@pytest.mark.unit
def test_checkpoint_compatibility_map_helper() -> None:
    """The legacy map helper delegates to the shared nested serializer."""
    assert make_checkpoint()._dict_to_dynamodb_map(
        {"values": [1, "two", True, None, {"nested": 2.5}]}
    ) == {
        "values": {
            "L": [
                {"N": "1"},
                {"S": "two"},
                {"BOOL": True},
                {"NULL": True},
                {"M": {"nested": {"N": "2.5"}}},
            ]
        }
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("size_bytes"),
        lambda item: item.__setitem__("size_bytes", {"N": "1.5"}),
        lambda item: item.__setitem__("is_best", {"BOOL": 1}),
        lambda item: item.__setitem__("metrics", {"S": "invalid"}),
    ],
)
def test_item_to_job_checkpoint_rejects_malformed_items(
    mutation: Any,
) -> None:
    """Missing and incorrectly encoded Dynamo attributes are rejected."""
    item = make_checkpoint().to_item()
    mutation(item)
    with pytest.raises(ValueError):
        item_to_job_checkpoint(item)
