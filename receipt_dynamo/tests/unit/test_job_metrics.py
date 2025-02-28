from datetime import datetime
import pytest
from receipt_dynamo import JobMetric, itemToJobMetric


@pytest.fixture
def example_job_metric():
    """Provides a sample JobMetric for testing."""
    return JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        0.15,
        "2021-01-01T12:30:45",
        "dimensionless",
        step=100,
        epoch=2
    )


@pytest.fixture
def example_job_metric_minimal():
    """Provides a minimal sample JobMetric for testing."""
    return JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "accuracy",
        "87.5%",
        datetime.fromisoformat("2021-01-01T12:35:45")
    )


@pytest.mark.unit
def test_job_metric_init_valid(example_job_metric):
    """Test the JobMetric constructor with valid parameters."""
    assert example_job_metric.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_metric.metric_name == "loss"
    assert example_job_metric.value == 0.15
    assert example_job_metric.timestamp == "2021-01-01T12:30:45"
    assert example_job_metric.unit == "dimensionless"
    assert example_job_metric.step == 100
    assert example_job_metric.epoch == 2


@pytest.mark.unit
def test_job_metric_init_minimal(example_job_metric_minimal):
    """Test the JobMetric constructor with minimal parameters."""
    assert example_job_metric_minimal.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_metric_minimal.metric_name == "accuracy"
    assert example_job_metric_minimal.value == "87.5%"
    assert example_job_metric_minimal.timestamp == "2021-01-01T12:35:45"
    assert example_job_metric_minimal.unit is None
    assert example_job_metric_minimal.step is None
    assert example_job_metric_minimal.epoch is None


@pytest.mark.unit
def test_job_metric_init_invalid_id():
    """Test the JobMetric constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobMetric(
            1,  # Invalid: should be a string
            "loss",
            0.15,
            "2021-01-01T12:30:45"
        )
    
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobMetric(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "loss",
            0.15,
            "2021-01-01T12:30:45"
        )


@pytest.mark.unit
def test_job_metric_init_invalid_metric_name():
    """Test the JobMetric constructor with invalid metric_name."""
    with pytest.raises(ValueError, match="metric_name must be a non-empty string"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",  # Invalid: empty string
            0.15,
            "2021-01-01T12:30:45"
        )
    
    with pytest.raises(ValueError, match="metric_name must be a non-empty string"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a string
            0.15,
            "2021-01-01T12:30:45"
        )


@pytest.mark.unit
def test_job_metric_init_invalid_value():
    """Test the JobMetric constructor with invalid value."""
    with pytest.raises(ValueError, match="value must be a number or string"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            {"invalid": "type"},  # Invalid: not a number or string
            "2021-01-01T12:30:45"
        )


@pytest.mark.unit
def test_job_metric_init_invalid_timestamp():
    """Test the JobMetric constructor with invalid timestamp."""
    with pytest.raises(ValueError, match="timestamp must be a datetime object or a string"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            0.15,
            123  # Invalid: not a datetime or string
        )


@pytest.mark.unit
def test_job_metric_init_invalid_unit():
    """Test the JobMetric constructor with invalid unit."""
    with pytest.raises(ValueError, match="unit must be a string if provided"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            0.15,
            "2021-01-01T12:30:45",
            123  # Invalid: not a string
        )


@pytest.mark.unit
def test_job_metric_init_invalid_step():
    """Test the JobMetric constructor with invalid step."""
    with pytest.raises(ValueError, match="step must be a non-negative integer if provided"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            0.15,
            "2021-01-01T12:30:45",
            "dimensionless",
            -1  # Invalid: negative
        )
    
    with pytest.raises(ValueError, match="step must be a non-negative integer if provided"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            0.15,
            "2021-01-01T12:30:45",
            "dimensionless",
            "100"  # Invalid: not an integer
        )


@pytest.mark.unit
def test_job_metric_init_invalid_epoch():
    """Test the JobMetric constructor with invalid epoch."""
    with pytest.raises(ValueError, match="epoch must be a non-negative integer if provided"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            0.15,
            "2021-01-01T12:30:45",
            "dimensionless",
            100,
            -1  # Invalid: negative
        )
    
    with pytest.raises(ValueError, match="epoch must be a non-negative integer if provided"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            0.15,
            "2021-01-01T12:30:45",
            "dimensionless",
            100,
            "2"  # Invalid: not an integer
        )


@pytest.mark.unit
def test_job_metric_key(example_job_metric):
    """Test the JobMetric.key() method."""
    assert example_job_metric.key() == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"}
    }


@pytest.mark.unit
def test_job_metric_gsi1_key(example_job_metric):
    """Test the JobMetric.gsi1_key() method."""
    assert example_job_metric.gsi1_key() == {
        "GSI1PK": {"S": "METRIC"},
        "GSI1SK": {"S": "METRIC#loss"}
    }


@pytest.mark.unit
def test_job_metric_to_item(example_job_metric, example_job_metric_minimal):
    """Test the JobMetric.to_item() method."""
    # Test with full job metric
    item = example_job_metric.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "METRIC#loss#2021-01-01T12:30:45"}
    assert item["GSI1PK"] == {"S": "METRIC"}
    assert item["GSI1SK"] == {"S": "METRIC#loss"}
    assert item["TYPE"] == {"S": "JOB_METRIC"}
    assert item["job_id"] == {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["metric_name"] == {"S": "loss"}
    assert item["value"] == {"N": "0.15"}
    assert item["timestamp"] == {"S": "2021-01-01T12:30:45"}
    assert item["unit"] == {"S": "dimensionless"}
    assert item["step"] == {"N": "100"}
    assert item["epoch"] == {"N": "2"}
    
    # Test with minimal job metric and string value
    item = example_job_metric_minimal.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "METRIC#accuracy#2021-01-01T12:35:45"}
    assert item["value"] == {"S": "87.5%"}
    assert "unit" not in item
    assert "step" not in item
    assert "epoch" not in item


@pytest.mark.unit
def test_job_metric_repr(example_job_metric):
    """Test the JobMetric.__repr__() method."""
    repr_str = repr(example_job_metric)
    assert "job_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "metric_name='loss'" in repr_str
    assert "value=0.15" in repr_str
    assert "timestamp='2021-01-01T12:30:45'" in repr_str
    assert "unit='dimensionless'" in repr_str
    assert "step=100" in repr_str
    assert "epoch=2" in repr_str


@pytest.mark.unit
def test_job_metric_iter(example_job_metric):
    """Test the JobMetric.__iter__() method."""
    job_metric_dict = dict(example_job_metric)
    assert job_metric_dict["job_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert job_metric_dict["metric_name"] == "loss"
    assert job_metric_dict["value"] == 0.15
    assert job_metric_dict["timestamp"] == "2021-01-01T12:30:45"
    assert job_metric_dict["unit"] == "dimensionless"
    assert job_metric_dict["step"] == 100
    assert job_metric_dict["epoch"] == 2


@pytest.mark.unit
def test_job_metric_eq():
    """Test the JobMetric.__eq__() method."""
    # Create multiple JobMetric objects for comparison
    metric1 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T12:30:45", "dimensionless", 100, 2)
    metric2 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T12:30:45", "dimensionless", 100, 2)
    metric3 = JobMetric("4f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T12:30:45", "dimensionless", 100, 2)  # Different job_id
    metric4 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "accuracy", 0.15, "2021-01-01T12:30:45", "dimensionless", 100, 2)  # Different metric_name
    metric5 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.25, "2021-01-01T12:30:45", "dimensionless", 100, 2)  # Different value
    metric6 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T13:30:45", "dimensionless", 100, 2)  # Different timestamp
    metric7 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T12:30:45", "percent", 100, 2)  # Different unit
    metric8 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T12:30:45", "dimensionless", 150, 2)  # Different step
    metric9 = JobMetric("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "loss", 0.15, "2021-01-01T12:30:45", "dimensionless", 100, 3)  # Different epoch
    
    assert metric1 == metric2, "Should be equal"
    assert metric1 != metric3, "Different job_id"
    assert metric1 != metric4, "Different metric_name"
    assert metric1 != metric5, "Different value"
    assert metric1 != metric6, "Different timestamp"
    assert metric1 != metric7, "Different unit"
    assert metric1 != metric8, "Different step"
    assert metric1 != metric9, "Different epoch"
    
    # Compare with non-JobMetric object
    assert metric1 != 42, "Not a JobMetric object"


@pytest.mark.unit
def test_itemToJobMetric(example_job_metric, example_job_metric_minimal):
    """Test the itemToJobMetric() function."""
    # Test with full job metric
    item = example_job_metric.to_item()
    metric = itemToJobMetric(item)
    assert metric == example_job_metric
    
    # Test with minimal job metric
    item = example_job_metric_minimal.to_item()
    metric = itemToJobMetric(item)
    assert metric == example_job_metric_minimal
    
    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        itemToJobMetric({
            "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
            "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"}
        })
    
    # Test with invalid item format
    with pytest.raises(ValueError, match="Error converting item to JobMetric"):
        itemToJobMetric({
            "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
            "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"},
            "TYPE": {"S": "JOB_METRIC"},
            "job_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
            "metric_name": {"S": "loss"},
            "value": {"INVALID_TYPE": "0.15"},  # Invalid type
            "timestamp": {"S": "2021-01-01T12:30:45"}
        })


@pytest.mark.unit
def test_different_value_types():
    """Test the JobMetric handling of different value types."""
    # Test with integer value
    metric_int = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "count",
        42,
        "2021-01-01T12:30:45"
    )
    assert metric_int.value == 42
    item_int = metric_int.to_item()
    assert item_int["value"] == {"N": "42"}
    
    # Test with float value
    metric_float = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        0.15,
        "2021-01-01T12:30:45"
    )
    assert metric_float.value == 0.15
    item_float = metric_float.to_item()
    assert item_float["value"] == {"N": "0.15"}
    
    # Test with string value
    metric_string = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "status",
        "running",
        "2021-01-01T12:30:45"
    )
    assert metric_string.value == "running"
    item_string = metric_string.to_item()
    assert item_string["value"] == {"S": "running"}
    
    # Test conversion back to JobMetric
    assert itemToJobMetric(item_int).value == 42
    assert itemToJobMetric(item_float).value == 0.15
    assert itemToJobMetric(item_string).value == "running" 