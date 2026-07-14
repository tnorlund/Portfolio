"""Unit tests for the QueueJob DynamoDB entity."""

from datetime import datetime
from uuid import uuid4

import pytest

from receipt_dynamo import QueueJob, item_to_queue_job

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
BASE = {
    "queue_name": "training",
    "job_id": JOB_ID,
    "enqueued_at": "2024-01-01T12:00:00",
    "priority": "medium",
    "position": 0,
}


def make_queue_job(**overrides):
    """Build a valid queue assignment with field overrides."""
    return QueueJob(**(BASE | overrides))


@pytest.mark.unit
def test_queue_job_init_and_datetime_normalization():
    queued = make_queue_job(enqueued_at=datetime(2024, 1, 1, 12))
    assert dict(queued) == BASE


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"queue_name": ""}, "queue_name must be a non-empty string"),
        ({"queue_name": 1}, "queue_name must be a non-empty string"),
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "bad"}, "valid UUIDv4"),
        ({"enqueued_at": 1}, "enqueued_at must be a datetime"),
        ({"priority": "bad"}, "priority must be one of"),
        ({"priority": 1}, "priority must be one of"),
        ({"position": -1}, "position must be a non-negative integer"),
        ({"position": 1.0}, "position must be a non-negative integer"),
        ({"position": True}, "position must be a non-negative integer"),
    ],
)
def test_queue_job_rejects_invalid_fields(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_queue_job(**overrides)


@pytest.mark.unit
def test_queue_job_normalizes_priority_and_builds_keys():
    queued = make_queue_job(priority="HIGH", position=4)
    assert queued.priority == "high"
    assert queued.key == {
        "PK": {"S": "QUEUE#training"},
        "SK": {"S": f"JOB#{JOB_ID}"},
    }
    assert queued.gsi1_key() == {
        "GSI1PK": {"S": "JOB"},
        "GSI1SK": {"S": f"JOB#{JOB_ID}#QUEUE#training"},
    }


@pytest.mark.unit
def test_queue_job_item_shape_and_round_trip():
    queued = make_queue_job(priority="high", position=4)
    item = queued.to_item()
    assert item == {
        **queued.key,
        **queued.gsi1_key(),
        "TYPE": {"S": "QUEUE_JOB"},
        "enqueued_at": {"S": BASE["enqueued_at"]},
        "priority": {"S": "high"},
        "position": {"N": "4"},
    }
    assert item_to_queue_job(item) == queued


@pytest.mark.unit
def test_queue_job_value_semantics():
    queued = make_queue_job(position=4)
    duplicate = make_queue_job(position=4)
    different = make_queue_job(job_id=str(uuid4()))

    assert queued == duplicate
    assert hash(queued) == hash(duplicate)
    assert queued != different
    assert queued != "not queued"
    assert dict(queued)["position"] == 4
    rendered = repr(queued)
    assert "QueueJob(" in rendered and JOB_ID in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.pop("position"), "missing keys"),
        (lambda item: item.__setitem__("position", {}), "Error converting"),
        (lambda item: item.__setitem__("priority", {"S": "BAD"}), "one of"),
    ],
)
def test_queue_job_rejects_invalid_items(mutator, match):
    item = make_queue_job().to_item()
    mutator(item)
    with pytest.raises((ValueError, TypeError), match=match):
        item_to_queue_job(item)
