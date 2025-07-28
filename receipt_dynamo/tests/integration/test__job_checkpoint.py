import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data._job_checkpoint import validate_last_evaluated_key
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_checkpoint import JobCheckpoint


@pytest.fixture
def job_checkpoint_dynamo(dynamodb_table):
    """Return a DynamoClient instance that uses the mocked DynamoDB table"""
    return DynamoClient(table_name=dynamodb_table)


@pytest.fixture
def sample_job():
    job_id = str(uuid.uuid4())
    created_at = datetime.now()
    return Job(
        job_id=job_id,
        name="Test Job",
        description="This is a test job",
        created_at=created_at,
        created_by="test_user",
        status="pending",
        priority="medium",
        job_config={
            "type": "training",
            "model": "receipt_model",
            "batch_size": 32,
        },
        estimated_duration=3600,
        tags={"env": "test", "purpose": "integration-test"},
    )


@pytest.fixture
def sample_job_checkpoint(sample_job):
    """Provides a sample JobCheckpoint for testing."""
    timestamp = datetime.now(timezone.utc).isoformat()
    return JobCheckpoint(
        job_id=sample_job.job_id,
        timestamp=timestamp,
        s3_bucket="test-checkpoint-bucket",
        s3_key=f"jobs/{sample_job.job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=1024000,
        step=1000,
        epoch=5,
        model_state=True,
        optimizer_state=True,
        metrics={"loss": 0.1234, "accuracy": 0.9876},
        is_best=False,
    )


@pytest.fixture
def sample_job_checkpoint_2(sample_job):
    """Provides a second sample JobCheckpoint for testing."""
    timestamp = (
        datetime.now(timezone.utc).replace(microsecond=0)
        - timedelta(minutes=5)
    ).isoformat()
    return JobCheckpoint(
        job_id=sample_job.job_id,
        timestamp=timestamp,
        s3_bucket="test-checkpoint-bucket",
        s3_key=f"jobs/{sample_job.job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=512000,
        step=500,
        epoch=2,
        model_state=True,
        optimizer_state=True,
        metrics={"loss": 0.2345, "accuracy": 0.8765},
        is_best=False,
    )


# ---
# addJobCheckpoint
# ---


@pytest.mark.integration
def test_addJobCheckpoint_success(
    job_checkpoint_dynamo, sample_job, sample_job_checkpoint
):
    """Test adding a job checkpoint successfully"""
    # Add the job first (since it's a foreign key reference)
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoint
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)

    # Verify the job checkpoint was added by retrieving it
    checkpoint = job_checkpoint_dynamo.get_job_checkpoint(
        sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
    )
    assert checkpoint.job_id == sample_job_checkpoint.job_id
    assert checkpoint.timestamp == sample_job_checkpoint.timestamp
    assert checkpoint.s3_bucket == sample_job_checkpoint.s3_bucket
    assert checkpoint.s3_key == sample_job_checkpoint.s3_key
    assert checkpoint.size_bytes == sample_job_checkpoint.size_bytes
    assert checkpoint.step == sample_job_checkpoint.step
    assert checkpoint.epoch == sample_job_checkpoint.epoch
    assert checkpoint.model_state == sample_job_checkpoint.model_state
    assert checkpoint.optimizer_state == sample_job_checkpoint.optimizer_state
    assert checkpoint.metrics == sample_job_checkpoint.metrics
    assert checkpoint.is_best == sample_job_checkpoint.is_best


@pytest.mark.integration
def test_addJobCheckpoint_raises_value_error(job_checkpoint_dynamo):
    """
    Test that addJobCheckpoint raises ValueError when job_checkpoint is None
    """
    with pytest.raises(
        ValueError,
        match="JobCheckpoint parameter is required and cannot be None",
    ):
        job_checkpoint_dynamo.add_job_checkpoint(None)


@pytest.mark.integration
def test_addJobCheckpoint_raises_value_error_not_instance(
    job_checkpoint_dynamo,
):
    """
    Test that addJobCheckpoint raises ValueError when job_checkpoint is not an
    instance of JobCheckpoint
    """
    with pytest.raises(
        ValueError,
        match="job_checkpoint must be an instance of the JobCheckpoint class.",
    ):
        job_checkpoint_dynamo.add_job_checkpoint("not a job checkpoint")


@pytest.mark.integration
def test_addJobCheckpoint_raises_conditional_check_failed(
    job_checkpoint_dynamo, sample_job, sample_job_checkpoint
):
    """
    Test that addJobCheckpoint raises ValueError when the job checkpoint
    already exists
    """
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoint first
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)

    # Try to add it again
    expected_msg = f"Entity already exists: JobCheckpoint with job_id={sample_job_checkpoint.job_id}"
    with pytest.raises(
        EntityAlreadyExistsError, match=re.escape(expected_msg)
    ):
        job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)


@pytest.mark.integration
def test_addJobCheckpoint_raises_resource_not_found(
    job_checkpoint_dynamo, sample_job_checkpoint, mocker
):
    """Simulate a ResourceNotFoundException when adding a job checkpoint"""
    mock_put = mocker.patch.object(
        job_checkpoint_dynamo._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Table not found"):
        job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    mock_put.assert_called_once()


# ---
# getJobCheckpoint
# ---


@pytest.mark.integration
def test_getJobCheckpoint_success(
    job_checkpoint_dynamo, sample_job, sample_job_checkpoint
):
    """Test getting a job checkpoint successfully"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoint
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)

    # Get the job checkpoint
    checkpoint = job_checkpoint_dynamo.get_job_checkpoint(
        sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
    )

    # Verify
    assert checkpoint.job_id == sample_job_checkpoint.job_id
    assert checkpoint.timestamp == sample_job_checkpoint.timestamp
    assert checkpoint.s3_bucket == sample_job_checkpoint.s3_bucket
    assert checkpoint.s3_key == sample_job_checkpoint.s3_key
    assert checkpoint.size_bytes == sample_job_checkpoint.size_bytes
    assert checkpoint.step == sample_job_checkpoint.step
    assert checkpoint.epoch == sample_job_checkpoint.epoch
    assert checkpoint.model_state == sample_job_checkpoint.model_state
    assert checkpoint.optimizer_state == sample_job_checkpoint.optimizer_state
    assert checkpoint.metrics == sample_job_checkpoint.metrics
    assert checkpoint.is_best == sample_job_checkpoint.is_best


@pytest.mark.integration
def test_getJobCheckpoint_raises_value_error_job_id_none(
    job_checkpoint_dynamo,
):
    """Test that getJobCheckpoint raises ValueError when job_id is None"""
    with pytest.raises(ValueError, match="job_id cannot be None"):
        job_checkpoint_dynamo.get_job_checkpoint(None, "timestamp")


@pytest.mark.integration
def test_getJobCheckpoint_raises_value_error_timestamp_none(
    job_checkpoint_dynamo, sample_job
):
    """Test that getJobCheckpoint raises ValueError when timestamp is None"""
    with pytest.raises(
        ValueError,
        match="Timestamp is required and must be a non-empty string.",
    ):
        job_checkpoint_dynamo.get_job_checkpoint(sample_job.job_id, None)


@pytest.mark.integration
def test_getJobCheckpoint_raises_value_error_not_found(
    job_checkpoint_dynamo, sample_job
):
    """
    Test that getJobCheckpoint raises ValueError when the job checkpoint does
    not exist
    """
    with pytest.raises(
        ValueError, match="No job checkpoint found with job ID.*"
    ):
        job_checkpoint_dynamo.get_job_checkpoint(
            sample_job.job_id, "nonexistent-timestamp"
        )


# ---
# updateBestCheckpoint
# ---


@pytest.mark.integration
def test_updateBestCheckpoint_success(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test updating the best checkpoint successfully"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # Update the best checkpoint
    job_checkpoint_dynamo.update_best_checkpoint(
        sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
    )

    # Get both checkpoints to verify the update
    checkpoint1 = job_checkpoint_dynamo.get_job_checkpoint(
        sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
    )
    checkpoint2 = job_checkpoint_dynamo.get_job_checkpoint(
        sample_job_checkpoint_2.job_id, sample_job_checkpoint_2.timestamp
    )

    # Verify
    assert checkpoint1.is_best is True
    assert checkpoint2.is_best is False


@pytest.mark.integration
def test_updateBestCheckpoint_raises_value_error_job_id_none(
    job_checkpoint_dynamo,
):
    """Test that updateBestCheckpoint raises ValueError when job_id is None"""
    with pytest.raises(ValueError, match="job_id cannot be None"):
        job_checkpoint_dynamo.update_best_checkpoint(None, "timestamp")


@pytest.mark.integration
def test_updateBestCheckpoint_raises_value_error_timestamp_none(
    job_checkpoint_dynamo, sample_job
):
    """
    Test that updateBestCheckpoint raises ValueError when timestamp is None
    """
    with pytest.raises(
        ValueError,
        match="Timestamp is required and must be a non-empty string.",
    ):
        job_checkpoint_dynamo.update_best_checkpoint(sample_job.job_id, None)


@pytest.mark.integration
def test_updateBestCheckpoint_raises_value_error_not_found(
    job_checkpoint_dynamo, sample_job
):
    """
    Test that updateBestCheckpoint raises ValueError when the job checkpoint
    does not exist
    """
    with pytest.raises(
        ValueError,
        match=(
            "Cannot update best checkpoint: No checkpoint found with job ID.*"
        ),
    ):
        job_checkpoint_dynamo.update_best_checkpoint(
            sample_job.job_id, "nonexistent-timestamp"
        )


# ---
# listJobCheckpoints
# ---


@pytest.mark.integration
def test_listJobCheckpoints_success(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test listing job checkpoints successfully"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # List the job checkpoints
    checkpoints, last_evaluated_key = (
        job_checkpoint_dynamo.list_job_checkpoints(sample_job.job_id)
    )

    # Verify
    assert len(checkpoints) == 2
    checkpoint_timestamps = [c.timestamp for c in checkpoints]
    assert sample_job_checkpoint.timestamp in checkpoint_timestamps
    assert sample_job_checkpoint_2.timestamp in checkpoint_timestamps


@pytest.mark.integration
def test_listJobCheckpoints_with_limit(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test listing job checkpoints with a limit"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # List the job checkpoints with limit=1
    checkpoints, last_evaluated_key = (
        job_checkpoint_dynamo.list_job_checkpoints(sample_job.job_id, limit=1)
    )

    # Verify
    assert len(checkpoints) == 1
    assert (
        last_evaluated_key is not None
    )  # There should be a last evaluated key


@pytest.mark.integration
def test_listJobCheckpoints_with_pagination(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test listing job checkpoints with pagination"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # List the first page
    checkpoints_page1, last_evaluated_key = (
        job_checkpoint_dynamo.list_job_checkpoints(sample_job.job_id, limit=1)
    )

    # Verify first page
    assert len(checkpoints_page1) == 1
    assert last_evaluated_key is not None

    # List the second page
    checkpoints_page2, last_evaluated_key2 = (
        job_checkpoint_dynamo.list_job_checkpoints(
            sample_job.job_id, limit=1, last_evaluated_key=last_evaluated_key
        )
    )

    # Verify second page
    assert len(checkpoints_page2) == 1
    assert (
        checkpoints_page1[0].timestamp != checkpoints_page2[0].timestamp
    )  # Different checkpoint on each page


@pytest.mark.integration
def test_listJobCheckpoints_empty(job_checkpoint_dynamo, sample_job):
    """Test listing job checkpoints when none exist"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # List the job checkpoints
    checkpoints, last_evaluated_key = (
        job_checkpoint_dynamo.list_job_checkpoints(sample_job.job_id)
    )

    # Verify
    assert len(checkpoints) == 0
    assert last_evaluated_key is None


# ---
# getBestCheckpoint
# ---


@pytest.mark.integration
def test_getBestCheckpoint_success(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test getting the best checkpoint successfully"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # Update the best checkpoint
    job_checkpoint_dynamo.update_best_checkpoint(
        sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
    )

    # Get the best checkpoint
    best_checkpoint = job_checkpoint_dynamo.get_best_checkpoint(
        sample_job.job_id
    )

    # Verify
    assert best_checkpoint is not None
    assert best_checkpoint.job_id == sample_job_checkpoint.job_id
    assert best_checkpoint.timestamp == sample_job_checkpoint.timestamp
    assert best_checkpoint.is_best is True


@pytest.mark.integration
def test_getBestCheckpoint_none_found(job_checkpoint_dynamo, sample_job):
    """Test getting the best checkpoint when none exist"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Get the best checkpoint
    best_checkpoint = job_checkpoint_dynamo.get_best_checkpoint(
        sample_job.job_id
    )

    # Verify
    assert best_checkpoint is None


@pytest.mark.integration
def test_getBestCheckpoint_raises_value_error_job_id_none(
    job_checkpoint_dynamo,
):
    """Test that getBestCheckpoint raises ValueError when job_id is None"""
    with pytest.raises(ValueError, match="job_id cannot be None"):
        job_checkpoint_dynamo.get_best_checkpoint(None)


# ---
# deleteJobCheckpoint
# ---


@pytest.mark.integration
def test_deleteJobCheckpoint_success(
    job_checkpoint_dynamo, sample_job, sample_job_checkpoint
):
    """Test deleting a job checkpoint successfully"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoint
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)

    # Delete the job checkpoint
    job_checkpoint_dynamo.delete_job_checkpoint(
        sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
    )

    # Verify the job checkpoint was deleted
    with pytest.raises(
        ValueError, match="No job checkpoint found with job ID.*"
    ):
        job_checkpoint_dynamo.get_job_checkpoint(
            sample_job_checkpoint.job_id, sample_job_checkpoint.timestamp
        )


@pytest.mark.integration
def test_deleteJobCheckpoint_raises_value_error_job_id_none(
    job_checkpoint_dynamo,
):
    """Test that deleteJobCheckpoint raises ValueError when job_id is None"""
    with pytest.raises(ValueError, match="job_id cannot be None"):
        job_checkpoint_dynamo.delete_job_checkpoint(None, "timestamp")


@pytest.mark.integration
def test_deleteJobCheckpoint_raises_value_error_timestamp_none(
    job_checkpoint_dynamo, sample_job
):
    """
    Test that deleteJobCheckpoint raises ValueError when timestamp is None
    """
    with pytest.raises(
        ValueError,
        match="Timestamp is required and must be a non-empty string.",
    ):
        job_checkpoint_dynamo.delete_job_checkpoint(sample_job.job_id, None)


# ---
# listAllJobCheckpoints
# ---


@pytest.mark.integration
def test_listAllJobCheckpoints_success(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test listing all job checkpoints successfully"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # List all job checkpoints
    checkpoints, last_evaluated_key = (
        job_checkpoint_dynamo.list_all_job_checkpoints()
    )

    # Verify
    assert len(checkpoints) >= 2  # There may be other checkpoints in the DB
    job_checkpoints = [c for c in checkpoints if c.job_id == sample_job.job_id]
    assert len(job_checkpoints) == 2
    checkpoint_timestamps = [c.timestamp for c in job_checkpoints]
    assert sample_job_checkpoint.timestamp in checkpoint_timestamps
    assert sample_job_checkpoint_2.timestamp in checkpoint_timestamps


@pytest.mark.integration
def test_listAllJobCheckpoints_with_limit(
    job_checkpoint_dynamo,
    sample_job,
    sample_job_checkpoint,
    sample_job_checkpoint_2,
):
    """Test listing all job checkpoints with a limit"""
    # Add the job first
    job_checkpoint_dynamo.add_job(sample_job)

    # Add the job checkpoints
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint)
    job_checkpoint_dynamo.add_job_checkpoint(sample_job_checkpoint_2)

    # List all job checkpoints with limit=1
    checkpoints, last_evaluated_key = (
        job_checkpoint_dynamo.list_all_job_checkpoints(limit=1)
    )

    # Verify
    assert len(checkpoints) == 1
    assert (
        last_evaluated_key is not None
    )  # There should be a last evaluated key


# ---
# Error Handling
# ---


@pytest.mark.integration
def test_validate_last_evaluated_key_raises_value_error_missing_keys():
    """
    Test that validate_last_evaluated_key raises ValueError when keys are
    missing
    """
    with pytest.raises(ValueError, match="LastEvaluatedKey must contain keys"):
        validate_last_evaluated_key({"PK": {"S": "value"}})  # Missing SK


@pytest.mark.integration
def test_validate_last_evaluated_key_raises_value_error_invalid_format():
    """
    Test that validate_last_evaluated_key raises ValueError when the format is
    invalid
    """
    with pytest.raises(
        ValueError,
        match="LastEvaluatedKey.* must be a dict containing a key 'S'",
    ):
        validate_last_evaluated_key({"PK": {"S": "value"}, "SK": "not a dict"})


@pytest.mark.integration
def test_listJobCheckpoints_raises_client_error(
    job_checkpoint_dynamo, sample_job, mocker
):
    """
    Test that listJobCheckpoints raises an exception when a ClientError occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_checkpoint_dynamo._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "Query",
        ),
    )

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Could not list job checkpoints from the database"
    ):
        job_checkpoint_dynamo.list_job_checkpoints(sample_job.job_id)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listAllJobCheckpoints_raises_client_error(
    job_checkpoint_dynamo, mocker
):
    """
    Test that listAllJobCheckpoints raises an exception when a ClientError
    occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_checkpoint_dynamo._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "Query",
        ),
    )

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Could not list all job checkpoints from the database"
    ):
        job_checkpoint_dynamo.list_all_job_checkpoints()
    mock_query.assert_called_once()
