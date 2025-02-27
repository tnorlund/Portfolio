from datetime import datetime
import pytest
from receipt_dynamo import Job, itemToJob
from receipt_dynamo.entities.job import _parse_dynamodb_map


@pytest.fixture
def example_job():
    """Provides a sample Job for testing."""
    # fmt: off
    return Job(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 
        "Training Job",
        "Example training job description",
        "2021-01-01T00:00:00",
        "user123",
        "pending",
        "medium",
        {"model": "layoutlm", "batch_size": 32, "epochs": 10},
        estimated_duration=3600,
        tags={"project": "receipts", "environment": "dev"}
    )
    # fmt: on


@pytest.fixture
def example_job_minimal():
    """Provides a minimal sample Job for testing."""
    # fmt: off
    return Job(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 
        "Training Job",
        "Example training job description",
        "2021-01-01T00:00:00",
        "user123",
        "pending",
        "medium",
        {"model": "layoutlm"}
    )
    # fmt: on


@pytest.mark.unit
def test_job_init_valid(example_job):
    """Test the Job constructor with valid parameters."""
    assert example_job.job_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_job.name == "Training Job"
    assert example_job.description == "Example training job description"
    assert example_job.created_at == "2021-01-01T00:00:00"
    assert example_job.created_by == "user123"
    assert example_job.status == "pending"
    assert example_job.priority == "medium"
    assert example_job.job_config == {"model": "layoutlm", "batch_size": 32, "epochs": 10}
    assert example_job.estimated_duration == 3600
    assert example_job.tags == {"project": "receipts", "environment": "dev"}


@pytest.mark.unit
def test_job_init_invalid_id():
    """Test the Job constructor with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Job(
            1,  # Invalid: should be a string
            "Training Job", 
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"}
        )
    
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Job(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "Training Job", 
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_name():
    """Test the Job constructor with invalid name."""
    with pytest.raises(ValueError, match="name must be a non-empty string"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",  # Invalid: empty string
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"}
        )
    
    with pytest.raises(ValueError, match="name must be a non-empty string"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,  # Invalid: not a string
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_description():
    """Test the Job constructor with invalid description."""
    with pytest.raises(ValueError, match="description must be a string"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            123,  # Invalid: not a string
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_created_at():
    """Test the Job constructor with invalid created_at."""
    with pytest.raises(ValueError, match="created_at must be a datetime object or a string"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            123,  # Invalid: not a datetime or string
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_created_by():
    """Test the Job constructor with invalid created_by."""
    with pytest.raises(ValueError, match="created_by must be a non-empty string"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "",  # Invalid: empty string
            "pending",
            "medium",
            {"model": "layoutlm"}
        )
    
    with pytest.raises(ValueError, match="created_by must be a non-empty string"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            123,  # Invalid: not a string
            "pending",
            "medium",
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_status():
    """Test the Job constructor with invalid status."""
    with pytest.raises(ValueError, match="status must be one of"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "invalid_status",  # Invalid: not a valid status
            "medium",
            {"model": "layoutlm"}
        )
    
    with pytest.raises(ValueError, match="status must be one of"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            123,  # Invalid: not a string
            "medium",
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_priority():
    """Test the Job constructor with invalid priority."""
    with pytest.raises(ValueError, match="priority must be one of"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "invalid_priority",  # Invalid: not a valid priority
            {"model": "layoutlm"}
        )
    
    with pytest.raises(ValueError, match="priority must be one of"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            123,  # Invalid: not a string
            {"model": "layoutlm"}
        )


@pytest.mark.unit
def test_job_init_invalid_job_config():
    """Test the Job constructor with invalid job_config."""
    with pytest.raises(ValueError, match="job_config must be a dictionary"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            "not_a_dict"  # Invalid: not a dictionary
        )


@pytest.mark.unit
def test_job_init_invalid_estimated_duration():
    """Test the Job constructor with invalid estimated_duration."""
    with pytest.raises(ValueError, match="estimated_duration must be a positive integer"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"},
            estimated_duration=0  # Invalid: not positive
        )
    
    with pytest.raises(ValueError, match="estimated_duration must be a positive integer"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"},
            estimated_duration=-100  # Invalid: negative
        )
    
    with pytest.raises(ValueError, match="estimated_duration must be a positive integer"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"},
            estimated_duration="3600"  # Invalid: not an integer
        )


@pytest.mark.unit
def test_job_init_invalid_tags():
    """Test the Job constructor with invalid tags."""
    with pytest.raises(ValueError, match="tags must be a dictionary"):
        Job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "Training Job",
            "Example description",
            "2021-01-01T00:00:00",
            "user123",
            "pending",
            "medium",
            {"model": "layoutlm"},
            tags="not_a_dict"  # Invalid: not a dictionary
        )


@pytest.mark.unit
def test_job_key(example_job):
    """Test the Job.key() method."""
    assert example_job.key() == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "JOB"}
    }


@pytest.mark.unit
def test_job_gsi1_key(example_job):
    """Test the Job.gsi1_key() method."""
    assert example_job.gsi1_key() == {
        "GSI1PK": {"S": "STATUS#pending"},
        "GSI1SK": {"S": "CREATED#2021-01-01T00:00:00"}
    }


@pytest.mark.unit
def test_job_gsi2_key(example_job):
    """Test the Job.gsi2_key() method."""
    assert example_job.gsi2_key() == {
        "GSI2PK": {"S": "USER#user123"},
        "GSI2SK": {"S": "CREATED#2021-01-01T00:00:00"}
    }


@pytest.mark.unit
def test_job_to_item(example_job, example_job_minimal):
    """Test the Job.to_item() method."""
    # Test with full job
    item = example_job.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "JOB"}
    assert item["GSI1PK"] == {"S": "STATUS#pending"}
    assert item["GSI1SK"] == {"S": "CREATED#2021-01-01T00:00:00"}
    assert item["GSI2PK"] == {"S": "USER#user123"}
    assert item["GSI2SK"] == {"S": "CREATED#2021-01-01T00:00:00"}
    assert item["TYPE"] == {"S": "JOB"}
    assert item["name"] == {"S": "Training Job"}
    assert item["description"] == {"S": "Example training job description"}
    assert item["created_at"] == {"S": "2021-01-01T00:00:00"}
    assert item["created_by"] == {"S": "user123"}
    assert item["status"] == {"S": "pending"}
    assert item["priority"] == {"S": "medium"}
    assert item["job_config"]["M"]["model"] == {"S": "layoutlm"}
    assert item["job_config"]["M"]["batch_size"] == {"N": "32"}
    assert item["job_config"]["M"]["epochs"] == {"N": "10"}
    assert item["estimated_duration"] == {"N": "3600"}
    assert item["tags"]["M"]["project"] == {"S": "receipts"}
    assert item["tags"]["M"]["environment"] == {"S": "dev"}
    
    # Test minimal job
    item = example_job_minimal.to_item()
    assert "estimated_duration" not in item
    assert "tags" not in item
    assert item["job_config"]["M"]["model"] == {"S": "layoutlm"}
    assert len(item["job_config"]["M"]) == 1


@pytest.mark.unit
def test_job_repr(example_job):
    """Test the Job.__repr__() method."""
    repr_str = repr(example_job)
    assert "job_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "name='Training Job'" in repr_str
    assert "description='Example training job description'" in repr_str
    assert "created_at='2021-01-01T00:00:00'" in repr_str
    assert "created_by='user123'" in repr_str
    assert "status='pending'" in repr_str
    assert "priority='medium'" in repr_str
    assert "'model': 'layoutlm'" in repr_str
    assert "'batch_size': 32" in repr_str
    assert "'epochs': 10" in repr_str
    assert "estimated_duration=3600" in repr_str
    assert "'project': 'receipts'" in repr_str
    assert "'environment': 'dev'" in repr_str


@pytest.mark.unit
def test_job_iter(example_job):
    """Test the Job.__iter__() method."""
    job_dict = dict(example_job)
    assert job_dict["job_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert job_dict["name"] == "Training Job"
    assert job_dict["description"] == "Example training job description"
    assert job_dict["created_at"] == "2021-01-01T00:00:00"
    assert job_dict["created_by"] == "user123"
    assert job_dict["status"] == "pending"
    assert job_dict["priority"] == "medium"
    assert job_dict["job_config"] == {"model": "layoutlm", "batch_size": 32, "epochs": 10}
    assert job_dict["estimated_duration"] == 3600
    assert job_dict["tags"] == {"project": "receipts", "environment": "dev"}


@pytest.mark.unit
def test_job_eq():
    """Test the Job.__eq__() method."""
    # fmt: off
    job1 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})
    job2 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})
    job3 = Job("4f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different job_id
    job4 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Different Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different name
    job5 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Different description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different description
    job6 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-02T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different created_at
    job7 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "different_user", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different created_by
    job8 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "running", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different status
    job9 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "high", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different priority
    job10 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "different", "batch_size": 32, "epochs": 10}, 3600, {"project": "receipts", "environment": "dev"})  # Different job_config
    job11 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 7200, {"project": "receipts", "environment": "dev"})  # Different estimated_duration
    job12 = Job("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "Training Job", "Example training job description", "2021-01-01T00:00:00", "user123", "pending", "medium", {"model": "layoutlm", "batch_size": 32, "epochs": 10}, 3600, {"project": "different", "environment": "dev"})  # Different tags
    # fmt: on
    
    assert job1 == job2, "Should be equal"
    assert job1 != job3, "Different job_id"
    assert job1 != job4, "Different name"
    assert job1 != job5, "Different description"
    assert job1 != job6, "Different created_at"
    assert job1 != job7, "Different created_by"
    assert job1 != job8, "Different status"
    assert job1 != job9, "Different priority"
    assert job1 != job10, "Different job_config"
    assert job1 != job11, "Different estimated_duration"
    assert job1 != job12, "Different tags"
    
    # Compare with non-Job object
    assert job1 != 42, "Not a Job object"


@pytest.mark.unit
def test_itemToJob(example_job, example_job_minimal):
    """Test the itemToJob() function."""
    # Test with full job
    item = example_job.to_item()
    job = itemToJob(item)
    assert job == example_job
    
    # Test with minimal job
    item = example_job_minimal.to_item()
    job = itemToJob(item)
    assert job == example_job_minimal
    
    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        itemToJob({"PK": {"S": "JOB#id"}, "SK": {"S": "JOB"}})
    
    # Test with invalid item format
    with pytest.raises(ValueError, match="Error converting item to Job"):
        itemToJob({
            "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
            "SK": {"S": "JOB"},
            "TYPE": {"S": "JOB"},
            "name": {"S": "Training Job"},
            "description": {"S": "Example training job description"},
            "created_at": {"S": "2021-01-01T00:00:00"},
            "created_by": {"S": "user123"},
            "status": {"INVALID_TYPE": "pending"},  # Invalid type
            "priority": {"S": "medium"},
            "job_config": {"M": {"model": {"S": "layoutlm"}}}
        })


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
        "nested_map": {"M": {
            "inner_string": {"S": "inner_value"},
            "inner_number": {"N": "10"}
        }},
        "list": {"L": [
            {"S": "item1"},
            {"N": "2"},
            {"BOOL": False},
            {"M": {"key": {"S": "value"}}}
        ]}
    }
    
    # Use the job's _parse_dynamodb_map by creating a temporary job
    temp_job = Job(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "Test Job",
        "Test description",
        "2021-01-01T00:00:00",
        "user123",
        "pending",
        "medium",
        {}
    )
    
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