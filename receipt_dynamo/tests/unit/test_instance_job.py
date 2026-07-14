"""Unit tests for the InstanceJob DynamoDB entity."""

from datetime import datetime
from uuid import uuid4

import pytest

from receipt_dynamo import InstanceJob, item_to_instance_job

INSTANCE_ID = "i-0123456789abcdef0"
JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
BASE = {
    "instance_id": INSTANCE_ID,
    "job_id": JOB_ID,
    "assigned_at": "2024-01-01T12:00:00",
    "status": "running",
}


def make_assignment(**overrides):
    """Build a valid instance assignment with field overrides."""
    return InstanceJob(**(BASE | overrides))


@pytest.mark.unit
@pytest.mark.parametrize("utilization", [None, {"cpu": 10, "gpu": 20.5}])
def test_instance_job_init_and_datetime_normalization(utilization):
    assignment = make_assignment(
        assigned_at=datetime(2024, 1, 1, 12),
        resource_utilization=utilization,
    )
    assert dict(assignment) == {
        **BASE,
        "resource_utilization": utilization or {},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"instance_id": ""}, "instance_id must be a non-empty string"),
        ({"instance_id": 1}, "instance_id must be a non-empty string"),
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "bad"}, "valid UUIDv4"),
        ({"assigned_at": 1}, "assigned_at must be a datetime"),
        ({"status": "bad"}, "status must be one of"),
        ({"status": 1}, "status must be one of"),
        (
            {"resource_utilization": "bad"},
            "resource_utilization must be a dictionary",
        ),
    ],
)
def test_instance_job_rejects_invalid_fields(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_assignment(**overrides)


@pytest.mark.unit
def test_instance_job_normalizes_status_and_builds_keys():
    assignment = make_assignment(status="RUNNING")
    assert assignment.status == "running"
    assert assignment.key == {
        "PK": {"S": f"INSTANCE#{INSTANCE_ID}"},
        "SK": {"S": f"JOB#{JOB_ID}"},
    }
    assert assignment.gsi1_key() == {
        "GSI1PK": {"S": "JOB"},
        "GSI1SK": {"S": f"JOB#{JOB_ID}#INSTANCE#{INSTANCE_ID}"},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "utilization", [{}, {"cpu": 10, "nested": {"gpu": 1}}]
)
def test_instance_job_item_shape_and_round_trip(utilization):
    assignment = make_assignment(resource_utilization=utilization)
    item = assignment.to_item()
    assert item["TYPE"] == {"S": "INSTANCE_JOB"}
    assert item["assigned_at"] == {"S": BASE["assigned_at"]}
    assert item["status"] == {"S": "running"}
    if utilization:
        assert item["resource_utilization"]["M"]["nested"] == {
            "M": {"gpu": {"N": "1"}}
        }
    else:
        assert "resource_utilization" not in item
    assert item_to_instance_job(item) == assignment


@pytest.mark.unit
def test_instance_job_value_semantics():
    assignment = make_assignment(resource_utilization={"cpu": 10})
    duplicate = make_assignment(resource_utilization={"cpu": 10})
    different = make_assignment(job_id=str(uuid4()))

    assert assignment == duplicate
    assert hash(assignment) == hash(duplicate)
    assert assignment != different
    assert assignment != "not an assignment"
    assert dict(assignment)["resource_utilization"] == {"cpu": 10}
    rendered = repr(assignment)
    assert "InstanceJob(" in rendered and INSTANCE_ID in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.pop("status"), "missing keys"),
        (lambda item: item.__setitem__("status", {}), "Error converting"),
        (lambda item: item.__setitem__("status", {"S": "BAD"}), "one of"),
    ],
)
def test_instance_job_rejects_invalid_items(mutator, match):
    item = make_assignment().to_item()
    mutator(item)
    with pytest.raises(ValueError, match=match):
        item_to_instance_job(item)
