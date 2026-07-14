# pylint: disable=redefined-outer-name
"""Unit tests for the JobMetric entity."""

from datetime import datetime
from typing import Any

import pytest

from receipt_dynamo import JobMetric, item_to_job_metric

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2021-01-01T12:30:45"


def metric_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return valid JobMetric arguments with selected fields replaced."""
    return {
        **{
            "job_id": JOB_ID,
            "metric_name": "loss",
            "timestamp": TIMESTAMP,
            "value": 0.15,
            "unit": "dimensionless",
            "step": 100,
            "epoch": 2,
        },
        **overrides,
    }


def make_metric(**overrides: Any) -> JobMetric:
    """Build a JobMetric with stable defaults."""
    return JobMetric(**metric_kwargs(**overrides))


@pytest.fixture
def example_job_metric() -> JobMetric:
    """Return a fully populated JobMetric."""
    return make_metric()


@pytest.mark.unit
def test_job_metric_init_normalizes_datetime() -> None:
    """Datetime timestamps are normalized to ISO strings."""
    metric = make_metric(timestamp=datetime.fromisoformat(TIMESTAMP))
    assert metric.timestamp == TIMESTAMP


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "not-a-uuid"}, "uuid must be a valid UUID"),
        ({"metric_name": ""}, "metric_name must be a non-empty string"),
        ({"metric_name": 123}, "metric_name must be str, got int"),
        ({"timestamp": 123}, "timestamp must be datetime, str, got int"),
        (
            {"value": "0.15"},
            r"value must be a number \(int/float\) or a dictionary",
        ),
        (
            {"value": True},
            r"value must be a number \(int/float\) or a dictionary",
        ),
        ({"value": float("nan")}, "value must be a finite number"),
        ({"value": float("inf")}, "value must be a finite number"),
        ({"unit": 123}, "unit must be a string"),
        ({"step": -1}, "step must be a non-negative integer"),
        ({"step": 1.5}, "step must be a non-negative integer"),
        ({"step": True}, "step must be a non-negative integer"),
        ({"epoch": -1}, "epoch must be a non-negative integer"),
        ({"epoch": "2"}, "epoch must be a non-negative integer"),
        ({"epoch": False}, "epoch must be a non-negative integer"),
    ],
)
def test_job_metric_rejects_invalid_fields(
    overrides: dict[str, Any], match: str
) -> None:
    """Every typed field rejects values outside its exact boundary."""
    with pytest.raises(ValueError, match=match):
        make_metric(**overrides)


@pytest.mark.unit
def test_job_metric_keys(example_job_metric: JobMetric) -> None:
    """Primary and secondary indexes encode the documented access paths."""
    assert example_job_metric.key == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": f"METRIC#loss#{TIMESTAMP}"},
    }
    assert example_job_metric.gsi1_key() == {
        "GSI1PK": {"S": "METRIC#loss"},
        "GSI1SK": {"S": TIMESTAMP},
    }
    assert example_job_metric.gsi2_key() == {
        "GSI2PK": {"S": "METRIC#loss"},
        "GSI2SK": {"S": f"JOB#{JOB_ID}#{TIMESTAMP}"},
    }


@pytest.mark.unit
def test_job_metric_to_item(example_job_metric: JobMetric) -> None:
    """A populated metric serializes all keys and attributes exactly."""
    assert example_job_metric.to_item() == {
        **example_job_metric.key,
        **example_job_metric.gsi1_key(),
        **example_job_metric.gsi2_key(),
        "TYPE": {"S": "JOB_METRIC"},
        "job_id": {"S": JOB_ID},
        "metric_name": {"S": "loss"},
        "timestamp": {"S": TIMESTAMP},
        "value": {"N": "0.15"},
        "unit": {"S": "dimensionless"},
        "step": {"N": "100"},
        "epoch": {"N": "2"},
    }


@pytest.mark.unit
def test_job_metric_nested_value_round_trip() -> None:
    """Nested Dynamo maps preserve lists, booleans, nulls, and numbers."""
    value = {
        "series": [1, 2.5, True, None, {"label": "validation"}],
        "summary": {"minimum": 0, "converged": False},
    }
    metric = make_metric(value=value, unit=None, step=None, epoch=None)
    item = metric.to_item()

    assert item["value"]["M"]["series"]["L"][2] == {"BOOL": True}
    assert item_to_job_metric(item) == metric
    assert all(field not in item for field in ("unit", "step", "epoch"))


@pytest.mark.unit
def test_job_metric_repr_and_iteration(example_job_metric: JobMetric) -> None:
    """Representation and iteration expose every dataclass field."""
    assert dict(example_job_metric) == metric_kwargs()
    representation = repr(example_job_metric)
    for expected in (
        "JobMetric(",
        f"job_id='{JOB_ID}'",
        "metric_name='loss'",
        f"timestamp='{TIMESTAMP}'",
        "value=0.15",
        "unit='dimensionless'",
        "step=100",
        "epoch=2",
    ):
        assert expected in representation


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "4f52804b-2fad-4e00-92c8-b593da3a8ed3"),
        ("metric_name", "accuracy"),
        ("timestamp", "2021-01-01T13:30:45"),
        ("value", 0.25),
        ("unit", "percent"),
        ("step", 150),
        ("epoch", 3),
    ],
)
def test_job_metric_equality_detects_each_field(
    field: str, value: Any
) -> None:
    """Changing any field changes dataclass equality."""
    baseline = make_metric()
    assert baseline != make_metric(**{field: value})
    assert baseline != object()


@pytest.mark.unit
def test_job_metric_hash_matches_dict_equality() -> None:
    """Equal mappings hash equally regardless of insertion order."""
    first = make_metric(value={"a": 1, "b": 2})
    second = make_metric(value={"b": 2, "a": 1})
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


@pytest.mark.unit
@pytest.mark.parametrize("value", [42, 0.95, {"vector": [0.1, 0.2]}])
def test_job_metric_round_trips_supported_values(value: Any) -> None:
    """Every supported metric value type survives serialization."""
    metric = make_metric(value=value)
    assert item_to_job_metric(metric.to_item()) == metric


@pytest.mark.unit
def test_job_metric_to_item_revalidates_mutated_value() -> None:
    """Serialization rejects an invalid value assigned after construction."""
    metric = make_metric()
    metric.value = "invalid"
    with pytest.raises(ValueError, match="value must be a number"):
        metric.to_item()


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("value"),
        lambda item: item.__setitem__("value", {"S": "0.15"}),
        lambda item: item.__setitem__("step", {"N": "1.5"}),
    ],
)
def test_item_to_job_metric_rejects_malformed_items(mutation: Any) -> None:
    """Missing, unsupported, and non-integral Dynamo values are rejected."""
    item = make_metric().to_item()
    mutation(item)
    with pytest.raises(ValueError):
        item_to_job_metric(item)
