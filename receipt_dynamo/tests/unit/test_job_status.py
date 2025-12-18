import pytest

from receipt_dynamo import JobStatus, item_to_job_status


@pytest.fixture
def example_job_status():
    """Provides a sample JobStatus for testing."""
    return JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        progress=75.5,
        message="Training in progress",
        updated_by="user123",
        instance_id="i-abc123def456",
    )


@pytest.fixture
def example_job_status_minimal():
    """Provides a minimal sample JobStatus for testing."""
    return JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "pending",
        "2021-01-01T00:00:00",
    )


@pytest.mark.unit
def test_job_status_init_valid(example_job_status):
    """Test the JobStatus constructor with valid parameters."""
    assert example_job_status.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_status.status == "running"
    assert example_job_status.updated_at == "2021-01-01T00:00:00"
    assert example_job_status.progress == 75.5
    assert example_job_status.message == "Training in progress"
    assert example_job_status.updated_by == "user123"
    assert example_job_status.instance_id == "i-abc123def456"


@pytest.mark.unit
def test_job_status_init_minimal(example_job_status_minimal):
    """Test the JobStatus constructor with minimal parameters."""
    assert example_job_status_minimal.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job_status_minimal.status == "pending"
    assert example_job_status_minimal.updated_at == "2021-01-01T00:00:00"
    assert example_job_status_minimal.progress is None
    assert example_job_status_minimal.message is None
    assert example_job_status_minimal.updated_by is None
    assert example_job_status_minimal.instance_id is None


@pytest.mark.unit
def test_job_status_init_invalid_id():
    """Test the JobStatus constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        # Invalid: should be a string
        JobStatus(1, "running", "2021-01-01T00:00:00")

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobStatus(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "running",
            "2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_job_status_init_invalid_status():
    """Test the JobStatus constructor with invalid status."""
    with pytest.raises(ValueError, match="status must be one of"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "invalid_status",  # Invalid: not a valid status
            "2021-01-01T00:00:00",
        )

    with pytest.raises(ValueError, match="status must be one of"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a string
            "2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_job_status_init_invalid_updated_at():
    """Test the JobStatus constructor with invalid updated_at."""
    with pytest.raises(
        ValueError, match="updated_at must be a datetime object or a string"
    ):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            123,
        )


@pytest.mark.unit
def test_job_status_init_invalid_progress():
    """Test the JobStatus constructor with invalid progress."""
    with pytest.raises(ValueError, match="progress must be a number between 0 and 100"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            "2021-01-01T00:00:00",
            progress=-10,
        )

    with pytest.raises(ValueError, match="progress must be a number between 0 and 100"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            "2021-01-01T00:00:00",
            progress=101,
        )

    with pytest.raises(ValueError, match="progress must be a number between 0 and 100"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            "2021-01-01T00:00:00",
            progress="50",
        )


@pytest.mark.unit
def test_job_status_init_invalid_message():
    """Test the JobStatus constructor with invalid message."""
    with pytest.raises(ValueError, match="message must be a string"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            "2021-01-01T00:00:00",
            message=123,
        )


@pytest.mark.unit
def test_job_status_init_invalid_updated_by():
    """Test the JobStatus constructor with invalid updated_by."""
    with pytest.raises(ValueError, match="updated_by must be a string"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            "2021-01-01T00:00:00",
            updated_by=123,
        )


@pytest.mark.unit
def test_job_status_init_invalid_instance_id():
    """Test the JobStatus constructor with invalid instance_id."""
    with pytest.raises(ValueError, match="instance_id must be a string"):
        JobStatus(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "running",
            "2021-01-01T00:00:00",
            instance_id=123,
        )


@pytest.mark.unit
def test_job_status_key(example_job_status):
    """Test the JobStatus.key method."""
    assert example_job_status.key == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "STATUS#2021-01-01T00:00:00"},
    }


@pytest.mark.unit
def test_job_status_gsi1_key(example_job_status):
    """Test the JobStatus.gsi1_key() method."""
    assert example_job_status.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#running"},
        "GSI1SK": {"S": "UPDATED#2021-01-01T00:00:00"},
    }


@pytest.mark.unit
def test_job_status_to_item(example_job_status, example_job_status_minimal):
    """Test the JobStatus.to_item() method."""
    # Test with full job status
    item = example_job_status.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "STATUS#2021-01-01T00:00:00"}
    assert item["GSI1PK"] == {"S": "STATUS#running"}
    assert item["GSI1SK"] == {"S": "UPDATED#2021-01-01T00:00:00"}
    assert item["TYPE"] == {"S": "JOB_STATUS"}
    assert item["status"] == {"S": "running"}
    assert item["updated_at"] == {"S": "2021-01-01T00:00:00"}
    assert item["progress"] == {"N": "75.5"}
    assert item["message"] == {"S": "Training in progress"}
    assert item["updated_by"] == {"S": "user123"}
    assert item["instance_id"] == {"S": "i-abc123def456"}

    # Test minimal job status
    item = example_job_status_minimal.to_item()
    assert "progress" not in item
    assert "message" not in item
    assert "updated_by" not in item
    assert "instance_id" not in item
    assert item["status"] == {"S": "pending"}


@pytest.mark.unit
def test_job_status_repr(example_job_status):
    """Test the JobStatus.__repr__() method."""
    repr_str = repr(example_job_status)
    assert "job_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "status='running'" in repr_str
    assert "updated_at='2021-01-01T00:00:00'" in repr_str
    assert "progress=75.5" in repr_str
    assert "message='Training in progress'" in repr_str
    assert "updated_by='user123'" in repr_str
    assert "instance_id='i-abc123def456'" in repr_str


@pytest.mark.unit
def test_job_status_iter(example_job_status):
    """Test the JobStatus.__iter__() method."""
    job_status_dict = dict(example_job_status)
    assert job_status_dict["job_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert job_status_dict["status"] == "running"
    assert job_status_dict["updated_at"] == "2021-01-01T00:00:00"
    assert job_status_dict["progress"] == 75.5
    assert job_status_dict["message"] == "Training in progress"
    assert job_status_dict["updated_by"] == "user123"
    assert job_status_dict["instance_id"] == "i-abc123def456"


@pytest.mark.unit
def test_job_status_eq():
    """Test the JobStatus.__eq__() method."""

    js1 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        75.5,
        "Training in progress",
        "user123",
        "i-abc123def456",
    )
    js2 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        75.5,
        "Training in progress",
        "user123",
        "i-abc123def456",
    )
    js3 = JobStatus(
        "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        75.5,
        "Training in progress",
        "user123",
        "i-abc123def456",
    )  # Different job_id
    js4 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "pending",
        "2021-01-01T00:00:00",
        75.5,
        "Training in progress",
        "user123",
        "i-abc123def456",
    )  # Different status
    js5 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-02T00:00:00",
        75.5,
        "Training in progress",
        "user123",
        "i-abc123def456",
    )  # Different updated_at
    js6 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        50.0,
        "Training in progress",
        "user123",
        "i-abc123def456",
    )  # Different progress
    js7 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        75.5,
        "Different message",
        "user123",
        "i-abc123def456",
    )  # Different message
    js8 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        75.5,
        "Training in progress",
        "different_user",
        "i-abc123def456",
    )  # Different updated_by
    js9 = JobStatus(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "running",
        "2021-01-01T00:00:00",
        75.5,
        "Training in progress",
        "user123",
        "i-different",
    )  # Different instance_id

    assert js1 == js2, "Should be equal"
    assert js1 != js3, "Different job_id"
    assert js1 != js4, "Different status"
    assert js1 != js5, "Different updated_at"
    assert js1 != js6, "Different progress"
    assert js1 != js7, "Different message"
    assert js1 != js8, "Different updated_by"
    assert js1 != js9, "Different instance_id"

    # Compare with non-JobStatus object
    assert js1 != 42, "Not a JobStatus object"


@pytest.mark.unit
def test_itemToJobStatus(example_job_status, example_job_status_minimal):
    """Test the item_to_job_status() function."""
    # Test with full job status
    item = example_job_status.to_item()
    job_status = item_to_job_status(item)
    assert job_status == example_job_status

    # Test with minimal job status
    item = example_job_status_minimal.to_item()
    job_status = item_to_job_status(item)
    assert job_status == example_job_status_minimal

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_job_status({"PK": {"S": "JOB#id"}, "SK": {"S": "STATUS#timestamp"}})

    # Test with invalid item format
    with pytest.raises(ValueError, match="Error converting item to JobStatus"):
        item_to_job_status(
            {
                "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "STATUS#2021-01-01T00:00:00"},
                "TYPE": {"S": "JOB_STATUS"},
                "status": {"INVALID_TYPE": "running"},  # Invalid type
                "updated_at": {"S": "2021-01-01T00:00:00"},
            }
        )
