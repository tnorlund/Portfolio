"""Unit tests for the JobDependency DynamoDB entity."""

from datetime import datetime
from uuid import uuid4

import pytest

from receipt_dynamo import JobDependency, item_to_job_dependency

DEPENDENT_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
DEPENDENCY_ID = "9d32fa91-1d2f-4b3a-a88c-9e7ab3aeee4b"
BASE = {
    "dependent_job_id": DEPENDENT_ID,
    "dependency_job_id": DEPENDENCY_ID,
    "type": "SUCCESS",
    "created_at": "2024-01-01T12:00:00",
}


def make_dependency(**overrides):
    """Build a valid dependency with field overrides."""
    return JobDependency(**(BASE | overrides))


@pytest.mark.unit
@pytest.mark.parametrize("condition", [None, "artifact exists"])
def test_job_dependency_init_and_datetime_normalization(condition):
    dependency = make_dependency(
        created_at=datetime(2024, 1, 1, 12), condition=condition
    )
    assert dict(dependency) == {
        **BASE,
        "created_at": "2024-01-01T12:00:00",
        "condition": condition,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"dependent_job_id": 1}, "uuid must be a string"),
        ({"dependent_job_id": "bad"}, "valid UUIDv4"),
        ({"dependency_job_id": 1}, "uuid must be a string"),
        ({"dependency_job_id": "bad"}, "valid UUIDv4"),
        ({"dependency_job_id": DEPENDENT_ID}, "cannot depend on itself"),
        ({"type": "bad"}, "type must be one of"),
        ({"type": 1}, "type must be one of"),
        ({"created_at": 1}, "created_at must be a datetime"),
        ({"condition": 1}, "condition must be a string"),
    ],
)
def test_job_dependency_rejects_invalid_fields(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_dependency(**overrides)


@pytest.mark.unit
def test_job_dependency_keys_and_item_shape():
    dependency = make_dependency(type="success", condition="ready")
    assert dependency.type == "SUCCESS"
    assert dependency.key == {
        "PK": {"S": f"JOB#{DEPENDENT_ID}"},
        "SK": {"S": f"DEPENDS_ON#{DEPENDENCY_ID}"},
    }
    assert dependency.gsi1_key() == {
        "GSI1PK": {"S": "DEPENDENCY"},
        "GSI1SK": {
            "S": f"DEPENDENT#{DEPENDENT_ID}#DEPENDENCY#{DEPENDENCY_ID}"
        },
    }
    assert dependency.gsi2_key() == {
        "GSI2PK": {"S": "DEPENDENCY"},
        "GSI2SK": {
            "S": f"DEPENDED_BY#{DEPENDENCY_ID}#DEPENDENT#{DEPENDENT_ID}"
        },
    }
    assert dependency.to_item() == {
        **dependency.key,
        **dependency.gsi1_key(),
        **dependency.gsi2_key(),
        "TYPE": {"S": "JOB_DEPENDENCY"},
        "dependent_job_id": {"S": DEPENDENT_ID},
        "dependency_job_id": {"S": DEPENDENCY_ID},
        "type": {"S": "SUCCESS"},
        "created_at": {"S": BASE["created_at"]},
        "condition": {"S": "ready"},
    }
    assert "condition" not in make_dependency().to_item()


@pytest.mark.unit
@pytest.mark.parametrize("condition", [None, "ready"])
def test_job_dependency_round_trip(condition):
    dependency = make_dependency(condition=condition)
    assert item_to_job_dependency(dependency.to_item()) == dependency


@pytest.mark.unit
def test_job_dependency_value_semantics():
    dependency = make_dependency(condition="ready")
    duplicate = make_dependency(condition="ready")
    different = make_dependency(dependency_job_id=str(uuid4()))

    assert dependency == duplicate
    assert hash(dependency) == hash(duplicate)
    assert dependency != different
    assert dependency != "not a dependency"
    assert dict(dependency)["condition"] == "ready"
    rendered = repr(dependency)
    assert "JobDependency(" in rendered
    assert DEPENDENT_ID in rendered and DEPENDENCY_ID in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.pop("type"), "missing keys"),
        (lambda item: item.__setitem__("type", {}), "Error converting"),
        (
            lambda item: item.__setitem__(
                "dependency_job_id", {"S": DEPENDENT_ID}
            ),
            "cannot depend on itself",
        ),
    ],
)
def test_job_dependency_rejects_invalid_items(mutator, match):
    item = make_dependency().to_item()
    mutator(item)
    with pytest.raises(ValueError, match=match):
        item_to_job_dependency(item)
