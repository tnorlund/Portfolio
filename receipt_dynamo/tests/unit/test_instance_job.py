from datetime import datetime

import pytest

from receipt_dynamo.entities.instance_job import (
    InstanceJob,
    item_to_instance_job,
)


@pytest.fixture
def example_instance_job():
    """Provides a sample InstanceJob for testing."""
    return InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "running",
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )


@pytest.fixture
def example_instance_job_minimal():
    """Provides a minimal sample InstanceJob for testing."""
    return InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "running",
    )


@pytest.mark.unit
def test_instance_job_init_valid(example_instance_job):
    """Test the InstanceJob constructor with valid parameters."""
    assert (
        example_instance_job.instance_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_instance_job.job_id == "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_instance_job.assigned_at == "2021-01-01T00:00:00"
    assert example_instance_job.status == "running"
    assert example_instance_job.resource_utilization == {
        "cpu_utilization": 75,
        "memory_utilization": 60,
        "gpu_utilization": 90,
    }


@pytest.mark.unit
def test_instance_job_init_minimal(example_instance_job_minimal):
    """Test the InstanceJob constructor with minimal parameters."""
    assert (
        example_instance_job_minimal.instance_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_instance_job_minimal.job_id
        == "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_instance_job_minimal.assigned_at == "2021-01-01T00:00:00"
    assert example_instance_job_minimal.status == "running"
    assert example_instance_job_minimal.resource_utilization == {}


@pytest.mark.unit
def test_instance_job_init_datetime():
    """InstanceJob constructor with a datetime object for assigned_at."""
    instance_job = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        datetime(2021, 1, 1),
        "running",
    )
    assert instance_job.assigned_at == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_instance_job_init_invalid_instance_id():
    """Test the InstanceJob constructor with invalid instance_id."""
    with pytest.raises(
        ValueError, match="instance_id must be a non-empty string"
    ):
        InstanceJob(
            1,  # Invalid: not a string
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T00:00:00",
            "running",
        )


@pytest.mark.unit
def test_instance_job_init_invalid_job_id():
    """Test the InstanceJob constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        InstanceJob(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,  # Invalid: should be a string
            "2021-01-01T00:00:00",
            "running",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        InstanceJob(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-a-uuid",  # Invalid: not a valid UUID format
            "2021-01-01T00:00:00",
            "running",
        )


@pytest.mark.unit
def test_instance_job_init_invalid_assigned_at():
    """Test the InstanceJob constructor with invalid assigned_at."""
    with pytest.raises(
        ValueError, match="assigned_at must be a datetime object or a string"
    ):
        InstanceJob(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a datetime or string
            "running",
        )


@pytest.mark.unit
def test_instance_job_init_invalid_status():
    """Test the InstanceJob constructor with invalid status."""
    with pytest.raises(ValueError, match="status must be one of"):
        InstanceJob(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T00:00:00",
            "invalid_status",
        )

    with pytest.raises(ValueError, match="status must be one of"):
        InstanceJob(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T00:00:00",
            123,
        )


@pytest.mark.unit
def test_instance_job_init_invalid_resource_utilization():
    """Test the InstanceJob constructor with invalid resource_utilization."""
    with pytest.raises(
        ValueError, match="resource_utilization must be a dictionary"
    ):
        InstanceJob(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T00:00:00",
            "running",
            "not_a_dict",
        )


@pytest.mark.unit
def test_instance_job_key(example_instance_job):
    """Test the InstanceJob.key method."""
    assert example_instance_job.key == {
        "PK": {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
    }


@pytest.mark.unit
def test_instance_job_gsi1_key(example_instance_job):
    """Test the InstanceJob.gsi1_key() method."""
    assert example_instance_job.gsi1_key() == {
        "GSI1PK": {"S": "JOB"},
        "GSI1SK": {
            "S": "JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3#INSTANCE#"
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
        },
    }


@pytest.mark.unit
def test_instance_job_to_item(
    example_instance_job, example_instance_job_minimal
):
    """Test the InstanceJob.to_item() method."""
    # Test with full instance job
    item = example_instance_job.to_item()
    assert item["PK"] == {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["GSI1PK"] == {"S": "JOB"}
    assert item["GSI1SK"] == {
        "S": "JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3#INSTANCE#"
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    }
    assert item["TYPE"] == {"S": "INSTANCE_JOB"}
    assert item["assigned_at"] == {"S": "2021-01-01T00:00:00"}
    assert item["status"] == {"S": "running"}
    assert item["resource_utilization"]["M"]["cpu_utilization"] == {"N": "75"}
    assert item["resource_utilization"]["M"]["memory_utilization"] == {
        "N": "60"
    }
    assert item["resource_utilization"]["M"]["gpu_utilization"] == {"N": "90"}

    # Test minimal instance job
    item = example_instance_job_minimal.to_item()
    assert "resource_utilization" not in item


@pytest.mark.unit
def test_instance_job_repr(example_instance_job):
    """Test the InstanceJob.__repr__() method."""
    repr_str = repr(example_instance_job)
    assert "instance_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "job_id='4f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "assigned_at='2021-01-01T00:00:00'" in repr_str
    assert "status='running'" in repr_str
    assert "'cpu_utilization': 75" in repr_str
    assert "'memory_utilization': 60" in repr_str
    assert "'gpu_utilization': 90" in repr_str


@pytest.mark.unit
def test_instance_job_iter(example_instance_job):
    """Test the InstanceJob.__iter__() method."""
    instance_job_dict = dict(example_instance_job)
    assert (
        instance_job_dict["instance_id"]
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        instance_job_dict["job_id"] == "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert instance_job_dict["assigned_at"] == "2021-01-01T00:00:00"
    assert instance_job_dict["status"] == "running"
    assert instance_job_dict["resource_utilization"] == {
        "cpu_utilization": 75,
        "memory_utilization": 60,
        "gpu_utilization": 90,
    }


@pytest.mark.unit
def test_instance_job_eq():
    """Test the InstanceJob.__eq__() method."""

    instance_job1 = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "running",
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )
    instance_job2 = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "running",
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )
    instance_job3 = InstanceJob(
        "5f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Different instance_id
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "running",
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )
    instance_job4 = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Different job_id
        "2021-01-01T00:00:00",
        "running",
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )
    instance_job5 = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-02T00:00:00",  # Different assigned_at
        "running",
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )
    instance_job6 = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "completed",  # Different status
        {
            "cpu_utilization": 75,
            "memory_utilization": 60,
            "gpu_utilization": 90,
        },
    )
    instance_job7 = InstanceJob(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T00:00:00",
        "running",
        {"cpu_utilization": 50},
    )

    assert instance_job1 == instance_job2, "Should be equal"
    assert instance_job1 != instance_job3, "Different instance_id"
    assert instance_job1 != instance_job4, "Different job_id"
    assert instance_job1 != instance_job5, "Different assigned_at"
    assert instance_job1 != instance_job6, "Different status"
    assert instance_job1 != instance_job7, "Different resource_utilization"
    assert instance_job1 != 42, "Not an InstanceJob object"


@pytest.mark.unit
def test_itemToInstanceJob(example_instance_job, example_instance_job_minimal):
    """Test the item_to_instance_job() function."""
    # Test with full instance job
    item = example_instance_job.to_item()
    instance_job = item_to_instance_job(item)
    assert instance_job == example_instance_job

    # Test with minimal instance job
    item = example_instance_job_minimal.to_item()
    instance_job = item_to_instance_job(item)
    assert instance_job == example_instance_job_minimal

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_instance_job(
            {"PK": {"S": "INSTANCE#id"}, "SK": {"S": "JOB#id"}}
        )

    # Test with invalid item format
    with pytest.raises(
        ValueError, match="Error converting item to InstanceJob"
    ):
        item_to_instance_job(
            {
                "PK": {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "TYPE": {"S": "INSTANCE_JOB"},
                "assigned_at": {"S": "2021-01-01T00:00:00"},
                "status": {"INVALID_TYPE": "running"},
            }
        )
