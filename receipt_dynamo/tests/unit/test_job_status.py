# pylint: disable=redefined-outer-name
"""Unit tests for the JobStatus entity."""

from datetime import datetime
from typing import Any

import pytest

from receipt_dynamo import JobStatus, item_to_job_status
from receipt_dynamo.constants import JobStatus as JobStatusEnum

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
UPDATED_AT = "2021-01-01T00:00:00"


def status_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return valid JobStatus arguments with selected fields replaced."""
    return {
        **{
            "job_id": JOB_ID,
            "status": "running",
            "updated_at": UPDATED_AT,
            "progress": 75.5,
            "message": "Training in progress",
            "updated_by": "user123",
            "instance_id": "i-abc123def456",
        },
        **overrides,
    }


def make_status(**overrides: Any) -> JobStatus:
    """Build a JobStatus with stable defaults."""
    return JobStatus(**status_kwargs(**overrides))


@pytest.fixture
def example_job_status() -> JobStatus:
    """Return a fully populated JobStatus."""
    return make_status()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime.fromisoformat(UPDATED_AT), UPDATED_AT),
        (JobStatusEnum.RUNNING, "running"),
    ],
)
def test_job_status_normalizes_supported_inputs(
    value: Any, expected: str
) -> None:
    """Datetime and enum inputs are normalized to stored strings."""
    overrides = (
        {"updated_at": value}
        if isinstance(value, datetime)
        else {"status": value}
    )
    status = make_status(**overrides)
    field = "updated_at" if isinstance(value, datetime) else "status"
    assert getattr(status, field) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "not-a-uuid"}, "uuid must be a valid UUID"),
        ({"status": "invalid"}, "JobStatus must be one of"),
        ({"status": 123}, "JobStatus must be a str or JobStatus"),
        (
            {"updated_at": 123},
            "updated_at must be a datetime object or a string",
        ),
        ({"progress": -0.1}, "progress must be a number between 0 and 100"),
        ({"progress": 100.1}, "progress must be a number between 0 and 100"),
        (
            {"progress": 10**1000},
            "progress must be a number between 0 and 100",
        ),
        ({"progress": "50"}, "progress must be a number between 0 and 100"),
        ({"progress": True}, "progress must be a number between 0 and 100"),
        (
            {"progress": float("nan")},
            "progress must be a number between 0 and 100",
        ),
        (
            {"progress": float("inf")},
            "progress must be a number between 0 and 100",
        ),
        ({"message": 123}, "message must be a string"),
        ({"updated_by": 123}, "updated_by must be a string"),
        ({"instance_id": 123}, "instance_id must be a string"),
    ],
)
def test_job_status_rejects_invalid_fields(
    overrides: dict[str, Any], match: str
) -> None:
    """Every typed field rejects values outside its exact boundary."""
    with pytest.raises(ValueError, match=match):
        make_status(**overrides)


@pytest.mark.unit
def test_job_status_keys(example_job_status: JobStatus) -> None:
    """Primary and secondary indexes encode job, state, and update time."""
    assert example_job_status.key == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": f"STATUS#{UPDATED_AT}"},
    }
    assert example_job_status.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#running"},
        "GSI1SK": {"S": f"UPDATED#{UPDATED_AT}"},
    }


@pytest.mark.unit
def test_job_status_to_item(example_job_status: JobStatus) -> None:
    """A populated status serializes all keys and attributes exactly."""
    assert example_job_status.to_item() == {
        **example_job_status.key,
        **example_job_status.gsi1_key(),
        "TYPE": {"S": "JOB_STATUS"},
        "status": {"S": "running"},
        "updated_at": {"S": UPDATED_AT},
        "progress": {"N": "75.5"},
        "message": {"S": "Training in progress"},
        "updated_by": {"S": "user123"},
        "instance_id": {"S": "i-abc123def456"},
    }


@pytest.mark.unit
def test_job_status_empty_strings_round_trip() -> None:
    """Present empty optional strings are not confused with missing fields."""
    status = make_status(message="", updated_by="", instance_id="")
    item = status.to_item()
    assert item["message"] == {"S": ""}
    assert item["updated_by"] == {"S": ""}
    assert item["instance_id"] == {"S": ""}
    assert item_to_job_status(item) == status


@pytest.mark.unit
def test_job_status_minimal_round_trip() -> None:
    """Optional fields remain absent through a Dynamo round trip."""
    status = make_status(
        progress=None, message=None, updated_by=None, instance_id=None
    )
    item = status.to_item()
    assert all(
        field not in item
        for field in ("progress", "message", "updated_by", "instance_id")
    )
    assert item_to_job_status(item) == status


@pytest.mark.unit
def test_job_status_repr_and_iteration(example_job_status: JobStatus) -> None:
    """Representation and iteration expose every dataclass field."""
    assert dict(example_job_status) == status_kwargs()
    representation = repr(example_job_status)
    for expected in (
        "JobStatus(",
        f"job_id='{JOB_ID}'",
        "status='running'",
        f"updated_at='{UPDATED_AT}'",
        "progress=75.5",
        "message='Training in progress'",
        "updated_by='user123'",
        "instance_id='i-abc123def456'",
    ):
        assert expected in representation


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "4f52804b-2fad-4e00-92c8-b593da3a8ed3"),
        ("status", "failed"),
        ("updated_at", "2021-01-02T00:00:00"),
        ("progress", 50.0),
        ("message", "Different"),
        ("updated_by", "other-user"),
        ("instance_id", "i-different"),
    ],
)
def test_job_status_equality_and_hash_detect_each_field(
    field: str, value: Any
) -> None:
    """Changing any field changes equality and the hashable value set."""
    baseline = make_status()
    same = make_status()
    different = make_status(**{field: value})
    assert baseline == same
    assert hash(baseline) == hash(same)
    assert baseline != different
    assert baseline != object()


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("status"),
        lambda item: item.__setitem__("status", {"N": "1"}),
        lambda item: item.__setitem__("progress", {"N": "invalid"}),
    ],
)
def test_item_to_job_status_rejects_malformed_items(mutation: Any) -> None:
    """Missing and incorrectly encoded Dynamo attributes are rejected."""
    item = make_status().to_item()
    mutation(item)
    with pytest.raises(ValueError):
        item_to_job_status(item)
