from datetime import datetime

import pytest

from receipt_dynamo.entities.job_log import JobLog, item_to_job_log


@pytest.fixture
def example_job_log():
    """Provides a sample JobLog for testing."""
    return JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # job_id
        "2021-01-01T12:30:45",  # timestamp
        "INFO",  # log_level
        "This is a test log message",  # message
        "test_component",  # source
        "Sample exception traceback",  # exception
    )


@pytest.fixture
def example_job_log_minimal():
    """Provides a minimal sample JobLog for testing."""
    return JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # job_id
        "2021-01-01T12:30:45",  # timestamp
        "INFO",  # log_level
        "This is a test log message",  # message
    )


@pytest.mark.unit
def test_job_log_init_valid(example_job_log, example_job_log_minimal):
    """Test the JobLog constructor with valid parameters."""
    # Test full job log
    assert example_job_log.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_log.timestamp == "2021-01-01T12:30:45"
    assert example_job_log.log_level == "INFO"
    assert example_job_log.message == "This is a test log message"
    assert example_job_log.source == "test_component"
    assert example_job_log.exception == "Sample exception traceback"

    # Test minimal job log
    assert example_job_log_minimal.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_log_minimal.timestamp == "2021-01-01T12:30:45"
    assert example_job_log_minimal.log_level == "INFO"
    assert example_job_log_minimal.message == "This is a test log message"
    assert example_job_log_minimal.source is None
    assert example_job_log_minimal.exception is None


@pytest.mark.unit
def test_job_log_init_with_datetime():
    """Test the JobLog constructor with datetime for timestamp."""
    job_log = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        datetime(2021, 1, 1, 12, 30, 45),
        "INFO",
        "Test message",
    )
    assert job_log.timestamp == "2021-01-01T12:30:45"


@pytest.mark.unit
def test_job_log_init_invalid_id():
    """Test the JobLog constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobLog(
            1,  # Invalid: should be a string
            "2021-01-01T12:30:45",
            "INFO",
            "Test message",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobLog(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "2021-01-01T12:30:45",
            "INFO",
            "Test message",
        )


@pytest.mark.unit
def test_job_log_init_invalid_timestamp():
    """Test the JobLog constructor with invalid timestamp."""
    with pytest.raises(
        ValueError, match="timestamp must be a datetime object or a string"
    ):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a datetime or string
            "INFO",
            "Test message",
        )


@pytest.mark.unit
def test_job_log_init_invalid_log_level():
    """Test the JobLog constructor with invalid log_level."""
    with pytest.raises(ValueError, match="log_level must be one of"):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T12:30:45",
            "INVALID_LEVEL",  # Invalid: not a valid log level
            "Test message",
        )

    with pytest.raises(ValueError, match="log_level must be one of"):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T12:30:45",
            123,  # Invalid: not a string
            "Test message",
        )


@pytest.mark.unit
def test_job_log_init_invalid_message():
    """Test the JobLog constructor with invalid message."""
    with pytest.raises(ValueError, match="message must be a non-empty string"):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T12:30:45",
            "INFO",
            "",  # Invalid: empty string
        )

    with pytest.raises(ValueError, match="message must be a non-empty string"):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T12:30:45",
            "INFO",
            123,  # Invalid: not a string
        )


@pytest.mark.unit
def test_job_log_init_invalid_source():
    """Test the JobLog constructor with invalid source."""
    with pytest.raises(ValueError, match="source must be a string"):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T12:30:45",
            "INFO",
            "Test message",
            123,  # Invalid: not a string
        )


@pytest.mark.unit
def test_job_log_init_invalid_exception():
    """Test the JobLog constructor with invalid exception."""
    with pytest.raises(ValueError, match="exception must be a string"):
        JobLog(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2021-01-01T12:30:45",
            "INFO",
            "Test message",
            "test_component",
            123,  # Invalid: not a string
        )


@pytest.mark.unit
def test_job_log_key(example_job_log):
    """Test the JobLog.key method."""
    assert example_job_log.key == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LOG#2021-01-01T12:30:45"},
    }


@pytest.mark.unit
def test_job_log_gsi1_key(example_job_log):
    """Test the JobLog.gsi1_key() method."""
    assert example_job_log.gsi1_key() == {
        "GSI1PK": {"S": "LOG"},
        "GSI1SK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3#2021-01-01T12:30:45"},
    }


@pytest.mark.unit
def test_job_log_to_item(example_job_log, example_job_log_minimal):
    """Test the JobLog.to_item() method."""
    # Test with full job log
    item = example_job_log.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "LOG#2021-01-01T12:30:45"}
    assert item["GSI1PK"] == {"S": "LOG"}
    assert item["GSI1SK"] == {
        "S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3#2021-01-01T12:30:45"
    }
    assert item["TYPE"] == {"S": "JOB_LOG"}
    assert item["log_level"] == {"S": "INFO"}
    assert item["message"] == {"S": "This is a test log message"}
    assert item["source"] == {"S": "test_component"}
    assert item["exception"] == {"S": "Sample exception traceback"}

    # Test with minimal job log
    item = example_job_log_minimal.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "LOG#2021-01-01T12:30:45"}
    assert item["log_level"] == {"S": "INFO"}
    assert item["message"] == {"S": "This is a test log message"}
    assert "source" not in item
    assert "exception" not in item


@pytest.mark.unit
def test_job_log_case_insensitive_log_level():
    """Test that log level is converted to uppercase."""
    job_log = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "info",  # lowercase
        "Test message",
    )
    assert job_log.log_level == "INFO"  # Should be converted to uppercase


@pytest.mark.unit
def test_job_log_repr(example_job_log):
    """Test the JobLog.__repr__() method."""
    repr_str = repr(example_job_log)
    assert "job_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "timestamp='2021-01-01T12:30:45'" in repr_str
    assert "log_level='INFO'" in repr_str
    assert "message='This is a test log message'" in repr_str
    assert "source='test_component'" in repr_str
    assert "exception='Sample exception traceback'" in repr_str


@pytest.mark.unit
def test_job_log_iter(example_job_log):
    """Test the JobLog.__iter__() method."""
    job_log_dict = dict(example_job_log)
    assert job_log_dict["job_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert job_log_dict["timestamp"] == "2021-01-01T12:30:45"
    assert job_log_dict["log_level"] == "INFO"
    assert job_log_dict["message"] == "This is a test log message"
    assert job_log_dict["source"] == "test_component"
    assert job_log_dict["exception"] == "Sample exception traceback"


@pytest.mark.unit
def test_job_log_eq():
    """Test the JobLog.__eq__() method."""
    # Same job logs
    job_log1 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "INFO",
        "Test message",
        "source",
        "exception",
    )
    job_log2 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "INFO",
        "Test message",
        "source",
        "exception",
    )
    assert job_log1 == job_log2, "Should be equal"

    # Different job_id
    job_log3 = JobLog(
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "INFO",
        "Test message",
        "source",
        "exception",
    )
    assert job_log1 != job_log3, "Different job_id"

    # Different timestamp
    job_log4 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:31:45",
        "INFO",
        "Test message",
        "source",
        "exception",
    )
    assert job_log1 != job_log4, "Different timestamp"

    # Different log_level
    job_log5 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "ERROR",
        "Test message",
        "source",
        "exception",
    )
    assert job_log1 != job_log5, "Different log_level"

    # Different message
    job_log6 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "INFO",
        "Different message",
        "source",
        "exception",
    )
    assert job_log1 != job_log6, "Different message"

    # Different source
    job_log7 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "INFO",
        "Test message",
        "different_source",
        "exception",
    )
    assert job_log1 != job_log7, "Different source"

    # Different exception
    job_log8 = JobLog(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "2021-01-01T12:30:45",
        "INFO",
        "Test message",
        "source",
        "different_exception",
    )
    assert job_log1 != job_log8, "Different exception"

    # Compare with non-JobLog object
    assert job_log1 != 42, "Not a JobLog object"


@pytest.mark.unit
def test_itemToJobLog(example_job_log, example_job_log_minimal):
    """Test the item_to_job_log() function."""
    # Test with full job log
    item = example_job_log.to_item()
    job_log = item_to_job_log(item)
    assert job_log == example_job_log

    # Test with minimal job log
    item = example_job_log_minimal.to_item()
    job_log = item_to_job_log(item)
    assert job_log == example_job_log_minimal

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_job_log({"PK": {"S": "JOB#id"}, "SK": {"S": "LOG#timestamp"}})

    # Test with invalid item format
    with pytest.raises(ValueError, match="Error converting item to JobLog"):
        item_to_job_log(
            {
                "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "LOG#2021-01-01T12:30:45"},
                "TYPE": {"S": "JOB_LOG"},
                "log_level": {"INVALID_TYPE": "INFO"},  # Invalid type
                "message": {"S": "Test message"},
            }
        )
