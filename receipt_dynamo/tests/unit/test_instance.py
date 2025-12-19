from datetime import datetime

import pytest

from receipt_dynamo.entities.instance import Instance, item_to_instance

# ###############################
# # Instance Metadata Summary
# ###############################
# Instance ID:       i-09ee977b7e1673d46
# Region:            us-east-1
# Instance Type:     g4dn.xlarge
# Availability Zone: us-east-1b
# Local IP:          10.0.2.134
# Is Spot Instance:  true
# Detected GPUs:     1
# ###############################


@pytest.fixture
def example_instance():
    """Provides a sample Instance for testing."""
    return Instance(
        instance_id="i-09ee977b7e1673d46",
        instance_type="g4dn.xlarge",
        gpu_count=1,
        status="running",
        launched_at=datetime(2021, 1, 1),
        ip_address="10.0.2.134",
        availability_zone="us-east-1b",
        is_spot=True,
        health_status="healthy",
    )


@pytest.mark.unit
def test_instance_init_valid(example_instance):
    """Test the Instance constructor with valid parameters."""
    assert example_instance.instance_id == "i-09ee977b7e1673d46"
    assert example_instance.instance_type == "g4dn.xlarge"
    assert example_instance.gpu_count == 1
    assert example_instance.status == "running"
    assert example_instance.launched_at == "2021-01-01T00:00:00"
    assert example_instance.ip_address == "10.0.2.134"
    assert example_instance.availability_zone == "us-east-1b"
    assert example_instance.is_spot is True
    assert example_instance.health_status == "healthy"


@pytest.mark.unit
def test_instance_init_datetime():
    """Test the Instance constructor with a datetime object for launched_at."""
    instance = Instance(
        instance_id="i-09ee977b7e1673d46",
        instance_type="g4dn.xlarge",
        gpu_count=1,
        status="running",
        launched_at=datetime(2021, 1, 1),
        ip_address="10.0.2.134",
        availability_zone="us-east-1b",
        is_spot=True,
        health_status="healthy",
    )
    assert instance.launched_at == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_instance_init_invalid_id():
    """Test the Instance constructor with invalid instance_id."""
    with pytest.raises(ValueError, match="instance_id must be a non-empty string"):
        Instance(
            instance_id=None,
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1b",
            is_spot=True,
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_instance_type():
    """Test the Instance constructor with invalid instance_type."""
    with pytest.raises(ValueError, match="instance_type must be a non-empty string"):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type=None,
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1b",
            is_spot=True,
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_gpu_count():
    """Test the Instance constructor with invalid gpu_count."""
    with pytest.raises(ValueError, match="gpu_count must be a non-negative integer"):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=-1,  # Invalid: negative number
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1b",
            is_spot=True,
            health_status="healthy",
        )

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
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="invalid_status",  # Invalid: not a valid status
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1b",
            is_spot=True,
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_launched_at():
    """Test the Instance constructor with invalid launched_at."""
    with pytest.raises(
        ValueError, match="launched_at must be a datetime object or a string"
    ):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=123,  # Invalid: not a datetime or string
            ip_address="10.0.2.134",
            availability_zone="us-east-1b",
            is_spot=True,
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_ip_address():
    """Test the Instance constructor with invalid ip_address."""
    with pytest.raises(ValueError, match="ip_address must be a string"):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address=123,  # Invalid: not a string
            availability_zone="us-east-1b",
            is_spot=True,
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_availability_zone():
    """Test the Instance constructor with invalid availability_zone."""
    with pytest.raises(
        ValueError, match="availability_zone must be a non-empty string"
    ):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone=None,  # Invalid: None
            is_spot=True,
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_is_spot():
    """Test the Instance constructor with invalid is_spot."""
    with pytest.raises(ValueError, match="is_spot must be a boolean"):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1a",
            is_spot="true",  # Invalid: not a boolean
            health_status="healthy",
        )


@pytest.mark.unit
def test_instance_init_invalid_health_status():
    """Test the Instance constructor with invalid health_status."""
    with pytest.raises(ValueError, match="health_status must be one of"):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1a",
            is_spot=True,
            health_status="invalid_health_status",
        )

    with pytest.raises(ValueError, match="health_status must be one of"):
        Instance(
            instance_id="i-09ee977b7e1673d46",
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="running",
            launched_at=datetime(2021, 1, 1),
            ip_address="10.0.2.134",
            availability_zone="us-east-1a",
            is_spot=True,
            health_status=123,
        )


@pytest.mark.unit
def test_instance_key(example_instance):
    """Test the Instance.key method."""
    assert example_instance.key == {
        "PK": {"S": "INSTANCE#i-09ee977b7e1673d46"},
        "SK": {"S": "INSTANCE"},
    }


@pytest.mark.unit
def test_instance_gsi1_key(example_instance):
    """Test the Instance.gsi1_key() method."""
    assert example_instance.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#running"},
        "GSI1SK": {"S": "INSTANCE#i-09ee977b7e1673d46"},
    }


@pytest.mark.unit
def test_instance_to_item(example_instance):
    """Test the Instance.to_item() method."""
    item = example_instance.to_item()
    assert item["PK"] == {"S": "INSTANCE#i-09ee977b7e1673d46"}
    assert item["SK"] == {"S": "INSTANCE"}
    assert item["GSI1PK"] == {"S": "STATUS#running"}
    assert item["GSI1SK"] == {"S": "INSTANCE#i-09ee977b7e1673d46"}
    assert item["TYPE"] == {"S": "INSTANCE"}
    assert item["instance_type"] == {"S": "g4dn.xlarge"}
    assert item["gpu_count"] == {"N": "1"}
    assert item["status"] == {"S": "running"}
    assert item["launched_at"] == {"S": "2021-01-01T00:00:00"}
    assert item["ip_address"] == {"S": "10.0.2.134"}
    assert item["availability_zone"] == {"S": "us-east-1b"}
    assert item["is_spot"] == {"BOOL": True}
    assert item["health_status"] == {"S": "healthy"}


@pytest.mark.unit
def test_instance_repr(example_instance):
    """Test the Instance.__repr__() method."""
    repr_str = repr(example_instance)
    assert "instance_id='i-09ee977b7e1673d46'" in repr_str
    assert "instance_type='g4dn.xlarge'" in repr_str
    assert "gpu_count=1" in repr_str
    assert "status='running'" in repr_str
    assert "launched_at='2021-01-01T00:00:00'" in repr_str
    assert "ip_address='10.0.2.134'" in repr_str
    assert "availability_zone='us-east-1b'" in repr_str
    assert "is_spot=True" in repr_str
    assert "health_status='healthy'" in repr_str


@pytest.mark.unit
def test_instance_iter(example_instance):
    """Test the Instance.__iter__() method."""
    instance_dict = dict(example_instance)
    assert instance_dict["instance_id"] == "i-09ee977b7e1673d46"
    assert instance_dict["instance_type"] == "g4dn.xlarge"
    assert instance_dict["gpu_count"] == 1
    assert instance_dict["status"] == "running"
    assert instance_dict["launched_at"] == "2021-01-01T00:00:00"
    assert instance_dict["ip_address"] == "10.0.2.134"
    assert instance_dict["availability_zone"] == "us-east-1b"
    assert instance_dict["is_spot"] is True
    assert instance_dict["health_status"] == "healthy"


@pytest.mark.unit
def test_instance_eq():
    """Test the Instance.__eq__() method."""
    instance1 = Instance(
        "i-09ee977b7e1673d46",
        "g4dn.xlarge",
        1,
        "running",
        "2021-01-01T00:00:00",
        "10.0.2.134",
        "us-east-1b",
        True,
        "healthy",
    )
    instance2 = Instance(
        "i-09ee977b7e1673d46",
        "g4dn.xlarge",
        1,
        "running",
        "2021-01-01T00:00:00",
        "10.0.2.134",
        "us-east-1b",
        True,
        "healthy",
    )
    instance3 = Instance(
        "i-09ee977b7e1673d45",  # Different instance_id
        "g4dn.xlarge",
        1,
        "running",
        "2021-01-01T00:00:00",
        "10.0.2.134",
        "us-east-1b",
        True,
        "healthy",
    )
    instance4 = Instance(
        "i-09ee977b7e1673d46",
        "g4dn.2xlarge",  # Different instance_type
        1,
        "running",
        "2021-01-01T00:00:00",
        "10.0.2.134",
        "us-east-1b",
        True,
        "healthy",
    )

    assert instance1 == instance2, "Should be equal"
    assert instance1 != instance3, "Different instance_id"
    assert instance1 != instance4, "Different instance_type"
    assert instance1 != 42, "Not an Instance object"


@pytest.mark.unit
def test_itemToInstance(example_instance):
    """Test the item_to_instance() function."""
    # Test with a valid item
    item = example_instance.to_item()
    instance = item_to_instance(item)
    assert instance == example_instance

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_instance({"PK": {"S": "INSTANCE#id"}, "SK": {"S": "INSTANCE"}})

    # Test with invalid item format
    with pytest.raises(ValueError, match="Error converting item to Instance"):
        item_to_instance(
            {
                "PK": {"S": "INSTANCE#i-09ee977b7e1673d46"},
                "SK": {"S": "INSTANCE"},
                "TYPE": {"S": "INSTANCE"},
                "instance_type": {"S": "g4dn.xlarge"},
                "gpu_count": {"N": "1"},
                "status": {"S": "running"},
                "launched_at": {"S": "2021-01-01T00:00:00"},
                "ip_address": {"S": "192.168.1.1"},
                "availability_zone": {"S": "us-east-1a"},
                "is_spot": {"INVALID_TYPE": True},  # Invalid type
                "health_status": {"S": "healthy"},
            }
        )
