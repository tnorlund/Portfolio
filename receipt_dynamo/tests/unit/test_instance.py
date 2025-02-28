from datetime import datetime

import pytest

from receipt_dynamo.entities.instance import Instance, itemToInstance


@pytest.fixture
def example_instance():
    """Provides a sample Instance for testing."""
    # fmt: off
    return Instance(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 
        "p3.2xlarge",
        4,
        "running",
        "2021-01-01T00:00:00",
        "192.168.1.1",
        "us-east-1a",
        True,
        "healthy"
    )
    # fmt: on


@pytest.mark.unit
def test_instance_init_valid(example_instance):
    """Test the Instance constructor with valid parameters."""
    assert (
        example_instance.instance_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_instance.instance_type == "p3.2xlarge"
    assert example_instance.gpu_count == 4
    assert example_instance.status == "running"
    assert example_instance.launched_at == "2021-01-01T00:00:00"
    assert example_instance.ip_address == "192.168.1.1"
    assert example_instance.availability_zone == "us-east-1a"
    assert example_instance.is_spot is True
    assert example_instance.health_status == "healthy"


@pytest.mark.unit
def test_instance_init_datetime():
    """Test the Instance constructor with a datetime object for launched_at."""
    instance = Instance(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "p3.2xlarge",
        4,
        "running",
        datetime(2021, 1, 1),
        "192.168.1.1",
        "us-east-1a",
        True,
        "healthy",
    )
    assert instance.launched_at == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_instance_init_invalid_id():
    """Test the Instance constructor with invalid instance_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Instance(
            1,  # Invalid: should be a string
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Instance(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_instance_type():
    """Test the Instance constructor with invalid instance_type."""
    with pytest.raises(
        ValueError, match="instance_type must be a non-empty string"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",  # Invalid: empty string
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )

    with pytest.raises(
        ValueError, match="instance_type must be a non-empty string"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a string
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_gpu_count():
    """Test the Instance constructor with invalid gpu_count."""
    with pytest.raises(
        ValueError, match="gpu_count must be a non-negative integer"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            -1,  # Invalid: negative number
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )

    with pytest.raises(
        ValueError, match="gpu_count must be a non-negative integer"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            "4",  # Invalid: not an integer
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_status():
    """Test the Instance constructor with invalid status."""
    with pytest.raises(ValueError, match="status must be one of"):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "invalid_status",  # Invalid: not a valid status
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )

    with pytest.raises(ValueError, match="status must be one of"):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            123,  # Invalid: not a string
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_launched_at():
    """Test the Instance constructor with invalid launched_at."""
    with pytest.raises(
        ValueError, match="launched_at must be a datetime object or a string"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            123,  # Invalid: not a datetime or string
            "192.168.1.1",
            "us-east-1a",
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_ip_address():
    """Test the Instance constructor with invalid ip_address."""
    with pytest.raises(ValueError, match="ip_address must be a string"):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            123,  # Invalid: not a string
            "us-east-1a",
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_availability_zone():
    """Test the Instance constructor with invalid availability_zone."""
    with pytest.raises(
        ValueError, match="availability_zone must be a non-empty string"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "",  # Invalid: empty string
            True,
            "healthy",
        )

    with pytest.raises(
        ValueError, match="availability_zone must be a non-empty string"
    ):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            123,  # Invalid: not a string
            True,
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_is_spot():
    """Test the Instance constructor with invalid is_spot."""
    with pytest.raises(ValueError, match="is_spot must be a boolean"):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            "true",  # Invalid: not a boolean
            "healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_health_status():
    """Test the Instance constructor with invalid health_status."""
    with pytest.raises(ValueError, match="health_status must be one of"):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            "invalid_health_status",  # Invalid: not a valid health status
        )

    with pytest.raises(ValueError, match="health_status must be one of"):
        Instance(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "p3.2xlarge",
            4,
            "running",
            "2021-01-01T00:00:00",
            "192.168.1.1",
            "us-east-1a",
            True,
            123,  # Invalid: not a string
        )


@pytest.mark.unit
def test_instance_key(example_instance):
    """Test the Instance.key() method."""
    assert example_instance.key() == {
        "PK": {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "INSTANCE"},
    }


@pytest.mark.unit
def test_instance_gsi1_key(example_instance):
    """Test the Instance.gsi1_key() method."""
    assert example_instance.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#running"},
        "GSI1SK": {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
    }


@pytest.mark.unit
def test_instance_to_item(example_instance):
    """Test the Instance.to_item() method."""
    item = example_instance.to_item()
    assert item["PK"] == {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "INSTANCE"}
    assert item["GSI1PK"] == {"S": "STATUS#running"}
    assert item["GSI1SK"] == {
        "S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    }
    assert item["TYPE"] == {"S": "INSTANCE"}
    assert item["instance_type"] == {"S": "p3.2xlarge"}
    assert item["gpu_count"] == {"N": "4"}
    assert item["status"] == {"S": "running"}
    assert item["launched_at"] == {"S": "2021-01-01T00:00:00"}
    assert item["ip_address"] == {"S": "192.168.1.1"}
    assert item["availability_zone"] == {"S": "us-east-1a"}
    assert item["is_spot"] == {"BOOL": True}
    assert item["health_status"] == {"S": "healthy"}


@pytest.mark.unit
def test_instance_repr(example_instance):
    """Test the Instance.__repr__() method."""
    repr_str = repr(example_instance)
    assert "instance_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "instance_type='p3.2xlarge'" in repr_str
    assert "gpu_count=4" in repr_str
    assert "status='running'" in repr_str
    assert "launched_at='2021-01-01T00:00:00'" in repr_str
    assert "ip_address='192.168.1.1'" in repr_str
    assert "availability_zone='us-east-1a'" in repr_str
    assert "is_spot=True" in repr_str
    assert "health_status='healthy'" in repr_str


@pytest.mark.unit
def test_instance_iter(example_instance):
    """Test the Instance.__iter__() method."""
    instance_dict = dict(example_instance)
    assert (
        instance_dict["instance_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert instance_dict["instance_type"] == "p3.2xlarge"
    assert instance_dict["gpu_count"] == 4
    assert instance_dict["status"] == "running"
    assert instance_dict["launched_at"] == "2021-01-01T00:00:00"
    assert instance_dict["ip_address"] == "192.168.1.1"
    assert instance_dict["availability_zone"] == "us-east-1a"
    assert instance_dict["is_spot"] is True
    assert instance_dict["health_status"] == "healthy"


@pytest.mark.unit
def test_instance_eq():
    """Test the Instance.__eq__() method."""
    # fmt: off
    instance1 = Instance(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "p3.2xlarge",
        4,
        "running",
        "2021-01-01T00:00:00",
        "192.168.1.1",
        "us-east-1a",
        True,
        "healthy"
    )
    instance2 = Instance(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "p3.2xlarge",
        4,
        "running",
        "2021-01-01T00:00:00",
        "192.168.1.1",
        "us-east-1a",
        True,
        "healthy"
    )
    instance3 = Instance(
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Different instance_id
        "p3.2xlarge",
        4,
        "running",
        "2021-01-01T00:00:00",
        "192.168.1.1",
        "us-east-1a",
        True,
        "healthy"
    )
    instance4 = Instance(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "g4dn.xlarge",  # Different instance_type
        4,
        "running",
        "2021-01-01T00:00:00",
        "192.168.1.1",
        "us-east-1a",
        True,
        "healthy"
    )
    # fmt: on

    assert instance1 == instance2, "Should be equal"
    assert instance1 != instance3, "Different instance_id"
    assert instance1 != instance4, "Different instance_type"
    assert instance1 != 42, "Not an Instance object"


@pytest.mark.unit
def test_itemToInstance(example_instance):
    """Test the itemToInstance() function."""
    # Test with a valid item
    item = example_instance.to_item()
    instance = itemToInstance(item)
    assert instance == example_instance

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        itemToInstance({"PK": {"S": "INSTANCE#id"}, "SK": {"S": "INSTANCE"}})

    # Test with invalid item format
    with pytest.raises(ValueError, match="Error converting item to Instance"):
        itemToInstance(
            {
                "PK": {"S": "INSTANCE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "INSTANCE"},
                "TYPE": {"S": "INSTANCE"},
                "instance_type": {"S": "p3.2xlarge"},
                "gpu_count": {"N": "4"},
                "status": {"S": "running"},
                "launched_at": {"S": "2021-01-01T00:00:00"},
                "ip_address": {"S": "192.168.1.1"},
                "availability_zone": {"S": "us-east-1a"},
                "is_spot": {"INVALID_TYPE": True},  # Invalid type
                "health_status": {"S": "healthy"},
            }
        )
