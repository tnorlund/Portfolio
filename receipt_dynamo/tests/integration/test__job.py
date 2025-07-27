import uuid
from datetime import datetime

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data._job import validate_last_evaluated_key
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError, EntityNotFoundError
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_status import JobStatus


@pytest.fixture
def job_dynamo(dynamodb_table):
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
def sample_job_status(sample_job):
    return JobStatus(
        job_id=sample_job.job_id,
        status="running",
        updated_at=datetime.now(),
        progress=50.0,
        message="Processing data",
        updated_by="test_system",
        instance_id="i-12345678",
    )


# ---
# addJob
# ---


@pytest.mark.integration
def test_addJob_success(job_dynamo, sample_job):
    """Test adding a job successfully"""
    job_dynamo.add_job(sample_job)
    # Verify the job was added
    job = job_dynamo.get_job(sample_job.job_id)
    assert job.job_id == sample_job.job_id
    assert job.name == sample_job.name
    assert job.status == sample_job.status


@pytest.mark.integration
def test_addJob_raises_value_error(job_dynamo):
    """Test that addJob raises ValueError when job is None"""
    with pytest.raises(
        ValueError, match="job cannot be None"
    ):
        job_dynamo.add_job(None)


@pytest.mark.integration
def test_addJob_raises_value_error_job_not_instance(job_dynamo):
    """Test that addJob raises ValueError when job is not an instance of Job"""
    with pytest.raises(
        ValueError, match="job must be an instance of the Job class."
    ):
        job_dynamo.add_job("not a job")


@pytest.mark.integration
def test_addJob_raises_conditional_check_failed(job_dynamo, sample_job):
    """Test that addJob raises ValueError when the job already exists"""
    # Add the job first
    job_dynamo.add_job(sample_job)
    # Try to add it again
    with pytest.raises(
        EntityAlreadyExistsError, match="already exists"
    ):
        job_dynamo.add_job(sample_job)


@pytest.mark.integration
def test_addJob_raises_resource_not_found(job_dynamo, sample_job, mocker):
    """
    Simulate a ResourceNotFoundException when adding a job.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
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
        # Note: In _job.py we do not specifically catch
        # ResourceNotFoundException in addJob, so it won't raise ValueError,
        # it re-raises the original error if it's not
        # ConditionalCheckFailedException.
        job_dynamo.add_job(sample_job)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJob_raises_provisioned_throughput_exceeded(
    job_dynamo, sample_job, mocker
):
    """
    Simulate a ProvisionedThroughputExceededException when adding a job.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        job_dynamo.add_job(sample_job)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJob_raises_internal_server_error(job_dynamo, sample_job, mocker):
    """
    Simulate an InternalServerError when adding a job.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Internal server error"):
        job_dynamo.add_job(sample_job)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJob_raises_unknown_error(job_dynamo, sample_job, mocker):
    """
    Simulate an unknown error when adding a job.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "Something unexpected",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Something unexpected"):
        job_dynamo.add_job(sample_job)
    mock_put.assert_called_once()


# ---
# addJobs
# ---


@pytest.mark.integration
def test_addJobs_success(job_dynamo, sample_job):
    """Test adding multiple jobs successfully"""
    job2 = Job(
        job_id=str(uuid.uuid4()),
        name="Test Job 2",
        description="This is another test job",
        created_at=datetime.now(),
        created_by="test_user",
        status="pending",
        priority="high",
        job_config={"type": "inference", "model": "receipt_model"},
        estimated_duration=1800,
        tags={"env": "test", "purpose": "integration-test"},
    )
    jobs = [sample_job, job2]
    job_dynamo.add_jobs(jobs)

    # Verify the jobs were added
    job1_result = job_dynamo.get_job(sample_job.job_id)
    job2_result = job_dynamo.get_job(job2.job_id)

    assert job1_result.job_id == sample_job.job_id
    assert job1_result.name == sample_job.name
    assert job2_result.job_id == job2.job_id
    assert job2_result.name == job2.name


@pytest.mark.integration
def test_addJobs_raises_value_error_jobs_none(job_dynamo):
    """Test that addJobs raises ValueError when jobs is None"""
    with pytest.raises(
        ValueError, match="jobs cannot be None"
    ):
        job_dynamo.add_jobs(None)


@pytest.mark.integration
def test_addJobs_raises_value_error_jobs_not_list(job_dynamo):
    """Test that addJobs raises ValueError when jobs is not a list"""
    with pytest.raises(
        ValueError, match="jobs must be a list of Job instances."
    ):
        job_dynamo.add_jobs("not a list")


@pytest.mark.integration
def test_addJobs_raises_value_error_jobs_not_list_of_jobs(
    job_dynamo, sample_job
):
    """
    Test that addJobs raises ValueError when jobs is not a list of Job
    instances
    """
    with pytest.raises(
        ValueError, match="All jobs must be instances of the Job class."
    ):
        job_dynamo.add_jobs([sample_job, "not a job"])


@pytest.mark.integration
def test_addJobs_raises_clienterror_provisioned_throughput_exceeded(
    job_dynamo, sample_job, mocker
):
    """
    Simulate a ProvisionedThroughputExceededException when adding jobs.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        job_dynamo.add_jobs([sample_job])
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJobs_raises_clienterror_internal_server_error(
    job_dynamo, sample_job, mocker
):
    """
    Simulate an InternalServerError when adding jobs.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(Exception, match="Internal server error"):
        job_dynamo.add_jobs([sample_job])
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJobs_raises_clienterror_validation_exception(
    job_dynamo, sample_job, mocker
):
    """
    Simulate a ValidationException when adding jobs.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameters given were invalid",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(
        Exception, match=r"One or more parameters.*invalid"
    ):
        job_dynamo.add_jobs([sample_job])
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJobs_raises_clienterror_access_denied(
    job_dynamo, sample_job, mocker
):
    """
    Simulate an AccessDeniedException when adding jobs.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "Access denied",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(Exception, match="Access denied"):
        job_dynamo.add_jobs([sample_job])
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addJobs_raises_clienterror(job_dynamo, sample_job, mocker):
    """
    Simulate an unknown error when adding jobs.
    """
    mock_put = mocker.patch.object(
        job_dynamo._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "Something unexpected",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(Exception, match="Something unexpected"):
        job_dynamo.add_jobs([sample_job])
    mock_put.assert_called_once()


@pytest.mark.integration
def test_getJob_success(job_dynamo, sample_job):
    """Test getting a job successfully"""
    job_dynamo.add_job(sample_job)

    # Get the job
    job = job_dynamo.get_job(sample_job.job_id)

    # Verify
    assert job.job_id == sample_job.job_id
    assert job.name == sample_job.name
    assert job.description == sample_job.description
    assert job.created_by == sample_job.created_by
    assert job.status == sample_job.status
    assert job.priority == sample_job.priority
    assert job.job_config == sample_job.job_config
    assert job.estimated_duration == sample_job.estimated_duration
    assert job.tags == sample_job.tags


@pytest.mark.integration
def test_addJobs_unprocessed_items_retry(job_dynamo, sample_job, mocker):
    """
    Partial mocking to simulate unprocessed items. The second call should
    succeed.
    """
    jobs = [sample_job]
    # Create a second job with a new UUID instead of trying to increment the
    # string
    second_job = Job(
        job_id=str(uuid.uuid4()),  # Generate a new UUID
        name="Test Job 2",
        description="This is another test job",
        created_at=datetime.now(),
        created_by=sample_job.created_by,
        status=sample_job.status,
        priority=sample_job.priority,
        job_config=sample_job.job_config,
        estimated_duration=sample_job.estimated_duration,
        tags=sample_job.tags,
    )
    jobs.append(second_job)

    real_batch_write_item = job_dynamo._client.batch_write_item
    call_count = {"value": 0}

    def custom_side_effect(*args, **kwargs):
        call_count["value"] += 1
        real_batch_write_item(*args, **kwargs)
        # On first call, pretend second job was unprocessed
        if call_count["value"] == 1:
            return {
                "UnprocessedItems": {
                    job_dynamo.table_name: [
                        {"PutRequest": {"Item": second_job.to_item()}}
                    ]
                }
            }
        else:
            # No unprocessed items on subsequent calls
            return {"UnprocessedItems": {}}

    mocker.patch.object(
        job_dynamo._client, "batch_write_item", side_effect=custom_side_effect
    )
    job_dynamo.add_jobs(jobs)

    assert call_count["value"] == 2, "Should have retried once."

    stored, _ = job_dynamo.list_jobs()
    assert len(stored) == 2
    # We can't directly compare objects since the stored ones might have
    # different timestamps/attributes
    stored_job_ids = [job.job_id for job in stored]
    assert sample_job.job_id in stored_job_ids
    assert second_job.job_id in stored_job_ids


@pytest.mark.integration
def test_getJob_raises_value_error_job_id_none(job_dynamo):
    """Test that getJob raises ValueError when job_id is None"""
    with pytest.raises(
        ValueError, match="job_id cannot be None"
    ):
        job_dynamo.get_job(None)


@pytest.mark.integration
def test_getJob_raises_value_error_job_not_found(job_dynamo):
    """Test that getJob raises EntityNotFoundError when job does not exist"""
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    with pytest.raises(
        EntityNotFoundError, match="Job with job id .* does not exist"
    ):
        job_dynamo.get_job(str(uuid.uuid4()))


@pytest.mark.integration
def test_updateJob_success(job_dynamo, sample_job):
    """Test updating a job successfully"""
    # Add the job
    job_dynamo.add_job(sample_job)

    # Update the job
    sample_job.status = "running"
    sample_job.priority = "high"
    job_dynamo.update_job(sample_job)

    # Get the updated job
    updated_job = job_dynamo.get_job(sample_job.job_id)

    # Verify
    assert updated_job.status == "running"
    assert updated_job.priority == "high"


@pytest.mark.integration
def test_updateJob_raises_value_error_job_none(job_dynamo):
    """Test that updateJob raises ValueError when job is None"""
    with pytest.raises(
        ValueError, match="job cannot be None"
    ):
        job_dynamo.update_job(None)


@pytest.mark.integration
def test_updateJob_raises_value_error_job_not_instance(job_dynamo):
    """
    Test that updateJob raises ValueError when job is not an instance of
    Job
    """
    with pytest.raises(
        ValueError, match="job must be an instance of the Job class."
    ):
        job_dynamo.update_job("not a job")


@pytest.mark.integration
def test_updateJob_raises_conditional_check_failed(job_dynamo, sample_job):
    """Test that updateJob raises EntityNotFoundError when the job does not exist"""
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    # Try to update without adding first
    with pytest.raises(
        EntityNotFoundError,
        match="Entity does not exist: Job",
    ):
        job_dynamo.update_job(sample_job)


@pytest.mark.integration
def test_deleteJob_success(job_dynamo, sample_job):
    """Test deleting a job successfully"""
    # Add the job
    job_dynamo.add_job(sample_job)

    # Delete the job
    job_dynamo.delete_job(sample_job)

    # Try to get the job - should raise EntityNotFoundError
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    with pytest.raises(
        EntityNotFoundError,
        match=f"Job with job id {sample_job.job_id} does not exist",
    ):
        job_dynamo.get_job(sample_job.job_id)


@pytest.mark.integration
def test_deleteJob_raises_value_error_job_none(job_dynamo):
    """Test that deleteJob raises ValueError when job is None"""
    with pytest.raises(
        ValueError, match="job cannot be None"
    ):
        job_dynamo.delete_job(None)


@pytest.mark.integration
def test_deleteJob_raises_value_error_job_not_instance(job_dynamo):
    """
    Test that deleteJob raises ValueError when job is not an instance of Job
    """
    with pytest.raises(
        ValueError, match="job must be an instance of the Job class."
    ):
        job_dynamo.delete_job("not a job")


@pytest.mark.integration
def test_deleteJob_raises_conditional_check_failed(job_dynamo, sample_job):
    """Test that deleteJob raises ValueError when the job does not exist"""
    # Try to delete without adding first
    with pytest.raises(
        EntityNotFoundError, match="Entity does not exist: Job"
    ):
        job_dynamo.delete_job(sample_job)


@pytest.mark.integration
def test_addJobStatus_success(job_dynamo, sample_job, sample_job_status):
    """Test adding a job status successfully"""
    # Add the job first
    job_dynamo.add_job(sample_job)

    # Add the job status
    job_dynamo.add_job_status(sample_job_status)

    # Get the latest job status
    status = job_dynamo.get_latest_job_status(sample_job.job_id)

    # Verify
    assert status.job_id == sample_job_status.job_id
    assert status.status == sample_job_status.status
    assert status.progress == sample_job_status.progress
    assert status.message == sample_job_status.message


@pytest.mark.integration
def test_addJobStatus_raises_value_error_status_none(job_dynamo):
    """Test that addJobStatus raises ValueError when status is None"""
    with pytest.raises(
        ValueError, match="jobstatus cannot be None"
    ):
        job_dynamo.add_job_status(None)


@pytest.mark.integration
def test_addJobStatus_raises_value_error_status_not_instance(job_dynamo):
    """
    Test that addJobStatus raises ValueError when status is not an instance
    of JobStatus
    """
    with pytest.raises(
        ValueError,
        match="job_status must be an instance of the JobStatus class.",
    ):
        job_dynamo.add_job_status("not a job status")


@pytest.mark.integration
def test_getJobWithStatus_success(job_dynamo, sample_job, sample_job_status):
    """Test getting a job with its status updates"""
    # Add the job
    job_dynamo.add_job(sample_job)

    # Add a status update
    job_dynamo.add_job_status(sample_job_status)

    # Add another status update with different status
    new_status = JobStatus(
        job_id=sample_job.job_id,
        status="succeeded",
        updated_at=datetime.now(),
        progress=100.0,
        message="Job completed successfully",
        updated_by="test_system",
        instance_id="i-12345678",
    )
    job_dynamo.add_job_status(new_status)

    # Get the job with status updates
    job, statuses = job_dynamo.get_job_with_status(sample_job.job_id)

    # Verify
    assert job.job_id == sample_job.job_id
    assert len(statuses) == 2
    assert statuses[0].status in ["running", "succeeded"]
    assert statuses[1].status in ["running", "succeeded"]
    # The statuses should be different
    assert statuses[0].status != statuses[1].status
    assert statuses[0].job_id == sample_job.job_id


@pytest.mark.integration
def test_getLatestJobStatus_raises_value_error_no_status(
    job_dynamo, sample_job
):
    """
    Test that getLatestJobStatus raises ValueError when there are no status
    updates
    """
    # Add the job
    job_dynamo.add_job(sample_job)

    # Try to get the latest status - should raise ValueError
    with pytest.raises(
        ValueError,
        match=f"No status updates found for job with ID {sample_job.job_id}",
    ):
        job_dynamo.get_latest_job_status(sample_job.job_id)


@pytest.mark.integration
def test_listJobs_success(job_dynamo, sample_job):
    """Test listJobs successfully lists jobs"""
    # Add the job first
    job_dynamo.add_job(sample_job)

    # List jobs
    jobs, last_evaluated_key = job_dynamo.list_jobs()

    # Verify
    assert len(jobs) >= 1
    assert any(job.job_id == sample_job.job_id for job in jobs)


@pytest.mark.integration
def test_listJobs_with_limit(job_dynamo, sample_job):
    """Test listJobs with a limit parameter"""
    # Add the job first
    job_dynamo.add_job(sample_job)

    # List jobs with limit=1
    jobs, last_evaluated_key = job_dynamo.list_jobs(limit=1)

    # Verify
    assert len(jobs) <= 1


@pytest.mark.integration
def test_listJobStatuses_success(job_dynamo, sample_job_status):
    """Test listJobStatuses successfully lists job statuses"""
    # Add the job status first
    job_dynamo.add_job_status(sample_job_status)

    # List job statuses
    job_statuses, last_evaluated_key = job_dynamo.list_job_statuses(
        sample_job_status.job_id
    )

    # Verify
    assert len(job_statuses) >= 1
    assert any(
        status.job_id == sample_job_status.job_id for status in job_statuses
    )


@pytest.mark.integration
def test_listJobStatuses_with_limit(job_dynamo, sample_job_status):
    """Test listJobStatuses with a limit parameter"""
    # Add the job status first
    job_dynamo.add_job_status(sample_job_status)

    # List job statuses with limit=1
    job_statuses, last_evaluated_key = job_dynamo.list_job_statuses(
        sample_job_status.job_id, limit=1
    )

    # Verify
    assert len(job_statuses) <= 1


@pytest.mark.integration
def test_listJobStatuses_raises_value_error_job_id_none(job_dynamo):
    """Test listJobStatuses raises ValueError when job_id is None"""
    with pytest.raises(
        ValueError, match="job_id cannot be None"
    ):
        job_dynamo.list_job_statuses(None)


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
def test_listJobs_raises_client_error_unknown(job_dynamo, mocker):
    """
    Test that listJobs raises an exception when an unknown ClientError
    occurs
    """
    # Mock the client to raise a ClientError
    mocked_response = {
        "Error": {
            "Code": "UnknownError",
            "Message": "An unknown error occurred",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Something unexpected"
    ):
        job_dynamo.list_jobs()


@pytest.mark.integration
def test_listJobs_raises_client_error_resource_not_found(job_dynamo, mocker):
    """
    Test that listJobs raises an exception when ResourceNotFoundException
    occurs
    """
    # Mock the client to raise a ResourceNotFoundException
    mocked_response = {
        "Error": {
            "Code": "ResourceNotFoundException",
            "Message": "Table not found",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Table not found"
    ):
        job_dynamo.list_jobs()


@pytest.mark.integration
def test_listJobStatuses_raises_client_error_resource_not_found(
    job_dynamo, mocker
):
    """
    Test that listJobStatuses raises an exception when
    ResourceNotFoundException occurs
    """
    # Mock the client to raise a ResourceNotFoundException
    mocked_response = {
        "Error": {
            "Code": "ResourceNotFoundException",
            "Message": "Table not found",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Could not list job statuses from the database"
    ):
        job_dynamo.list_job_statuses(str(uuid.uuid4()))


@pytest.mark.integration
def test_listJobStatuses_raises_client_error_internal_server_error(
    job_dynamo, mocker
):
    """
    Test that listJobStatuses raises an exception when InternalServerError
    occurs
    """
    # Mock the client to raise an InternalServerError
    mocked_response = {
        "Error": {
            "Code": "InternalServerError",
            "Message": "Internal server error",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(Exception, match="Internal server error"):
        job_dynamo.list_job_statuses(str(uuid.uuid4()))


@pytest.mark.integration
def test_listJobStatuses_raises_client_error_access_denied(job_dynamo, mocker):
    """
    Test that listJobStatuses raises an exception when AccessDeniedException
    occurs
    """
    # Mock the client to raise an AccessDeniedException
    mocked_response = {
        "Error": {"Code": "AccessDeniedException", "Message": "Access denied"}
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Could not list job statuses from the database"
    ):
        job_dynamo.list_job_statuses(str(uuid.uuid4()))


@pytest.mark.integration
def test_listJobsByStatus_raises_client_error_unknown(job_dynamo, mocker):
    """
    Test that listJobsByStatus raises an exception when an unknown ClientError
    occurs
    """
    # Mock the client to raise a ClientError
    mocked_response = {
        "Error": {
            "Code": "UnknownError",
            "Message": "An unknown error occurred",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Something unexpected"
    ):
        job_dynamo.list_jobs_by_status("pending")


@pytest.mark.integration
def test_listJobsByUser_raises_client_error_unknown(job_dynamo, mocker):
    """
    Test that listJobsByUser raises an exception when an unknown ClientError
    occurs
    """
    # Mock the client to raise a ClientError
    mocked_response = {
        "Error": {
            "Code": "UnknownError",
            "Message": "An unknown error occurred",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        Exception, match="Something unexpected"
    ):
        job_dynamo.list_jobs_by_user("test_user")


@pytest.mark.integration
def test_getJob_raises_client_error_resource_not_found(job_dynamo, mocker):
    """
    Test that getJob raises an exception when ResourceNotFoundException
    occurs
    """
    # Mock the client to raise a ResourceNotFoundException
    mocked_response = {
        "Error": {
            "Code": "ResourceNotFoundException",
            "Message": "Table not found",
        }
    }
    mocked_error = ClientError(mocked_response, "GetItem")
    mocker.patch.object(
        job_dynamo._client, "get_item", side_effect=mocked_error
    )

    # Call the method and verify it raises the expected exception
    with pytest.raises(Exception, match="Table not found for operation get_job"):
        job_dynamo.get_job(str(uuid.uuid4()))


@pytest.mark.integration
def test_getJob_raises_client_error_internal_server_error(job_dynamo, mocker):
    """
    Test that getJob raises an exception when InternalServerError occurs
    """
    # Mock the client to raise an InternalServerError
    mocked_response = {
        "Error": {
            "Code": "InternalServerError",
            "Message": "Internal server error",
        }
    }
    mocked_error = ClientError(mocked_response, "GetItem")
    mocker.patch.object(
        job_dynamo._client, "get_item", side_effect=mocked_error
    )

    # Call the method and verify it raises the expected exception
    with pytest.raises(Exception, match="Internal server error"):
        job_dynamo.get_job(str(uuid.uuid4()))


@pytest.mark.integration
def test_getLatestJobStatus_raises_client_error_resource_not_found(
    job_dynamo, mocker
):
    """
    Test that getLatestJobStatus raises an exception when
    ResourceNotFoundException occurs
    """
    # Mock the client to raise a ResourceNotFoundException
    mocked_response = {
        "Error": {
            "Code": "ResourceNotFoundException",
            "Message": "Table not found",
        }
    }
    mocked_error = ClientError(mocked_response, "Query")
    mocker.patch.object(job_dynamo._client, "query", side_effect=mocked_error)

    # Call the method and verify it raises the expected exception
    from receipt_dynamo.data.shared_exceptions import DynamoDBError

    with pytest.raises(DynamoDBError, match="Could not get latest job status"):
        job_dynamo.get_latest_job_status(str(uuid.uuid4()))
