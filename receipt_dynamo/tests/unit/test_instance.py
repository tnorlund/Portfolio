"""Unit tests for the Instance DynamoDB entity."""

from datetime import datetime

import pytest

from receipt_dynamo import Instance, item_to_instance

BASE = {
    "instance_id": "i-0123456789abcdef0",
    "instance_type": "g5.xlarge",
    "gpu_count": 1,
    "status": "running",
    "launched_at": "2024-01-01T12:00:00",
    "ip_address": "10.0.0.1",
    "availability_zone": "us-east-1a",
    "is_spot": True,
    "health_status": "healthy",
}


def make_instance(**overrides):
    """Build a valid instance with field overrides."""
    return Instance(**(BASE | overrides))


@pytest.mark.unit
def test_instance_init_and_datetime_normalization():
    instance = make_instance(launched_at=datetime(2024, 1, 1, 12))
    assert dict(instance) == BASE


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"instance_id": ""}, "instance_id must be a non-empty string"),
        ({"instance_id": 1}, "instance_id must be a non-empty string"),
        ({"instance_type": ""}, "instance_type must be a non-empty string"),
        ({"instance_type": 1}, "instance_type must be a non-empty string"),
        ({"gpu_count": -1}, "gpu_count must be a non-negative integer"),
        ({"gpu_count": 1.0}, "gpu_count must be a non-negative integer"),
        ({"gpu_count": True}, "gpu_count must be a non-negative integer"),
        ({"status": "bad"}, "status must be one of"),
        ({"status": 1}, "status must be one of"),
        ({"launched_at": 1}, "launched_at must be a datetime"),
        ({"ip_address": 1}, "ip_address must be a string"),
        (
            {"availability_zone": ""},
            "availability_zone must be a non-empty string",
        ),
        (
            {"availability_zone": 1},
            "availability_zone must be a non-empty string",
        ),
        ({"is_spot": 1}, "is_spot must be a boolean"),
        ({"health_status": "bad"}, "health_status must be one of"),
        ({"health_status": 1}, "health_status must be one of"),
    ],
)
def test_instance_rejects_invalid_fields(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_instance(**overrides)


@pytest.mark.unit
def test_instance_normalizes_enums_and_builds_keys():
    instance = make_instance(status="RUNNING", health_status="HEALTHY")
    assert instance.status == "running"
    assert instance.health_status == "healthy"
    assert instance.key == {
        "PK": {"S": f"INSTANCE#{BASE['instance_id']}"},
        "SK": {"S": "INSTANCE"},
    }
    assert instance.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#running"},
        "GSI1SK": {"S": f"INSTANCE#{BASE['instance_id']}"},
    }


@pytest.mark.unit
def test_instance_item_shape_and_round_trip():
    instance = make_instance()
    item = instance.to_item()
    assert item == {
        **instance.key,
        **instance.gsi1_key(),
        "TYPE": {"S": "INSTANCE"},
        "instance_type": {"S": BASE["instance_type"]},
        "gpu_count": {"N": "1"},
        "status": {"S": "running"},
        "launched_at": {"S": BASE["launched_at"]},
        "ip_address": {"S": BASE["ip_address"]},
        "availability_zone": {"S": BASE["availability_zone"]},
        "is_spot": {"BOOL": True},
        "health_status": {"S": "healthy"},
    }
    assert item_to_instance(item) == instance


@pytest.mark.unit
def test_instance_value_semantics():
    instance = make_instance()
    duplicate = make_instance()
    different = make_instance(instance_id="i-other")

    assert instance == duplicate
    assert hash(instance) == hash(duplicate)
    assert instance != different
    assert instance != "not an instance"
    assert dict(instance) == BASE
    rendered = repr(instance)
    assert "Instance(" in rendered and BASE["instance_id"] in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.pop("gpu_count"), "missing keys"),
        (lambda item: item.__setitem__("gpu_count", {}), "Error converting"),
        (lambda item: item.__setitem__("status", {"S": "BAD"}), "one of"),
    ],
)
def test_instance_rejects_invalid_items(mutator, match):
    item = make_instance().to_item()
    mutator(item)
    with pytest.raises(ValueError, match=match):
        item_to_instance(item)
