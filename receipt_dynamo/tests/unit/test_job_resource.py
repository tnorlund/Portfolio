# pylint: disable=redefined-outer-name
"""Unit tests for the JobResource entity."""

from datetime import datetime
from typing import Any

import pytest

from receipt_dynamo import JobResource, item_to_job_resource

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
RESOURCE_ID = "5e63804c-3abd-4f11-83d9-c694eb3b9de4"
ALLOCATED_AT = "2021-01-01T00:00:00"
RELEASED_AT = "2021-01-02T00:00:00"


def resource_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return valid JobResource arguments with selected fields replaced."""
    return {
        **{
            "job_id": JOB_ID,
            "resource_id": RESOURCE_ID,
            "instance_id": "i-0123456789abcdef0",
            "instance_type": "p3.2xlarge",
            "resource_type": "gpu",
            "allocated_at": ALLOCATED_AT,
            "status": "allocated",
            "gpu_count": 8,
            "released_at": RELEASED_AT,
            "resource_config": {"memory_mb": 61440, "vcpus": 8},
        },
        **overrides,
    }


def make_resource(**overrides: Any) -> JobResource:
    """Build a JobResource with stable defaults."""
    return JobResource(**resource_kwargs(**overrides))


@pytest.fixture
def example_job_resource() -> JobResource:
    """Return a fully populated JobResource."""
    return make_resource()


@pytest.mark.unit
def test_job_resource_normalizes_inputs() -> None:
    """Datetime and case-insensitive enum-like fields are normalized."""
    resource = make_resource(
        resource_type="GPU",
        status="ALLOCATED",
        allocated_at=datetime.fromisoformat(ALLOCATED_AT),
        released_at=datetime.fromisoformat(RELEASED_AT),
    )
    assert resource.resource_type == "gpu"
    assert resource.status == "allocated"
    assert resource.allocated_at == ALLOCATED_AT
    assert resource.released_at == RELEASED_AT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "invalid"}, "uuid must be a valid UUID"),
        ({"resource_id": None}, "uuid must be a string"),
        ({"resource_id": "invalid"}, "uuid must be a valid UUID"),
        ({"instance_id": ""}, "instance_id must be a non-empty string"),
        ({"instance_id": 1}, "instance_id must be a non-empty string"),
        ({"instance_type": ""}, "instance_type must be a non-empty string"),
        ({"instance_type": 1}, "instance_type must be a non-empty string"),
        ({"resource_type": "invalid"}, "resource_type must be one of"),
        ({"resource_type": 1}, "resource_type must be one of"),
        (
            {"allocated_at": 1},
            "allocated_at must be a datetime object or a string",
        ),
        (
            {"released_at": 1},
            "released_at must be a datetime object or a string",
        ),
        ({"status": "invalid"}, "status must be one of"),
        ({"status": 1}, "status must be one of"),
        ({"gpu_count": -1}, "gpu_count must be a non-negative integer"),
        ({"gpu_count": 1.5}, "gpu_count must be a non-negative integer"),
        ({"gpu_count": True}, "gpu_count must be a non-negative integer"),
        ({"resource_config": []}, "resource_config must be a dictionary"),
    ],
)
def test_job_resource_rejects_invalid_fields(
    overrides: dict[str, Any], match: str
) -> None:
    """Every typed field rejects values outside its exact boundary."""
    with pytest.raises(ValueError, match=match):
        make_resource(**overrides)


@pytest.mark.unit
def test_job_resource_default_config_is_not_shared() -> None:
    """Each resource receives its own mutable default configuration."""
    first = make_resource(resource_config=None)
    second = make_resource(resource_config=None)
    first.resource_config["gpu_memory"] = 24
    assert second.resource_config == {}


@pytest.mark.unit
def test_job_resource_keys(example_job_resource: JobResource) -> None:
    """Primary and secondary indexes encode both job and resource IDs."""
    assert example_job_resource.key == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": f"RESOURCE#{RESOURCE_ID}"},
    }
    assert example_job_resource.gsi1_key() == {
        "GSI1PK": {"S": "RESOURCE"},
        "GSI1SK": {"S": f"RESOURCE#{RESOURCE_ID}"},
    }


@pytest.mark.unit
def test_job_resource_to_item(example_job_resource: JobResource) -> None:
    """A populated resource serializes all keys and attributes exactly."""
    assert example_job_resource.to_item() == {
        **example_job_resource.key,
        **example_job_resource.gsi1_key(),
        "TYPE": {"S": "JOB_RESOURCE"},
        "job_id": {"S": JOB_ID},
        "resource_id": {"S": RESOURCE_ID},
        "instance_id": {"S": "i-0123456789abcdef0"},
        "instance_type": {"S": "p3.2xlarge"},
        "resource_type": {"S": "gpu"},
        "allocated_at": {"S": ALLOCATED_AT},
        "status": {"S": "allocated"},
        "gpu_count": {"N": "8"},
        "released_at": {"S": RELEASED_AT},
        "resource_config": {
            "M": {"memory_mb": {"N": "61440"}, "vcpus": {"N": "8"}}
        },
    }


@pytest.mark.unit
def test_job_resource_nested_config_round_trip() -> None:
    """Nested Dynamo maps preserve lists, booleans, nulls, and numbers."""
    config = {
        "limits": {"memory": 64, "preemptible": False},
        "devices": ["gpu0", {"active": True}, None],
    }
    resource = make_resource(resource_config=config)
    item = resource.to_item()
    assert item["resource_config"]["M"]["devices"]["L"][1] == {
        "M": {"active": {"BOOL": True}}
    }
    assert item_to_job_resource(item) == resource


@pytest.mark.unit
def test_job_resource_minimal_round_trip() -> None:
    """Optional attributes remain absent through a Dynamo round trip."""
    resource = make_resource(
        gpu_count=None, released_at=None, resource_config=None
    )
    item = resource.to_item()
    assert all(
        field not in item
        for field in ("gpu_count", "released_at", "resource_config")
    )
    assert item_to_job_resource(item) == resource


@pytest.mark.unit
def test_job_resource_repr_and_iteration(
    example_job_resource: JobResource,
) -> None:
    """Representation and iteration expose every dataclass field."""
    assert dict(example_job_resource) == resource_kwargs()
    representation = repr(example_job_resource)
    for expected in (
        "JobResource(",
        f"job_id='{JOB_ID}'",
        f"resource_id='{RESOURCE_ID}'",
        "instance_id='i-0123456789abcdef0'",
        "instance_type='p3.2xlarge'",
        "resource_type='gpu'",
        f"allocated_at='{ALLOCATED_AT}'",
        f"released_at='{RELEASED_AT}'",
        "status='allocated'",
        "gpu_count=8",
        "resource_config=",
    ):
        assert expected in representation


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "4f52804b-2fad-4e00-92c8-b593da3a8ed3"),
        ("resource_id", "6e63804c-3abd-4f11-93d9-c694eb3b9de4"),
        ("instance_id", "i-different"),
        ("instance_type", "c5.2xlarge"),
        ("resource_type", "cpu"),
        ("allocated_at", "2021-01-03T00:00:00"),
        ("status", "released"),
        ("gpu_count", 4),
        ("released_at", "2021-01-04T00:00:00"),
        ("resource_config", {"memory_mb": 32768}),
    ],
)
def test_job_resource_equality_detects_each_field(
    field: str, value: Any
) -> None:
    """Changing any field changes dataclass equality."""
    baseline = make_resource()
    same = make_resource()
    assert baseline == same
    assert hash(baseline) == hash(same)
    assert baseline != make_resource(**{field: value})
    assert baseline != object()


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("status"),
        lambda item: item.__setitem__("status", {"N": "1"}),
        lambda item: item.__setitem__("gpu_count", {"N": "1.5"}),
        lambda item: item.__setitem__("resource_config", {"S": "invalid"}),
    ],
)
def test_item_to_job_resource_rejects_malformed_items(mutation: Any) -> None:
    """Missing and incorrectly encoded Dynamo attributes are rejected."""
    item = make_resource().to_item()
    mutation(item)
    with pytest.raises(ValueError):
        item_to_job_resource(item)
