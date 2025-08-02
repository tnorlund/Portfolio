from datetime import datetime

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data._queue import validate_last_evaluated_key
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from receipt_dynamo.entities import Job, Queue, QueueJob

# This entity is not used in production infrastructure
pytestmark = [
    pytest.mark.integration,
    pytest.mark.unused_in_production
]


@pytest.fixture
def queue_dynamo(dynamodb_table):
    """Return a DynamoClient instance that uses the mocked DynamoDB table"""
    return DynamoClient(table_name=dynamodb_table)


@pytest.fixture
def sample_queue():
    created_at = datetime.now()
    return Queue(
        queue_name="test-queue",
        description="This is a test queue",
        created_at=created_at,
        max_concurrent_jobs=5,
        priority="high",
        job_count=0,
    )


@pytest.fixture
def sample_job():
    job_id = "12345678-1234-4678-9234-567812345678"
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
def sample_queue_job(sample_queue, sample_job):
    enqueued_at = datetime.now()
    return QueueJob(
        queue_name=sample_queue.queue_name,
        job_id=sample_job.job_id,
        enqueued_at=enqueued_at,
        priority="high",
        position=1,
    )


@pytest.mark.integration
def test_addQueue_success(queue_dynamo, sample_queue):
    """Test that adding a queue to DynamoDB is successful."""
    queue_dynamo.add_queue(sample_queue)

    # Verify the queue was added correctly
    queue = queue_dynamo.get_queue(sample_queue.queue_name)

    assert queue.queue_name == sample_queue.queue_name
    assert queue.description == sample_queue.description
    assert queue.max_concurrent_jobs == sample_queue.max_concurrent_jobs
    assert queue.priority == sample_queue.priority
    assert queue.job_count == sample_queue.job_count


@pytest.mark.integration
def test_addQueue_raises_value_error(queue_dynamo):
    """Test that trying to add a None queue raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.add_queue(None)


@pytest.mark.integration
def test_addQueue_raises_value_error_queue_not_instance(queue_dynamo):
    """Test that trying to add a non-Queue instance raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.add_queue("not a queue")


@pytest.mark.integration
def test_addQueue_raises_conditional_check_failed(queue_dynamo, sample_queue):
    """
    Test that trying to add an already existing queue raises a ValueError.
    """
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Try to add it again
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        queue_dynamo.add_queue(sample_queue)


@pytest.mark.integration
def test_addQueue_raises_resource_not_found(
    queue_dynamo, sample_queue, monkeypatch
):
    """
    Test that trying to add a queue to a non-existent table raises a
    ClientError.
    """

    def mock_put_item(*args, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table does not exist",
                }
            },
            "PutItem",
        )

    # Patch the put_item method to raise ResourceNotFoundException
    monkeypatch.setattr(queue_dynamo._client, "put_item", mock_put_item)

    with pytest.raises(
        Exception, match="Table not found for operation add_queue"
    ):
        queue_dynamo.add_queue(sample_queue)


@pytest.mark.integration
def test_addQueues_success(queue_dynamo):
    """Test that adding multiple queues is successful."""
    # Create multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now(),
            max_concurrent_jobs=5,
            priority="medium",
            job_count=0,
        )
        for i in range(3)
    ]

    queue_dynamo.add_queues(queues)

    # Verify all queues were added
    for queue in queues:
        result = queue_dynamo.get_queue(queue.queue_name)
        assert result.queue_name == queue.queue_name
        assert result.description == queue.description


@pytest.mark.integration
def test_addQueues_empty_list(queue_dynamo):
    """Test that adding an empty list of queues does nothing."""
    # This should not raise an error
    queue_dynamo.add_queues([])


@pytest.mark.integration
def test_addQueues_handles_unprocessed_items(queue_dynamo, monkeypatch):
    """Test that addQueues handles unprocessed items correctly."""
    # Create multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now().isoformat(),
        )
        for i in range(3)
    ]

    # Patch the get_item method to return successfully for all queues
    original_get_item = queue_dynamo._client.get_item

    def mock_get_item(*args, **kwargs):
        queue_name = kwargs["Key"]["PK"]["S"].replace("QUEUE#", "")

        # Find the matching queue from our list
        for q in queues:
            if q.queue_name == queue_name:
                return {"Item": q.to_item()}

        # Otherwise use the original method
        return original_get_item(*args, **kwargs)

    monkeypatch.setattr(queue_dynamo._client, "get_item", mock_get_item)

    # Mock batch_write_item to return unprocessed items on first call, then
    # succeed
    call_count = 0
    original_batch_write = queue_dynamo._client.batch_write_item

    def mock_batch_write(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            # Return unprocessed items for the first queue
            unprocessed_item = {
                queue_dynamo.table_name: [
                    {"PutRequest": {"Item": queues[0].to_item()}}
                ]
            }
            return {"UnprocessedItems": unprocessed_item}
        # On subsequent calls, use the original method
        return original_batch_write(*args, **kwargs)

    monkeypatch.setattr(
        queue_dynamo._client, "batch_write_item", mock_batch_write
    )

    # This should handle the unprocessed items
    queue_dynamo.add_queues(queues)

    # Verify all queues were eventually added
    for queue in queues:
        result = queue_dynamo.get_queue(queue.queue_name)
        assert result.queue_name == queue.queue_name
        assert result.description == queue.description


@pytest.mark.integration
def test_updateQueue_success(queue_dynamo, sample_queue):
    """Test that updating a queue is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Update the queue
    sample_queue.description = "Updated description"
    sample_queue.max_concurrent_jobs = 10
    sample_queue.priority = "critical"

    queue_dynamo.update_queue(sample_queue)

    # Verify the queue was updated
    updated_queue = queue_dynamo.get_queue(sample_queue.queue_name)
    assert updated_queue.description == "Updated description"
    assert updated_queue.max_concurrent_jobs == 10
    assert updated_queue.priority == "critical"


@pytest.mark.integration
def test_updateQueue_raises_value_error_none(queue_dynamo):
    """Test that trying to update a None queue raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.update_queue(None)


@pytest.mark.integration
def test_updateQueue_raises_value_error_not_instance(queue_dynamo):
    """Test that trying to update a non-Queue instance raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.update_queue("not a queue")


@pytest.mark.integration
def test_updateQueue_raises_queue_not_found(queue_dynamo, sample_queue):
    """Test that trying to update a non-existent queue raises a ValueError."""
    # Don't add the queue first

    with pytest.raises(EntityNotFoundError, match="Queue .* not found"):
        queue_dynamo.update_queue(sample_queue)


@pytest.mark.integration
def test_deleteQueue_success(queue_dynamo, sample_queue):
    """Test that deleting a queue is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Delete the queue
    queue_dynamo.delete_queue(sample_queue)

    # Verify the queue was deleted
    with pytest.raises(
        EntityNotFoundError, match=f"Queue {sample_queue.queue_name} not found"
    ):
        queue_dynamo.get_queue(sample_queue.queue_name)


@pytest.mark.integration
def test_deleteQueue_raises_value_error_none(queue_dynamo):
    """Test that trying to delete a None queue raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.delete_queue(None)


@pytest.mark.integration
def test_deleteQueue_raises_value_error_not_instance(queue_dynamo):
    """Test that trying to delete a non-Queue instance raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.delete_queue("not a queue")


@pytest.mark.integration
def test_deleteQueue_raises_queue_not_found(queue_dynamo, sample_queue):
    """Test that trying to delete a non-existent queue raises a ValueError."""
    # Don't add the queue first

    with pytest.raises(EntityNotFoundError, match="Queue .* not found"):
        queue_dynamo.delete_queue(sample_queue)


@pytest.mark.integration
def test_getQueue_success(queue_dynamo, sample_queue):
    """Test that getting a queue is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Get the queue
    queue = queue_dynamo.get_queue(sample_queue.queue_name)

    # Verify the queue data
    assert queue.queue_name == sample_queue.queue_name
    assert queue.description == sample_queue.description
    assert queue.max_concurrent_jobs == sample_queue.max_concurrent_jobs
    assert queue.priority == sample_queue.priority
    assert queue.job_count == sample_queue.job_count


@pytest.mark.integration
def test_getQueue_raises_value_error_none(queue_dynamo):
    """
    Test that trying to get a queue with None queue_name raises a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.get_queue(None)


@pytest.mark.integration
def test_getQueue_raises_value_error_empty(queue_dynamo):
    """
    Test that trying to get a queue with empty queue_name raises a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.get_queue("")


@pytest.mark.integration
def test_getQueue_queue_not_found(queue_dynamo):
    """Test that trying to get a non-existent queue raises a ValueError."""
    with pytest.raises(
        EntityNotFoundError, match="Queue non-existent-queue not found"
    ):
        queue_dynamo.get_queue("non-existent-queue")


@pytest.mark.integration
def test_listQueues_success(queue_dynamo):
    """Test that listing queues is successful."""
    # Add multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now(),
            max_concurrent_jobs=5,
            priority="medium",
        )
        for i in range(5)
    ]

    for queue in queues:
        queue_dynamo.add_queue(queue)

    # List the queues
    result_queues, last_evaluated_key = queue_dynamo.list_queues()

    # Verify the queues were listed
    assert len(result_queues) >= len(queues)

    # Check that all our test queues are in the results
    queue_names = [q.queue_name for q in result_queues]
    for queue in queues:
        assert queue.queue_name in queue_names


@pytest.mark.integration
def test_listQueues_with_limit(queue_dynamo):
    """Test that listing queues with a limit is successful."""
    # Add multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now(),
        )
        for i in range(5)
    ]

    for queue in queues:
        queue_dynamo.add_queue(queue)

    # List the queues with a limit
    result_queues, last_evaluated_key = queue_dynamo.list_queues(limit=2)

    # Verify the queues were limited
    assert len(result_queues) == 2

    # If there are more results, there should be a last_evaluated_key
    if len(queues) > 2:
        assert last_evaluated_key is not None


@pytest.mark.integration
def test_listQueues_with_pagination(queue_dynamo):
    """Test that listing queues with pagination is successful."""
    # Add multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now(),
        )
        for i in range(5)
    ]

    for queue in queues:
        queue_dynamo.add_queue(queue)

    # Get the first page
    result_queues1, last_evaluated_key = queue_dynamo.list_queues(limit=2)

    # If there's a last_evaluated_key, get the next page
    if last_evaluated_key:
        result_queues2, _ = queue_dynamo.list_queues(
            limit=2, last_evaluated_key=last_evaluated_key
        )

        # Verify the second page has different queues
        assert set(q.queue_name for q in result_queues1).isdisjoint(
            set(q.queue_name for q in result_queues2)
        )


@pytest.mark.integration
def test_listQueues_with_invalid_last_evaluated_key(queue_dynamo):
    """
    Test that listing queues with an invalid last_evaluated_key raises
    a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.list_queues(last_evaluated_key={"wrong_key": "value"})


@pytest.mark.integration
def test_addJobToQueue_success(
    queue_dynamo, sample_queue, sample_job, sample_queue_job
):
    """Test that adding a job to a queue is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Add the job to the queue
    queue_dynamo.add_job_to_queue(sample_queue_job)

    # Verify the job was added to the queue
    result_jobs, _ = queue_dynamo.list_jobs_in_queue(sample_queue.queue_name)
    assert len(result_jobs) == 1
    assert result_jobs[0].job_id == sample_job.job_id
    assert result_jobs[0].queue_name == sample_queue.queue_name

    # Verify the queue's job count was incremented
    updated_queue = queue_dynamo.get_queue(sample_queue.queue_name)
    assert updated_queue.job_count == 1


@pytest.mark.integration
def test_addJobToQueue_raises_value_error_none(queue_dynamo):
    """Test that trying to add a None queue_job raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.add_job_to_queue(None)


@pytest.mark.integration
def test_addJobToQueue_raises_value_error_not_instance(queue_dynamo):
    """Test that trying to add a non-QueueJob instance raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.add_job_to_queue("not a queue job")


@pytest.mark.integration
def test_addJobToQueue_queue_not_found(queue_dynamo, sample_queue_job):
    """
    Test that trying to add a job to a non-existent queue raises a
    ValueError.
    """
    # Don't add the queue first

    with pytest.raises(
        EntityNotFoundError,
        match=f"Queue {sample_queue_job.queue_name} not found",
    ):
        queue_dynamo.add_job_to_queue(sample_queue_job)


@pytest.mark.integration
def test_removeJobFromQueue_success(
    queue_dynamo, sample_queue, sample_queue_job
):
    """Test that removing a job from a queue is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Add the job to the queue
    queue_dynamo.add_job_to_queue(sample_queue_job)

    # Remove the job from the queue
    queue_dynamo.remove_job_from_queue(sample_queue_job)

    # Verify the job was removed from the queue
    result_jobs, _ = queue_dynamo.list_jobs_in_queue(sample_queue.queue_name)
    assert len(result_jobs) == 0

    # Verify the queue's job count was decremented
    updated_queue = queue_dynamo.get_queue(sample_queue.queue_name)
    assert updated_queue.job_count == 0


@pytest.mark.integration
def test_removeJobFromQueue_raises_value_error_none(queue_dynamo):
    """Test that trying to remove a None queue_job raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.remove_job_from_queue(None)


@pytest.mark.integration
def test_removeJobFromQueue_raises_value_error_not_instance(queue_dynamo):
    """
    Test that trying to remove a non-QueueJob instance raises a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.remove_job_from_queue("not a queue job")


@pytest.mark.integration
def test_removeJobFromQueue_queue_not_found(queue_dynamo, sample_queue_job):
    """
    Test that trying to remove a job from a non-existent queue raises a
    ValueError.
    """
    # Don't add the queue first

    with pytest.raises(
        EntityNotFoundError,
        match="Entity does not exist: QueueJob",
    ):
        queue_dynamo.remove_job_from_queue(sample_queue_job)


@pytest.mark.integration
def test_removeJobFromQueue_job_not_in_queue(
    queue_dynamo, sample_queue, sample_queue_job
):
    """
    Test that trying to remove a job that isn't in the queue raises a
    ValueError.
    """
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Don't add the job to the queue

    with pytest.raises(
        EntityNotFoundError,
        match="Entity does not exist: QueueJob",
    ):
        queue_dynamo.remove_job_from_queue(sample_queue_job)


@pytest.mark.integration
def test_listJobsInQueue_success(queue_dynamo, sample_queue, sample_job):
    """Test that listing jobs in a queue is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Add multiple jobs to the queue
    for i in range(3):
        # Ensure valid UUIDv4 format
        job_id = f"12345678-1234-4678-9{i}34-567812345678"
        queue_job = QueueJob(
            queue_name=sample_queue.queue_name,
            job_id=job_id,
            enqueued_at=datetime.now(),
            priority="medium",
            position=i,
        )
        queue_dynamo.add_job_to_queue(queue_job)

    # List the jobs in the queue
    result_jobs, last_evaluated_key = queue_dynamo.list_jobs_in_queue(
        sample_queue.queue_name
    )

    # Verify the jobs were listed
    assert len(result_jobs) == 3

    # Verify the queue's job count
    updated_queue = queue_dynamo.get_queue(sample_queue.queue_name)
    assert updated_queue.job_count == 3


@pytest.mark.integration
def test_listJobsInQueue_with_limit(queue_dynamo, sample_queue, sample_job):
    """Test that listing jobs in a queue with a limit is successful."""
    # Add the queue first
    queue_dynamo.add_queue(sample_queue)

    # Add multiple jobs to the queue
    for i in range(5):
        # Ensure valid UUIDv4 format
        job_id = f"12345678-1234-4678-9{i}34-567812345678"
        queue_job = QueueJob(
            queue_name=sample_queue.queue_name,
            job_id=job_id,
            enqueued_at=datetime.now(),
            priority="medium",
            position=i,
        )
        queue_dynamo.add_job_to_queue(queue_job)

    # List the jobs with a limit
    result_jobs, last_evaluated_key = queue_dynamo.list_jobs_in_queue(
        sample_queue.queue_name, limit=2
    )

    # Verify the jobs were limited
    assert len(result_jobs) == 2

    # If there are more results, there should be a last_evaluated_key
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_listJobsInQueue_raises_value_error_none_queue_name(queue_dynamo):
    """
    Test that trying to list jobs with None queue_name raises a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.list_jobs_in_queue(None)


@pytest.mark.integration
def test_listJobsInQueue_raises_value_error_empty_queue_name(queue_dynamo):
    """
    Test that trying to list jobs with empty queue_name raises a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.list_jobs_in_queue("")


@pytest.mark.integration
def test_listJobsInQueue_queue_not_found(queue_dynamo, monkeypatch):
    """
    Test that trying to list jobs in a non-existent queue raises a ValueError.
    """

    # Mock the getQueue method to raise an EntityNotFoundError
    def mock_get_queue(self, queue_name):
        raise EntityNotFoundError(f"Queue {queue_name} not found")

    # Apply the mock
    monkeypatch.setattr(queue_dynamo.__class__, "get_queue", mock_get_queue)

    # Now test the listJobsInQueue function
    with pytest.raises(
        EntityNotFoundError, match="Queue non-existent-queue not found"
    ):
        queue_dynamo.list_jobs_in_queue("non-existent-queue")


@pytest.mark.integration
def test_findQueuesForJob_success(queue_dynamo, sample_job):
    """Test that finding queues for a job is successful."""
    # Create multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now(),
        )
        for i in range(3)
    ]

    for queue in queues:
        queue_dynamo.add_queue(queue)

    # Add the job to all queues
    for queue in queues:
        queue_job = QueueJob(
            queue_name=queue.queue_name,
            job_id=sample_job.job_id,
            enqueued_at=datetime.now(),
            priority="medium",
            position=0,
        )
        queue_dynamo.add_job_to_queue(queue_job)

    # Find queues for the job
    result_queue_jobs, last_evaluated_key = queue_dynamo.find_queues_for_job(
        sample_job.job_id
    )

    # Verify the queues were found
    assert len(result_queue_jobs) == 3

    # Check that all our test queues are in the results
    queue_names = [qj.queue_name for qj in result_queue_jobs]
    for queue in queues:
        assert queue.queue_name in queue_names


@pytest.mark.integration
def test_findQueuesForJob_with_limit(queue_dynamo, sample_job):
    """Test that finding queues for a job with a limit is successful."""
    # Create multiple queues
    queues = [
        Queue(
            queue_name=f"test-queue-{i}",
            description=f"Test queue {i}",
            created_at=datetime.now(),
        )
        for i in range(5)
    ]

    for queue in queues:
        queue_dynamo.add_queue(queue)

    # Add the job to all queues
    for queue in queues:
        queue_job = QueueJob(
            queue_name=queue.queue_name,
            job_id=sample_job.job_id,
            enqueued_at=datetime.now(),
            priority="medium",
            position=0,
        )
        queue_dynamo.add_job_to_queue(queue_job)

    # Find queues with a limit
    result_queue_jobs, last_evaluated_key = queue_dynamo.find_queues_for_job(
        sample_job.job_id, limit=2
    )

    # Verify the results were limited
    assert len(result_queue_jobs) == 2

    # If there are more results, there should be a last_evaluated_key
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_findQueuesForJob_raises_value_error_none_job_id(queue_dynamo):
    """Test that trying to find queues with None job_id raises a ValueError."""
    with pytest.raises(ValueError):
        queue_dynamo.find_queues_for_job(None)


@pytest.mark.integration
def test_findQueuesForJob_raises_value_error_empty_job_id(queue_dynamo):
    """
    Test that trying to find queues with empty job_id raises a ValueError.
    """
    with pytest.raises(ValueError):
        queue_dynamo.find_queues_for_job("")


@pytest.mark.integration
def test_findQueuesForJob_with_no_queues(queue_dynamo, sample_job):
    """
    Test that finding queues for a job with no queues returns an empty list.
    """
    # Don't add the job to any queues

    result_queue_jobs, last_evaluated_key = queue_dynamo.find_queues_for_job(
        sample_job.job_id
    )

    # Verify no queues were found
    assert len(result_queue_jobs) == 0
    assert last_evaluated_key is None


@pytest.mark.integration
def test_validate_last_evaluated_key_valid():
    """Test that valid last_evaluated_key passes validation."""
    valid_lek = {"PK": {"S": "QUEUE#test-queue"}, "SK": {"S": "QUEUE"}}

    # This should not raise an exception
    validate_last_evaluated_key(valid_lek)


@pytest.mark.integration
def test_validate_last_evaluated_key_invalid_none():
    """
    Test that None last_evaluated_key passes validation (treated as not
    provided).
    """
    # This should not raise an exception, so we just call it directly
    # No assertion needed because we expect no exception
    validate_last_evaluated_key(None)


@pytest.mark.integration
def test_validate_last_evaluated_key_invalid_missing_key():
    """Test that last_evaluated_key with missing key raises ValueError."""
    # This is missing the SK key
    invalid_lek = {"PK": {"S": "QUEUE#test-queue"}}

    with pytest.raises(
        ValueError, match="LastEvaluatedKey must contain PK and SK"
    ):
        validate_last_evaluated_key(invalid_lek)


@pytest.mark.integration
def test_validate_last_evaluated_key_invalid_format():
    """Test that last_evaluated_key with invalid format raises ValueError."""
    invalid_lek = {
        "PK": "QUEUE#test-queue",  # Not in DynamoDB format
        "SK": {"S": "QUEUE"},
    }

    with pytest.raises(
        ValueError,
        match="LastEvaluatedKey values must be in DynamoDB format with 'S' "
        "attribute",
    ):
        validate_last_evaluated_key(invalid_lek)
