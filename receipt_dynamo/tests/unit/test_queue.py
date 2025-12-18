from datetime import datetime

import pytest

from receipt_dynamo.entities import Queue, item_to_queue


@pytest.fixture
def example_queue():
    """Creates an example Queue object for testing."""
    return Queue(
        queue_name="test-queue",
        description="A test queue for running test jobs",
        created_at=datetime(2023, 1, 1, 12, 0, 0),
        max_concurrent_jobs=5,
        priority="high",
        job_count=3,
    )


@pytest.fixture
def example_queue_minimal():
    """Creates a minimal example Queue object for testing."""
    return Queue(
        queue_name="minimal-queue",
        description="Minimal test queue",
        created_at="2023-01-01T12:00:00",
    )


@pytest.mark.unit
def test_queue_init_valid(example_queue):
    """Queue constructor works with valid parameters."""
    assert example_queue.queue_name == "test-queue"
    assert example_queue.description == "A test queue for running test jobs"
    assert example_queue.created_at == "2023-01-01T12:00:00"
    assert example_queue.max_concurrent_jobs == 5
    assert example_queue.priority == "high"
    assert example_queue.job_count == 3


@pytest.mark.unit
def test_queue_init_invalid_queue_name():
    """Queue constructor raises a ValueError with an invalid queue_name."""
    with pytest.raises(ValueError, match="queue_name must be a non-empty string"):
        Queue(queue_name="", description="Test queue", created_at=datetime.now())

    with pytest.raises(ValueError, match="queue_name must be a non-empty string"):
        Queue(
            queue_name=None,
            description="Test queue",
            created_at=datetime.now(),
        )

    with pytest.raises(ValueError, match="queue_name must be a non-empty string"):
        Queue(queue_name=123, description="Test queue", created_at=datetime.now())


@pytest.mark.unit
def test_queue_init_invalid_description():
    """Queue constructor raises a ValueError with an invalid description."""
    with pytest.raises(
        ValueError,
        match="description must be a string",
    ):
        Queue(
            queue_name="test-queue",
            description=123,
            created_at=datetime.now(),
        )

    with pytest.raises(
        ValueError,
        match="description must be a string",
    ):
        Queue(
            queue_name="test-queue",
            description=None,
            created_at=datetime.now(),
        )


@pytest.mark.unit
def test_queue_init_invalid_created_at():
    """Queue constructor raises a ValueError with an invalid created_at."""
    with pytest.raises(
        ValueError,
        match="created_at must be a datetime object or a string",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=123,
        )

    with pytest.raises(
        ValueError,
        match="created_at must be a datetime object or a string",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=None,
        )


@pytest.mark.unit
def test_queue_init_invalid_max_concurrent_jobs():
    """
    Queue constructor raises a ValueError with invalid max_concurrent_jobs.
    """
    with pytest.raises(
        ValueError,
        match="max_concurrent_jobs must be a positive integer",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            max_concurrent_jobs=0,
        )

    with pytest.raises(
        ValueError,
        match="max_concurrent_jobs must be a positive integer",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            max_concurrent_jobs=-1,
        )

    with pytest.raises(
        ValueError,
        match="max_concurrent_jobs must be a positive integer",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            max_concurrent_jobs="5",
        )


@pytest.mark.unit
def test_queue_init_invalid_priority():
    """Queue constructor raises a ValueError with an invalid priority."""
    with pytest.raises(ValueError, match="priority must be one of"):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            priority="invalid",
        )

    with pytest.raises(ValueError, match="priority must be one of"):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            priority=123,
        )


@pytest.mark.unit
def test_queue_init_invalid_job_count():
    """Queue constructor raises a ValueError with an invalid job_count."""
    with pytest.raises(
        ValueError,
        match="job_count must be a non-negative integer",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            job_count=-1,
        )

    with pytest.raises(
        ValueError,
        match="job_count must be a non-negative integer",
    ):
        Queue(
            queue_name="test-queue",
            description="Test queue",
            created_at=datetime.now(),
            job_count="3",
        )


@pytest.mark.unit
def test_queue_key(example_queue):
    """Queue.key method returns the correct primary key."""
    key = example_queue.key
    assert key["PK"] == {"S": "QUEUE#test-queue"}
    assert key["SK"] == {"S": "QUEUE"}


@pytest.mark.unit
def test_queue_gsi1_key(example_queue):
    """Queue.gsi1_key() method returns the correct GSI1 key."""
    gsi1_key = example_queue.gsi1_key()
    assert gsi1_key["GSI1PK"] == {"S": "QUEUE"}
    assert gsi1_key["GSI1SK"] == {"S": "QUEUE#test-queue"}


@pytest.mark.unit
def test_queue_to_item(example_queue, example_queue_minimal):
    """Queue.to_item() method returns the correct DynamoDB item."""
    # Test full queue
    item = example_queue.to_item()
    assert item["PK"] == {"S": "QUEUE#test-queue"}
    assert item["SK"] == {"S": "QUEUE"}
    assert item["GSI1PK"] == {"S": "QUEUE"}
    assert item["GSI1SK"] == {"S": "QUEUE#test-queue"}
    assert item["TYPE"] == {"S": "QUEUE"}
    assert item["description"] == {"S": "A test queue for running test jobs"}
    assert item["created_at"] == {"S": "2023-01-01T12:00:00"}
    assert item["max_concurrent_jobs"] == {"N": "5"}
    assert item["priority"] == {"S": "high"}
    assert item["job_count"] == {"N": "3"}

    # Test minimal queue
    item = example_queue_minimal.to_item()
    assert item["PK"] == {"S": "QUEUE#minimal-queue"}
    assert item["SK"] == {"S": "QUEUE"}
    assert item["GSI1PK"] == {"S": "QUEUE"}
    assert item["GSI1SK"] == {"S": "QUEUE#minimal-queue"}
    assert item["TYPE"] == {"S": "QUEUE"}
    assert item["description"] == {"S": "Minimal test queue"}
    assert item["created_at"] == {"S": "2023-01-01T12:00:00"}
    assert item["max_concurrent_jobs"] == {"N": "1"}
    assert item["priority"] == {"S": "medium"}
    assert item["job_count"] == {"N": "0"}


@pytest.mark.unit
def test_queue_repr(example_queue):
    """Queue.__repr__() method returns the correct string representation."""
    repr_str = repr(example_queue)
    assert "Queue(" in repr_str
    assert "queue_name='test-queue'" in repr_str
    assert "description='A test queue for running test jobs'" in repr_str
    assert "created_at='2023-01-01T12:00:00'" in repr_str
    assert "max_concurrent_jobs=5" in repr_str
    assert "priority='high'" in repr_str
    assert "job_count=3" in repr_str


@pytest.mark.unit
def test_queue_iter(example_queue):
    """Queue.__iter__() method yields all attributes."""
    queue_dict = dict(example_queue)
    assert queue_dict["queue_name"] == "test-queue"
    assert queue_dict["description"] == "A test queue for running test jobs"
    assert queue_dict["created_at"] == "2023-01-01T12:00:00"
    assert queue_dict["max_concurrent_jobs"] == 5
    assert queue_dict["priority"] == "high"
    assert queue_dict["job_count"] == 3


@pytest.mark.unit
def test_queue_eq():
    """Queue.__eq__() method correctly compares Queue objects."""
    queue1 = Queue(
        queue_name="test-queue",
        description="Test queue",
        created_at="2023-01-01T12:00:00",
        max_concurrent_jobs=5,
        priority="high",
        job_count=3,
    )  # noqa: E501
    queue2 = Queue(
        queue_name="test-queue",
        description="Test queue",
        created_at="2023-01-01T12:00:00",
        max_concurrent_jobs=5,
        priority="high",
        job_count=3,
    )  # noqa: E501
    queue3 = Queue(
        queue_name="different-queue",
        description="Different queue",
        created_at="2023-01-02T12:00:00",
        max_concurrent_jobs=10,
        priority="medium",
        job_count=0,
    )  # noqa: E501

    assert queue1 == queue2
    assert queue1 != queue3
    assert queue1 != "not a queue"


@pytest.mark.unit
def test_itemToQueue(example_queue, example_queue_minimal):
    """item_to_queue converts a DynamoDB item to a Queue object."""
    # Test full queue
    item = example_queue.to_item()
    queue = item_to_queue(item)
    assert queue == example_queue

    # Test minimal queue
    item = example_queue_minimal.to_item()
    queue = item_to_queue(item)
    assert queue == example_queue_minimal

    # Test invalid item
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_queue({})

    # Test missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_queue({"PK": {"S": "QUEUE#test-queue"}})

    # Test error handling for missing/invalid values
    item = example_queue.to_item()
    del item["priority"]
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_queue(item)
