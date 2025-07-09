import pytest

from receipt_dynamo import JobResource, item_to_job_resource
from receipt_dynamo.entities.job_resource import _parse_dynamodb_map


@pytest.fixture
def example_job_resource():
    """Provides a sample JobResource for testing."""
    return JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        gpu_count=8,
        released_at="2021-01-02T00:00:00",
        resource_config={"memory_mb": 61440, "vcpus": 8},
    )


@pytest.fixture
def example_job_resource_minimal():
    """Provides a minimal sample JobResource for testing."""
    return JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "t2.micro",
        "cpu",
        "2021-01-01T00:00:00",
        "allocated",
    )


@pytest.mark.unit
def test_job_resource_init_valid(example_job_resource):
    """Test the JobResource constructor with valid parameters."""
    assert (
        example_job_resource.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_job_resource.resource_id
        == "5e63804c-3abd-4f11-83d9-c694eb3b9de4"
    )
    assert example_job_resource.instance_id == "i-0123456789abcdef0"
    assert example_job_resource.instance_type == "p3.2xlarge"
    assert example_job_resource.resource_type == "gpu"
    assert example_job_resource.allocated_at == "2021-01-01T00:00:00"
    assert example_job_resource.released_at == "2021-01-02T00:00:00"
    assert example_job_resource.status == "allocated"
    assert example_job_resource.gpu_count == 8
    assert example_job_resource.resource_config == {
        "memory_mb": 61440,
        "vcpus": 8,
    }


@pytest.mark.unit
def test_job_resource_init_invalid_job_id():
    """Test the JobResource constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobResource(
            1,  # Invalid: should be a string
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )

    with pytest.raises(ValueError, match="uuid must be a valid"):
        JobResource(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_resource_id():
    """Test the JobResource constructor with invalid resource_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,  # Invalid: should be a string
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )

    with pytest.raises(ValueError, match="uuid must be a valid"):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-a-uuid",  # Invalid: not a valid UUID format
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_instance_id():
    """Test the JobResource constructor with invalid instance_id."""
    with pytest.raises(
        ValueError, match="instance_id must be a non-empty string"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "",  # Invalid: empty string
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )

    with pytest.raises(
        ValueError, match="instance_id must be a non-empty string"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            123,  # Invalid: not a string
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_instance_type():
    """Test the JobResource constructor with invalid instance_type."""
    with pytest.raises(
        ValueError, match="instance_type must be a non-empty string"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "",  # Invalid: empty string
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )

    with pytest.raises(
        ValueError, match="instance_type must be a non-empty string"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            123,  # Invalid: not a string
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_resource_type():
    """Test the JobResource constructor with invalid resource_type."""
    with pytest.raises(ValueError, match="resource_type must be one of"):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "invalid_type",  # Invalid: not a valid resource type
            "2021-01-01T00:00:00",
            "allocated",
        )

    with pytest.raises(ValueError, match="resource_type must be one of"):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            123,  # Invalid: not a string
            "2021-01-01T00:00:00",
            "allocated",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_allocated_at():
    """Test the JobResource constructor with invalid allocated_at."""
    with pytest.raises(
        ValueError, match="allocated_at must be a datetime object or a string"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            123,  # Invalid: not a datetime or string
            "allocated",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_status():
    """Test the JobResource constructor with invalid status."""
    with pytest.raises(ValueError, match="status must be one of"):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "invalid_status",
        )

    with pytest.raises(ValueError, match="status must be one of"):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            123,
        )


@pytest.mark.unit
def test_job_resource_init_invalid_gpu_count():
    """Test the JobResource constructor with invalid gpu_count."""
    with pytest.raises(
        ValueError, match="gpu_count must be a non-negative integer"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
            gpu_count=-1,
        )

    with pytest.raises(
        ValueError, match="gpu_count must be a non-negative integer"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
            gpu_count="8",
        )


@pytest.mark.unit
def test_job_resource_init_invalid_released_at():
    """Test the JobResource constructor with invalid released_at."""
    with pytest.raises(
        ValueError, match="released_at must be a datetime object or a string"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
            released_at=123,
        )


@pytest.mark.unit
def test_job_resource_init_invalid_resource_config():
    """Test the JobResource constructor with invalid resource_config."""
    with pytest.raises(
        ValueError, match="resource_config must be a dictionary"
    ):
        JobResource(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
            "i-0123456789abcdef0",
            "p3.2xlarge",
            "gpu",
            "2021-01-01T00:00:00",
            "allocated",
            resource_config="not_a_dict",
        )


@pytest.mark.unit
def test_job_resource_key(example_job_resource):
    """Test the JobResource.key method."""
    assert example_job_resource.key == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RESOURCE#5e63804c-3abd-4f11-83d9-c694eb3b9de4"},
    }


@pytest.mark.unit
def test_job_resource_gsi1_key(example_job_resource):
    """Test the JobResource.gsi1_key() method."""
    assert example_job_resource.gsi1_key() == {
        "GSI1PK": {"S": "RESOURCE"},
        "GSI1SK": {"S": "RESOURCE#5e63804c-3abd-4f11-83d9-c694eb3b9de4"},
    }


@pytest.mark.unit
def test_job_resource_to_item(
    example_job_resource, example_job_resource_minimal
):
    """Test the JobResource.to_item() method."""
    # Test with full job resource
    item = example_job_resource.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "RESOURCE#5e63804c-3abd-4f11-83d9-c694eb3b9de4"}
    assert item["GSI1PK"] == {"S": "RESOURCE"}
    assert item["GSI1SK"] == {
        "S": "RESOURCE#5e63804c-3abd-4f11-83d9-c694eb3b9de4"
    }
    assert item["TYPE"] == {"S": "JOB_RESOURCE"}
    assert item["job_id"] == {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["resource_id"] == {"S": "5e63804c-3abd-4f11-83d9-c694eb3b9de4"}
    assert item["instance_id"] == {"S": "i-0123456789abcdef0"}
    assert item["instance_type"] == {"S": "p3.2xlarge"}
    assert item["resource_type"] == {"S": "gpu"}
    assert item["allocated_at"] == {"S": "2021-01-01T00:00:00"}
    assert item["released_at"] == {"S": "2021-01-02T00:00:00"}
    assert item["status"] == {"S": "allocated"}
    assert item["gpu_count"] == {"N": "8"}
    assert item["resource_config"]["M"]["memory_mb"] == {"N": "61440"}
    assert item["resource_config"]["M"]["vcpus"] == {"N": "8"}

    # Test minimal job resource
    item = example_job_resource_minimal.to_item()
    assert "released_at" not in item
    assert "gpu_count" not in item
    assert "resource_config" not in item


@pytest.mark.unit
def test_job_resource_repr(example_job_resource):
    """Test the JobResource.__repr__() method."""
    repr_str = repr(example_job_resource)
    assert "job_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "resource_id='5e63804c-3abd-4f11-83d9-c694eb3b9de4'" in repr_str
    assert "instance_id='i-0123456789abcdef0'" in repr_str
    assert "instance_type='p3.2xlarge'" in repr_str
    assert "resource_type='gpu'" in repr_str
    assert "allocated_at='2021-01-01T00:00:00'" in repr_str
    assert "released_at='2021-01-02T00:00:00'" in repr_str
    assert "status='allocated'" in repr_str
    assert "gpu_count=8" in repr_str
    assert "'memory_mb': 61440" in repr_str
    assert "'vcpus': 8" in repr_str


@pytest.mark.unit
def test_job_resource_iter(example_job_resource):
    """Test the JobResource.__iter__() method."""
    job_resource_dict = dict(example_job_resource)
    assert (
        job_resource_dict["job_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        job_resource_dict["resource_id"]
        == "5e63804c-3abd-4f11-83d9-c694eb3b9de4"
    )
    assert job_resource_dict["instance_id"] == "i-0123456789abcdef0"
    assert job_resource_dict["instance_type"] == "p3.2xlarge"
    assert job_resource_dict["resource_type"] == "gpu"
    assert job_resource_dict["allocated_at"] == "2021-01-01T00:00:00"
    assert job_resource_dict["released_at"] == "2021-01-02T00:00:00"
    assert job_resource_dict["status"] == "allocated"
    assert job_resource_dict["gpu_count"] == 8
    assert job_resource_dict["resource_config"] == {
        "memory_mb": 61440,
        "vcpus": 8,
    }


@pytest.mark.unit
def test_job_resource_eq():
    """Test the JobResource.__eq__() method."""

    resource1 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )
    resource2 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )
    resource3 = JobResource(
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different job_id
    resource4 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "6e63804c-3abd-4f11-93d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different resource_id, also fixed UUID format
    resource5 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-98765432100fedcba",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different instance_id
    resource6 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "c5.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different instance_type
    resource7 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "cpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different resource_type
    resource8 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-02T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different allocated_at
    resource9 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "released",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different status
    resource10 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        4,
        "2021-01-02T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different gpu_count
    resource11 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-03T00:00:00",
        {"memory_mb": 61440, "vcpus": 8},
    )  # Different released_at
    resource12 = JobResource(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5e63804c-3abd-4f11-83d9-c694eb3b9de4",
        "i-0123456789abcdef0",
        "p3.2xlarge",
        "gpu",
        "2021-01-01T00:00:00",
        "allocated",
        8,
        "2021-01-02T00:00:00",
        {"memory_mb": 32768, "vcpus": 4},
    )  # Different resource_config

    assert resource1 == resource2, "Should be equal"
    assert resource1 != resource3, "Different job_id"
    assert resource1 != resource4, "Different resource_id"
    assert resource1 != resource5, "Different instance_id"
    assert resource1 != resource6, "Different instance_type"
    assert resource1 != resource7, "Different resource_type"
    assert resource1 != resource8, "Different allocated_at"
    assert resource1 != resource9, "Different status"
    assert resource1 != resource10, "Different gpu_count"
    assert resource1 != resource11, "Different released_at"
    assert resource1 != resource12, "Different resource_config"

    # Compare with non-JobResource object
    assert resource1 != 42, "Not a JobResource object"


@pytest.mark.unit
def test_itemToJobResource(example_job_resource, example_job_resource_minimal):
    """Test the item_to_job_resource() function."""
    # Test with full job resource
    item = example_job_resource.to_item()
    job_resource = item_to_job_resource(item)
    assert job_resource == example_job_resource

    # Test with minimal job resource
    item = example_job_resource_minimal.to_item()
    job_resource = item_to_job_resource(item)
    assert job_resource == example_job_resource_minimal

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_job_resource(
            {"PK": {"S": "JOB#id"}, "SK": {"S": "RESOURCE#id"}}
        )

    # Test with invalid item format
    with pytest.raises(
        ValueError, match="Error converting item to JobResource"
    ):
        item_to_job_resource(
            {
                "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "RESOURCE#5e63804c-3abd-4f11-83d9-c694eb3b9de4"},
                "TYPE": {"S": "JOB_RESOURCE"},
                "job_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "resource_id": {"S": "5e63804c-3abd-4f11-83d9-c694eb3b9de4"},
                "instance_id": {"S": "i-0123456789abcdef0"},
                "instance_type": {"S": "p3.2xlarge"},
                "resource_type": {"S": "gpu"},
                "allocated_at": {"S": "2021-01-01T00:00:00"},
                "status": {"INVALID_TYPE": "allocated"},
            }
        )


@pytest.mark.unit
def test_parse_dynamodb_map():
    """Test the _parse_dynamodb_map function."""
    # Create a complex DynamoDB map
    dynamodb_map = {
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

    # Convert to Python values and test
    result = _parse_dynamodb_map(dynamodb_map)
    assert result["string"] == "value"
    assert result["number"] == 42
    assert result["decimal"] == 3.14
    assert result["boolean"] is True
    assert result["null"] is None
    assert result["nested_map"]["inner_string"] == "inner_value"
    assert result["nested_map"]["inner_number"] == 10
    assert result["list"][0] == "item1"
    assert result["list"][1] == 2
    assert result["list"][2] is False
    assert result["list"][3]["key"] == "value"
