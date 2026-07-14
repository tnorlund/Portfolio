"""Unit tests for the JobLog DynamoDB entity."""

from datetime import datetime
from uuid import uuid4

import pytest

from receipt_dynamo import JobLog, item_to_job_log

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
BASE = {
    "job_id": JOB_ID,
    "timestamp": "2024-01-01T12:00:00",
    "log_level": "INFO",
    "message": "training started",
}


def make_log(**overrides):
    """Build a valid log entry with field overrides."""
    return JobLog(**(BASE | overrides))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "exception"), [(None, None), ("trainer", "traceback")]
)
def test_job_log_init_and_datetime_normalization(source, exception):
    log = make_log(
        timestamp=datetime(2024, 1, 1, 12),
        source=source,
        exception=exception,
    )
    assert dict(log) == {
        **BASE,
        "source": source,
        "exception": exception,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "bad"}, "valid UUIDv4"),
        ({"timestamp": 1}, "timestamp must be a datetime"),
        ({"log_level": "bad"}, "log_level must be one of"),
        ({"log_level": 1}, "log_level must be one of"),
        ({"message": ""}, "message must be a non-empty string"),
        ({"message": 1}, "message must be a non-empty string"),
        ({"source": 1}, "source must be a string"),
        ({"exception": 1}, "exception must be a string"),
    ],
)
def test_job_log_rejects_invalid_fields(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_log(**overrides)


@pytest.mark.unit
def test_job_log_keys_and_item_shape():
    log = make_log(
        log_level="warning", source="trainer", exception="traceback"
    )
    assert log.log_level == "WARNING"
    assert log.key == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": f"LOG#{BASE['timestamp']}"},
    }
    assert log.gsi1_key() == {
        "GSI1PK": {"S": "LOG"},
        "GSI1SK": {"S": f"JOB#{JOB_ID}#{BASE['timestamp']}"},
    }
    assert log.to_item() == {
        **log.key,
        **log.gsi1_key(),
        "TYPE": {"S": "JOB_LOG"},
        "log_level": {"S": "WARNING"},
        "message": {"S": BASE["message"]},
        "source": {"S": "trainer"},
        "exception": {"S": "traceback"},
    }
    minimal = make_log().to_item()
    assert "source" not in minimal and "exception" not in minimal


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "exception"), [(None, None), ("trainer", "traceback")]
)
def test_job_log_round_trip(source, exception):
    log = make_log(source=source, exception=exception)
    assert item_to_job_log(log.to_item()) == log


@pytest.mark.unit
def test_job_log_value_semantics():
    log = make_log(source="trainer")
    duplicate = make_log(source="trainer")
    different = make_log(job_id=str(uuid4()))

    assert log == duplicate
    assert hash(log) == hash(duplicate)
    assert log != different
    assert log != "not a log"
    assert dict(log)["source"] == "trainer"
    rendered = repr(log)
    assert "JobLog(" in rendered and JOB_ID in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.pop("message"), "missing keys"),
        (lambda item: item.__setitem__("message", {}), "Error converting"),
        (lambda item: item.__setitem__("log_level", {"S": "BAD"}), "one of"),
    ],
)
def test_job_log_rejects_invalid_items(mutator, match):
    item = make_log().to_item()
    mutator(item)
    with pytest.raises(ValueError, match=match):
        item_to_job_log(item)
