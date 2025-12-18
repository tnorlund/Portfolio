from datetime import datetime

import pytest

from receipt_dynamo import JobMetric, item_to_job_metric


@pytest.fixture
def example_job_metric():
    """Provides a sample JobMetric for testing."""
    return JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )


@pytest.fixture
def example_job_metric_minimal():
    """Provides a minimal sample JobMetric for testing."""
    return JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "accuracy",
        datetime.fromisoformat("2021-01-01T12:35:45"),
        87.5,
    )


@pytest.mark.unit
def test_job_metric_init_valid(example_job_metric):
    """Test the JobMetric constructor with valid parameters."""
    assert example_job_metric.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_metric.metric_name == "loss"
    assert example_job_metric.timestamp == "2021-01-01T12:30:45"
    assert example_job_metric.value == 0.15
    assert example_job_metric.unit == "dimensionless"
    assert example_job_metric.step == 100
    assert example_job_metric.epoch == 2


@pytest.mark.unit
def test_job_metric_init_minimal(example_job_metric_minimal):
    """Test the JobMetric constructor with minimal parameters."""
    assert example_job_metric_minimal.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_metric_minimal.metric_name == "accuracy"
    assert example_job_metric_minimal.timestamp == "2021-01-01T12:35:45"
    assert example_job_metric_minimal.value == 87.5
    assert example_job_metric_minimal.unit is None
    assert example_job_metric_minimal.step is None
    assert example_job_metric_minimal.epoch is None


@pytest.mark.unit
def test_job_metric_init_invalid_id():
    """Test the JobMetric constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        # Invalid: should be a string
        JobMetric(1, "loss", "2021-01-01T12:30:45", 0.15)

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobMetric(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "loss",
            "2021-01-01T12:30:45",
            0.15,
        )


@pytest.mark.unit
def test_job_metric_init_invalid_metric_name():
    """Test the JobMetric constructor with invalid metric_name."""
    with pytest.raises(ValueError, match="metric_name must be a non-empty string"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",  # Invalid: empty string
            "2021-01-01T12:30:45",
            0.15,
        )

    with pytest.raises(ValueError, match="metric_name must be str, got int"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a string
            "2021-01-01T12:30:45",
            0.15,
        )


@pytest.mark.unit
def test_job_metric_init_invalid_value():
    """Test the JobMetric constructor with invalid value."""
    with pytest.raises(
        ValueError,
        match="value must be a number \\(int/float\\) or a dictionary",
    ):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            "2021-01-01T12:30:45",
            "not-a-number",
        )  # Invalid: not convertible to float


@pytest.mark.unit
def test_job_metric_init_invalid_timestamp():
    """Test the JobMetric constructor with invalid timestamp."""
    with pytest.raises(ValueError, match="timestamp must be datetime, str, got"):
        JobMetric(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "loss",
            123,  # Invalid: not a datetime or string
            0.15,
        )


@pytest.mark.unit
def test_job_metric_init_invalid_unit():
    """Test the JobMetric constructor with invalid unit."""
    metric = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit=123,
    )  # Non-string unit
    assert metric.unit == 123


@pytest.mark.unit
def test_job_metric_init_invalid_step():
    """Test the JobMetric constructor with invalid step."""
    metric = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        step=-1,
    )  # Negative step
    assert metric.step == -1


@pytest.mark.unit
def test_job_metric_init_invalid_epoch():
    """Test the JobMetric constructor with invalid epoch."""
    metric = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        epoch=-1,
    )  # Negative epoch
    assert metric.epoch == -1


@pytest.mark.unit
def test_job_metric_init_invalid_metadata():
    """Test the JobMetric constructor with invalid metadata."""
    # This test is no longer needed since we don't have metadata


@pytest.mark.unit
def test_job_metric_key(example_job_metric):
    """Test the JobMetric.key method."""
    assert example_job_metric.key == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"},
    }


@pytest.mark.unit
def test_job_metric_gsi1_key(example_job_metric):
    """
    Test the gsi1_key method of the JobMetric class.
    """
    gsi1_key = example_job_metric.gsi1_key()
    assert isinstance(gsi1_key, dict)
    assert "GSI1PK" in gsi1_key
    assert "GSI1SK" in gsi1_key
    assert gsi1_key["GSI1PK"]["S"] == f"METRIC#{example_job_metric.metric_name}"
    assert gsi1_key["GSI1SK"]["S"] == f"{example_job_metric.timestamp}"


@pytest.mark.unit
def test_job_metric_to_item(example_job_metric, example_job_metric_minimal):
    """Test the JobMetric.to_item() method."""
    # Test with full job metric
    item = example_job_metric.to_item()
    assert item["PK"] == {"S": f"JOB#{example_job_metric.job_id}"}
    assert item["SK"] == {
        "S": f"METRIC#{example_job_metric.metric_name}#"
        f"{example_job_metric.timestamp}"
    }
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    assert item["job_id"] == {"S": example_job_metric.job_id}
    assert item["metric_name"] == {"S": example_job_metric.metric_name}
    assert item["timestamp"] == {"S": example_job_metric.timestamp}
    assert item["value"] == {"N": "0.15"}
    assert item["unit"] == {"S": "dimensionless"}
    assert item["step"] == {"N": "100"}
    assert item["epoch"] == {"N": "2"}
    assert item["TYPE"] == {"S": "JOB_METRIC"}

    # Test with minimal job metric and numeric value
    item = example_job_metric_minimal.to_item()
    assert item["PK"] == {"S": f"JOB#{example_job_metric_minimal.job_id}"}
    assert item["SK"] == {
        "S": f"METRIC#{example_job_metric_minimal.metric_name}#"
        f"{example_job_metric_minimal.timestamp}"
    }
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    assert item["value"] == {"N": "87.5"}
    assert "unit" not in item
    assert "step" not in item
    assert "epoch" not in item
    assert item["TYPE"] == {"S": "JOB_METRIC"}


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
    metric1 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )
    metric2 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )
    metric3 = JobMetric(
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )  # Different job_id
    metric4 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "accuracy",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )  # Different metric_name
    metric5 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.25,
        unit="dimensionless",
        step=100,
        epoch=2,
    )  # Different value
    metric6 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T13:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )  # Different timestamp
    metric7 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="percent",
        step=100,
        epoch=2,
    )  # Different unit
    metric8 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=150,
        epoch=2,
    )  # Different step
    metric9 = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "loss",
        "2021-01-01T12:30:45",
        0.15,
        unit="dimensionless",
        step=100,
        epoch=3,
    )  # Different epoch

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
    """Test the item_to_job_metric() function."""
    # Test with full job metric
    item = example_job_metric.to_item()
    metric = item_to_job_metric(item)
    assert metric == example_job_metric

    # Test with minimal job metric
    item = example_job_metric_minimal.to_item()
    metric = item_to_job_metric(item)
    assert metric == example_job_metric_minimal

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_job_metric(
            {
                "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"},
            }
        )

    # Test with invalid item format
    with pytest.raises(ValueError, match="Error parsing item"):
        item_to_job_metric(
            {
                "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"},
                "TYPE": {"S": "JOB_METRIC"},
                "job_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "metric_name": {"S": "loss"},
                "value": {"INVALID_TYPE": "0.15"},  # Invalid type
                "timestamp": {"S": "2021-01-01T12:30:45"},
            }
        )


@pytest.mark.unit
def test_different_value_types():
    """Test the JobMetric handling of different value types."""
    # Test with integer value
    metric_int = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "count",
        "2021-01-01T12:30:45",
        42,
    )
    assert metric_int.value == 42

    # Test with float value
    metric_float = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "accuracy",
        "2021-01-01T12:30:45",
        0.95,
    )
    assert metric_float.value == 0.95

    # Test with dictionary value
    metric_dict = JobMetric(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "embeddings",
        "2021-01-01T12:30:45",
        {"vector": [0.1, 0.2, 0.3]},
    )
    assert metric_dict.value == {"vector": [0.1, 0.2, 0.3]}


@pytest.mark.unit
def test_from_item():
    """Test the item_to_job_metric function."""
    # Test with a complete item
    item = {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "METRIC#loss#2021-01-01T12:30:45"},
        "GSI1PK": {"S": "METRIC"},
        "GSI1SK": {"S": "METRIC#loss"},
        "job_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "metric_name": {"S": "loss"},
        "timestamp": {"S": "2021-01-01T12:30:45"},
        "value": {"N": "0.15"},
        "unit": {"S": "dimensionless"},
        "step": {"N": "100"},
        "epoch": {"N": "2"},
        "TYPE": {"S": "JOB_METRIC"},
    }

    # First test that we can convert the item to a metric
    try:
        job_metric = item_to_job_metric(item)
        assert job_metric.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
        assert job_metric.metric_name == "loss"
        assert job_metric.timestamp == "2021-01-01T12:30:45"
        assert job_metric.value == 0.15
        assert job_metric.unit == "dimensionless"
        assert job_metric.step == 100
        assert job_metric.epoch == 2
    except ValueError as e:
        # If the test fails, print the actual implementation of item_to_job_metric
        import inspect

        print("item_to_job_metric implementation:")
        print(inspect.getsource(item_to_job_metric))
        raise e

    # Test with minimal item (no unit, step, or epoch)
    item = {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "METRIC#accuracy#2021-01-01T12:35:45"},
        "GSI1PK": {"S": "METRIC"},
        "GSI1SK": {"S": "METRIC#accuracy"},
        "job_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "metric_name": {"S": "accuracy"},
        "timestamp": {"S": "2021-01-01T12:35:45"},
        "value": {"N": "87.5"},
        "TYPE": {"S": "JOB_METRIC"},
    }

    try:
        job_metric = item_to_job_metric(item)
        assert job_metric.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
        assert job_metric.metric_name == "accuracy"
        assert job_metric.timestamp == "2021-01-01T12:35:45"
        assert job_metric.value == 87.5
        assert job_metric.unit is None
        assert job_metric.step is None
        assert job_metric.epoch is None
    except ValueError as e:
        # If the test fails, print the actual implementation of item_to_job_metric
        import inspect

        print("item_to_job_metric implementation:")
        print(inspect.getsource(item_to_job_metric))
        raise e
