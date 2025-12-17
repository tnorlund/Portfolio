import uuid
from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.job_log import JobLog

pytestmark = pytest.mark.integration


@pytest.fixture
def job_log_dynamo(dynamodb_table):
    """Creates a DynamoClient instance configured for testing job logs."""
    from receipt_dynamo import DynamoClient

    return DynamoClient(table_name=dynamodb_table, region="us-east-1")


@pytest.fixture
def sample_job_log():
    """Provides a sample JobLog for testing."""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    return JobLog(
        job_id=job_id,
        timestamp=timestamp,
        log_level="INFO",
        message="Test log message",
        source="test_component",
        exception=None,
    )


@pytest.fixture
def multiple_job_logs():
    """Provides multiple sample JobLogs for the same job for testing."""
    job_id = str(uuid.uuid4())
    base_time = datetime.now()
    return [
        JobLog(
            job_id=job_id,
            timestamp=(base_time - timedelta(minutes=i)).isoformat(),
            log_level=level,
            message=f"Test log message {i}",
            source="test_component",
            exception=None,
        )
        for i, level in enumerate(
            ["INFO", "WARNING", "ERROR", "INFO", "DEBUG"]
        )
    ]


@pytest.mark.integration
def test_addJobLog_success(job_log_dynamo, sample_job_log):
    """Test adding a job log entry successfully."""
    # Add the job log
    job_log_dynamo.add_job_log(sample_job_log)

    # Verify it was added by retrieving it
    retrieved_log = job_log_dynamo.get_job_log(
        job_id=sample_job_log.job_id, timestamp=sample_job_log.timestamp
    )
    assert retrieved_log == sample_job_log


@pytest.mark.integration
def test_addJobLog_raises_value_error(job_log_dynamo):
    """Test that addJobLog raises ValueError when job_log is None."""
    with pytest.raises(OperationError, match="job_log cannot be None"):
        job_log_dynamo.add_job_log(None)


@pytest.mark.integration
def test_addJobLog_raises_value_error_job_not_instance(job_log_dynamo):
    """
    Test that addJobLog raises ValueError when job_log is not a JobLog
    instance.
    """
    with pytest.raises(
        OperationError, match="job_log must be an instance of JobLog"
    ):
        job_log_dynamo.add_job_log("not a job log")


@pytest.mark.integration
def test_addJobLog_raises_conditional_check_failed(
    job_log_dynamo, sample_job_log
):
    """
    Test that addJobLog raises ValueError when trying to add a duplicate job
    log.
    """
    # Add the job log
    job_log_dynamo.add_job_log(sample_job_log)

    # Try to add it again, which should raise an error
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        job_log_dynamo.add_job_log(sample_job_log)


@pytest.mark.integration
def test_addJobLog_raises_resource_not_found(
    job_log_dynamo, sample_job_log, mocker
):
    """
    Test that addJobLog handles ResourceNotFoundException properly.
    """
    # Mock the put_item method to raise ResourceNotFoundException
    mock_client = mocker.patch.object(job_log_dynamo, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "The table does not exist",
            }
        },
        "PutItem",
    )

    # Attempt to add the job log
    with pytest.raises(OperationError, match="DynamoDB resource not found during add_job_log"):
        job_log_dynamo.add_job_log(sample_job_log)


@pytest.mark.integration
def test_addJobLogs_success(job_log_dynamo, multiple_job_logs):
    """Test adding multiple job logs successfully."""
    # Add the job logs
    job_log_dynamo.add_job_logs(multiple_job_logs)

    # Verify they were added by retrieving and comparing
    for log in multiple_job_logs:
        retrieved_log = job_log_dynamo.get_job_log(
            job_id=log.job_id, timestamp=log.timestamp
        )
        assert retrieved_log == log


@pytest.mark.integration
def test_addJobLogs_raises_value_error_logs_none(job_log_dynamo):
    """Test that addJobLogs raises ValueError when job_logs is None."""
    with pytest.raises(EntityValidationError, match="job_logs cannot be None"):
        job_log_dynamo.add_job_logs(None)


@pytest.mark.integration
def test_addJobLogs_raises_value_error_logs_not_list(job_log_dynamo):
    """Test that addJobLogs raises ValueError when job_logs is not a list."""
    with pytest.raises(EntityValidationError, match="job_logs must be a list"):
        job_log_dynamo.add_job_logs("not a list")


@pytest.mark.integration
def test_addJobLogs_raises_value_error_logs_not_list_of_logs(
    job_log_dynamo, sample_job_log
):
    """
    Test that addJobLogs raises ValueError when job_logs contains non-JobLog
    items.
    """
    with pytest.raises(
        EntityValidationError, match="All items in job_logs must be JobLog instances"
    ):
        job_log_dynamo.add_job_logs([sample_job_log, "not a job log"])


@pytest.mark.integration
def test_addJobLogs_empty_list(job_log_dynamo):
    """Test that addJobLogs handles an empty list gracefully."""
    # This should not raise an error
    job_log_dynamo.add_job_logs([])


@pytest.mark.integration
def test_getJobLog_success(job_log_dynamo, sample_job_log):
    """Test retrieving a job log successfully."""
    # Add the job log
    job_log_dynamo.add_job_log(sample_job_log)

    # Retrieve the job log
    retrieved_log = job_log_dynamo.get_job_log(
        job_id=sample_job_log.job_id, timestamp=sample_job_log.timestamp
    )
    assert retrieved_log == sample_job_log


@pytest.mark.integration
def test_getJobLog_raises_value_error_job_id_none(job_log_dynamo):
    """Test that getJobLog raises ValueError when job_id is None."""
    with pytest.raises(EntityValidationError, match="job_id cannot be None"):
        job_log_dynamo.get_job_log(
            job_id=None, timestamp="2021-01-01T12:00:00"
        )


@pytest.mark.integration
def test_getJobLog_raises_value_error_timestamp_none(job_log_dynamo):
    """Test that getJobLog raises ValueError when timestamp is None."""
    with pytest.raises(EntityValidationError, match="timestamp cannot be None"):
        job_log_dynamo.get_job_log(job_id="some-job-id", timestamp=None)


@pytest.mark.integration
def test_getJobLog_raises_value_error_log_not_found(job_log_dynamo):
    """Test that getJobLog raises EntityNotFoundError when the job log is not found."""
    with pytest.raises(
        EntityNotFoundError,
        match="Job log with job_id non-existent-job and timestamp 2021-01-01T12:00:00 not found",
    ):
        job_log_dynamo.get_job_log(
            job_id="non-existent-job", timestamp="2021-01-01T12:00:00"
        )


@pytest.mark.integration
def test_listJobLogs_success(job_log_dynamo, multiple_job_logs):
    """Test listing job logs successfully."""
    # Add the job logs
    job_log_dynamo.add_job_logs(multiple_job_logs)

    # List the job logs
    job_id = multiple_job_logs[0].job_id  # All logs have the same job_id
    logs, last_key = job_log_dynamo.list_job_logs(job_id=job_id)

    # Check that all logs are returned
    assert len(logs) == len(multiple_job_logs)

    # Check the content
    for log in multiple_job_logs:
        # Find the corresponding log in the returned list
        matching_log = next(
            (
                log_item
                for log_item in logs
                if log_item.timestamp == log.timestamp
            ),
            None,
        )
        assert (
            matching_log is not None
        ), f"Log with timestamp {log.timestamp} not found"
        assert matching_log == log


@pytest.mark.integration
def test_listJobLogs_with_limit(job_log_dynamo, multiple_job_logs):
    """Test listing job logs with a limit."""
    # Add the job logs
    job_log_dynamo.add_job_logs(multiple_job_logs)

    # List the job logs with a limit
    job_id = multiple_job_logs[0].job_id  # All logs have the same job_id
    limit = 2
    logs, last_key = job_log_dynamo.list_job_logs(job_id=job_id, limit=limit)

    # Check that only the specified number of logs are returned
    assert len(logs) == limit

    # Check that the last evaluated key is returned
    assert (
        last_key is not None
    ), "LastEvaluatedKey should be returned when limit is used"

    # Use the last key to get the next batch
    next_logs, next_last_key = job_log_dynamo.list_job_logs(
        job_id=job_id, limit=limit, last_evaluated_key=last_key
    )

    # Check that we got more logs
    assert len(next_logs) == limit

    # Ensure we got different logs
    assert all(
        log1.timestamp != log2.timestamp for log1 in logs for log2 in next_logs
    ), "Should get different logs"


@pytest.mark.integration
def test_listJobLogs_raises_value_error_job_id_none(job_log_dynamo):
    """Test that listJobLogs raises ValueError when job_id is None."""
    with pytest.raises(EntityValidationError, match="job_id cannot be None"):
        job_log_dynamo.list_job_logs(job_id=None)


@pytest.mark.integration
def test_deleteJobLog_success(job_log_dynamo, sample_job_log):
    """Test deleting a job log successfully."""
    # Add the job log
    job_log_dynamo.add_job_log(sample_job_log)

    # Verify it was added
    retrieved_log = job_log_dynamo.get_job_log(
        job_id=sample_job_log.job_id, timestamp=sample_job_log.timestamp
    )
    assert retrieved_log == sample_job_log

    # Delete the job log
    job_log_dynamo.delete_job_log(sample_job_log)

    # Verify it was deleted
    with pytest.raises(EntityNotFoundError, match="not found"):
        job_log_dynamo.get_job_log(
            job_id=sample_job_log.job_id, timestamp=sample_job_log.timestamp
        )


@pytest.mark.integration
def test_deleteJobLog_raises_value_error_log_none(job_log_dynamo):
    """Test that deleteJobLog raises ValueError when job_log is None."""
    with pytest.raises(EntityValidationError, match="job_log cannot be None"):
        job_log_dynamo.delete_job_log(None)


@pytest.mark.integration
def test_deleteJobLog_raises_value_error_log_not_instance(job_log_dynamo):
    """
    Test that deleteJobLog raises ValueError when job_log is not a JobLog
    instance.
    """
    with pytest.raises(
        EntityValidationError, match="job_log must be a JobLog instance, got"
    ):
        job_log_dynamo.delete_job_log("not a job log")


@pytest.mark.integration
def test_deleteJobLog_raises_conditional_check_failed(
    job_log_dynamo, sample_job_log
):
    """
    Test that deleteJobLog raises EntityNotFoundError when the job log does not exist.
    """
    # Try to delete a job log that doesn't exist
    with pytest.raises(EntityNotFoundError, match="joblog not found during delete_job_log"):
        job_log_dynamo.delete_job_log(sample_job_log)


@pytest.mark.integration
def test_listJobLogs_empty_result(job_log_dynamo):
    """Test listing job logs when there are none returns an empty list."""
    job_id = str(uuid.uuid4())
    logs, last_key = job_log_dynamo.list_job_logs(job_id=job_id)
    assert logs == []
    assert last_key is None


@pytest.mark.integration
def test_listJobLogs_with_resource_not_found(job_log_dynamo, mocker):
    """Test that listJobLogs handles ResourceNotFoundException properly."""
    # Mock the query method to raise ResourceNotFoundException
    mock_client = mocker.patch.object(job_log_dynamo, "_client")
    mock_client.query.side_effect = ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "The table does not exist",
            }
        },
        "Query",
    )

    # Attempt to list the job logs
    job_id = str(uuid.uuid4())
    with pytest.raises(OperationError, match="DynamoDB resource not found during list_job_logs"):
        job_log_dynamo.list_job_logs(job_id=job_id)
