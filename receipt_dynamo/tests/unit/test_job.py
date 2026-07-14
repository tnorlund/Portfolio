# pylint: disable=redefined-outer-name
"""Unit tests for Job entity."""

import pytest

from receipt_dynamo import Job, item_to_job
from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_map

JOB_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
BASE_KWARGS = {
    "job_id": JOB_ID,
    "name": "Training Job",
    "description": "Example training job description",
    "created_at": "2021-01-01T00:00:00",
    "created_by": "user123",
    "status": "pending",
    "priority": "medium",
    "job_config": {"model": "layoutlm", "batch_size": 32, "epochs": 10},
}


def make_job(**overrides):
    """Build a Job with sensible defaults and optional field overrides."""
    kwargs = {**BASE_KWARGS, **overrides}
    return Job(**kwargs)


@pytest.fixture
def example_job():
    """Provides a sample Job for testing."""
    return make_job(
        estimated_duration=3600,
        tags={"project": "receipts", "environment": "dev"},
    )


@pytest.fixture
def example_job_minimal():
    """Provides a minimal sample Job for testing."""
    return make_job(job_config={"model": "layoutlm"})


@pytest.mark.unit
def test_job_init_valid(example_job):
    """Test the Job constructor with valid parameters."""
    assert dict(example_job) == {
        **BASE_KWARGS,
        "estimated_duration": 3600,
        "tags": {"project": "receipts", "environment": "dev"},
        "storage": None,
        "results": None,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": 1}, "uuid must be a string"),
        ({"job_id": "not-a-uuid"}, "uuid must be a valid UUID"),
        ({"name": ""}, "name must be a non-empty string"),
        ({"name": 123}, "name must be a non-empty string"),
        ({"description": 123}, "description must be a string"),
        ({"created_at": 123}, "created_at must be a datetime object"),
        ({"created_by": ""}, "created_by must be a non-empty string"),
        ({"created_by": 123}, "created_by must be a non-empty string"),
        ({"status": "invalid_status"}, "JobStatus must be one of"),
        ({"status": 123}, "JobStatus must be a str or JobStatus"),
        ({"priority": "invalid_priority"}, "priority must be one of"),
        ({"priority": 123}, "priority must be one of"),
        ({"job_config": "not_a_dict"}, "job_config must be a dictionary"),
        (
            {"estimated_duration": 0},
            "estimated_duration must be a positive integer",
        ),
        (
            {"estimated_duration": -100},
            "estimated_duration must be a positive integer",
        ),
        (
            {"estimated_duration": "3600"},
            "estimated_duration must be a positive integer",
        ),
        ({"tags": "not_a_dict"}, "tags must be a dictionary"),
        ({"storage": "not-a-dict"}, "storage must be a dictionary"),
        (
            {"storage": {"bucket": 123}},
            "storage keys and values must be strings",
        ),
        ({"results": "not-a-dict"}, "results must be a dictionary"),
    ],
)
def test_job_init_invalid_fields(overrides, match):
    """Test constructor validation errors for invalid field values."""
    with pytest.raises(ValueError, match=match):
        make_job(**overrides)


@pytest.mark.unit
def test_job_storage_normalization():
    """Ensure *_prefix entries are normalized with trailing slash."""
    job = make_job(
        description="Example",
        job_config={"model": "layoutlm"},
        storage={
            "bucket": "layoutlm-models-571631d",
            "run_root_prefix": "runs/2025-09-20/receipts-abc",
            "best_prefix": "runs/2025-09-20/receipts-abc/best",
            "publish_model_prefix": "models/layoutlm/0.1.0/abc",
        },
    )
    assert job.storage["bucket"] == "layoutlm-models-571631d"
    assert job.storage["run_root_prefix"].endswith("/")
    assert job.storage["best_prefix"].endswith("/")
    assert job.storage["publish_model_prefix"].endswith("/")


@pytest.mark.unit
def test_job_key(example_job):
    """Test the Job.key method."""
    assert example_job.key == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": "JOB"},
    }


@pytest.mark.unit
def test_job_gsi_keys(example_job):
    """Test GSI key helpers."""
    assert example_job.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#pending"},
        "GSI1SK": {"S": "CREATED#2021-01-01T00:00:00"},
    }
    assert example_job.gsi2_key() == {
        "GSI2PK": {"S": "JOB_NAME#Training Job"},
        "GSI2SK": {"S": "CREATED#2021-01-01T00:00:00"},
    }


@pytest.mark.unit
def test_job_to_item(example_job, example_job_minimal):
    """Test the Job.to_item() method."""
    item = example_job.to_item()
    assert item == {
        "PK": {"S": f"JOB#{JOB_ID}"},
        "SK": {"S": "JOB"},
        "GSI1PK": {"S": "STATUS#pending"},
        "GSI1SK": {"S": "CREATED#2021-01-01T00:00:00"},
        "GSI2PK": {"S": "JOB_NAME#Training Job"},
        "GSI2SK": {"S": "CREATED#2021-01-01T00:00:00"},
        "TYPE": {"S": "JOB"},
        "name": {"S": "Training Job"},
        "description": {"S": "Example training job description"},
        "created_at": {"S": "2021-01-01T00:00:00"},
        "created_by": {"S": "user123"},
        "status": {"S": "pending"},
        "priority": {"S": "medium"},
        "job_config": {
            "M": {
                "model": {"S": "layoutlm"},
                "batch_size": {"N": "32"},
                "epochs": {"N": "10"},
            }
        },
        "estimated_duration": {"N": "3600"},
        "tags": {
            "M": {
                "project": {"S": "receipts"},
                "environment": {"S": "dev"},
            }
        },
    }

    item = example_job_minimal.to_item()
    assert "estimated_duration" not in item
    assert "tags" not in item
    assert item["job_config"] == {"M": {"model": {"S": "layoutlm"}}}


@pytest.mark.unit
def test_job_to_item_and_back_with_storage():
    """Roundtrip to_item/item_to_job should preserve normalized storage."""
    storage = {
        "bucket": "layoutlm-models-571631d",
        "run_root_prefix": "runs/2025-09-20/receipts-xyz",
        "checkpoints_prefix": "runs/2025-09-20/receipts-xyz/checkpoints",
        "logs_prefix": "runs/2025-09-20/receipts-xyz/logs",
        "config_prefix": "runs/2025-09-20/receipts-xyz/config",
        "publish_model_prefix": "models/layoutlm/0.1.0/xyz",
    }
    job = make_job(
        description="Example",
        job_config={"model": "layoutlm"},
        storage=storage,
    )

    item = job.to_item()
    assert "storage" in item and "M" in item["storage"]
    for key, value in parse_dynamodb_map(item["storage"]["M"]).items():
        if key.endswith("_prefix"):
            assert value.endswith("/")

    job2 = item_to_job(item)
    assert job2 == job
    assert all(
        value.endswith("/")
        for key, value in job2.storage.items()
        if key.endswith("_prefix")
    )


@pytest.mark.unit
def test_job_s3_helper_methods():
    """Validate S3 helper URI builders."""
    job = make_job(
        description="Example",
        job_config={"model": "layoutlm"},
        storage={
            "bucket": "layoutlm-models-571631d",
            "run_root_prefix": "runs/2025-09-20/receipts-xyz/",
            "publish_model_prefix": "models/layoutlm/0.1.0/xyz/",
        },
    )

    assert job.storage_bucket() == "layoutlm-models-571631d"
    assert (
        job.s3_uri_for_prefix("run_root_prefix")
        == "s3://layoutlm-models-571631d/runs/2025-09-20/receipts-xyz/"
    )
    assert job.best_dir_uri() == (
        "s3://layoutlm-models-571631d/runs/2025-09-20/receipts-xyz/best/"
    )
    assert job.publish_dir_uri() == (
        "s3://layoutlm-models-571631d/models/layoutlm/0.1.0/xyz/"
    )

    job_explicit = make_job(
        description="Example",
        job_config={"model": "layoutlm"},
        storage={
            "bucket": "layoutlm-models-571631d",
            "best_prefix": "runs/2025-09-20/receipts-xyz/best/",
        },
    )
    assert job_explicit.best_dir_uri() == (
        "s3://layoutlm-models-571631d/runs/2025-09-20/receipts-xyz/best/"
    )


@pytest.mark.unit
def test_job_repr(example_job):
    """Test the Job.__repr__() method."""
    repr_str = repr(example_job)
    for expected in [
        f"job_id='{JOB_ID}'",
        "name='Training Job'",
        "description='Example training job description'",
        "created_at='2021-01-01T00:00:00'",
        "created_by='user123'",
        "status='pending'",
        "priority='medium'",
        "'model': 'layoutlm'",
        "'batch_size': 32",
        "'epochs': 10",
        "estimated_duration=3600",
        "'project': 'receipts'",
        "'environment': 'dev'",
    ]:
        assert expected in repr_str


@pytest.mark.unit
def test_job_iter(example_job):
    """Test the Job.__iter__() method."""
    assert dict(example_job) == {
        **BASE_KWARGS,
        "estimated_duration": 3600,
        "tags": {"project": "receipts", "environment": "dev"},
        "storage": None,
        "results": None,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"job_id": "4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        {"name": "Different Job"},
        {"description": "Different description"},
        {"created_at": "2021-01-02T00:00:00"},
        {"created_by": "different_user"},
        {"status": "running"},
        {"priority": "high"},
        {"job_config": {"model": "different", "batch_size": 32, "epochs": 10}},
        {"estimated_duration": 7200},
        {"tags": {"project": "different", "environment": "dev"}},
    ],
)
def test_job_eq_detects_field_differences(overrides):
    """Test Job equality includes all relevant fields."""
    baseline = make_job(
        estimated_duration=3600,
        tags={"project": "receipts", "environment": "dev"},
    )
    changed_kwargs = {
        "estimated_duration": 3600,
        "tags": {"project": "receipts", "environment": "dev"},
        **overrides,
    }
    changed = make_job(**changed_kwargs)
    assert baseline != changed


@pytest.mark.unit
def test_job_eq_matches_same_values(example_job):
    """Test equal and incompatible Job comparisons."""
    assert example_job == make_job(
        estimated_duration=3600,
        tags={"project": "receipts", "environment": "dev"},
    )
    assert example_job != 42


@pytest.mark.unit
def test_item_to_job(example_job, example_job_minimal):
    """Test the item_to_job() function."""
    assert item_to_job(example_job.to_item()) == example_job
    assert item_to_job(example_job_minimal.to_item()) == example_job_minimal

    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_job({"PK": {"S": "JOB#id"}, "SK": {"S": "JOB"}})

    invalid_item = example_job.to_item()
    invalid_item["status"] = {"INVALID_TYPE": "pending"}
    with pytest.raises(ValueError, match="Error converting item to Job"):
        item_to_job(invalid_item)


@pytest.mark.unit
def test_parse_dynamodb_map():
    """Test the _parse_dynamodb_map function."""
    result = parse_dynamodb_map(
        {
            "string": {"S": "value"},
            "number": {"N": "42"},
            "decimal": {"N": "3.14"},
            "boolean": {"BOOL": True},
            "null": {"NULL": True},
            "nested_map": {
                "M": {
                    "inner_string": {"S": "inner_value"},
                    "inner_number": {"N": "10"},
                }
            },
            "list": {
                "L": [
                    {"S": "item1"},
                    {"N": "2"},
                    {"BOOL": False},
                    {"M": {"key": {"S": "value"}}},
                ]
            },
        }
    )
    assert result == {
        "string": "value",
        "number": 42,
        "decimal": 3.14,
        "boolean": True,
        "null": None,
        "nested_map": {"inner_string": "inner_value", "inner_number": 10},
        "list": ["item1", 2, False, {"key": "value"}],
    }
