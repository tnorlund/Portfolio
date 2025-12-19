from datetime import datetime

import pytest

from receipt_dynamo.entities.queue_job import QueueJob, item_to_queue_job


@pytest.fixture
def example_queue_job():
    """Creates an example QueueJob object for testing."""
    return QueueJob(
        queue_name="test-queue",
        job_id="12345678-1234-4678-9234-567812345678",
        enqueued_at=datetime(2023, 1, 1, 12, 0, 0),
        priority="high",
        position=1,
    )


@pytest.fixture
def example_queue_job_minimal():
    """Creates a minimal example QueueJob object for testing."""
    return QueueJob(
        queue_name="minimal-queue",
        job_id="12345678-1234-4678-9234-567812345678",
        enqueued_at="2023-01-01T12:00:00",
    )


@pytest.mark.unit
def test_queue_job_init_valid(example_queue_job):
    """QueueJob constructor works with valid parameters."""
    assert example_queue_job.queue_name == "test-queue"
    assert example_queue_job.job_id == "12345678-1234-4678-9234-567812345678"
    assert example_queue_job.enqueued_at == "2023-01-01T12:00:00"
    assert example_queue_job.priority == "high"
    assert example_queue_job.position == 1


@pytest.mark.unit
def test_queue_job_init_invalid_queue_name():
    """QueueJob constructor raises a ValueError with invalid queue_name."""
    with pytest.raises(ValueError, match="queue_name must be a non-empty string"):
        QueueJob(
            queue_name="",
            job_id="12345678-1234-5678-1234-567812345678",
            enqueued_at=datetime.now(),
        )

    with pytest.raises(ValueError, match="queue_name must be a non-empty string"):
        QueueJob(
            queue_name=None,
            job_id="12345678-1234-5678-1234-567812345678",
            enqueued_at=datetime.now(),
        )

    with pytest.raises(ValueError, match="queue_name must be a non-empty string"):
        QueueJob(
            queue_name=123,
            job_id="12345678-1234-5678-1234-567812345678",
            enqueued_at=datetime.now(),
        )


@pytest.mark.unit
def test_queue_job_init_invalid_job_id():
    """QueueJob constructor raises a ValueError with invalid job_id."""
    with pytest.raises(ValueError, match="uuid must be a valid UUIDv4"):
        QueueJob(
            queue_name="test-queue",
            job_id="invalid-uuid",
            enqueued_at=datetime.now(),
        )

    with pytest.raises(ValueError, match="uuid must be a string"):
        QueueJob(queue_name="test-queue", job_id=None, enqueued_at=datetime.now())

    with pytest.raises(ValueError, match="uuid must be a string"):
        QueueJob(queue_name="test-queue", job_id=123, enqueued_at=datetime.now())


@pytest.mark.unit
def test_queue_job_init_invalid_enqueued_at():
    """QueueJob constructor raises a ValueError with invalid enqueued_at."""
    with pytest.raises(
        ValueError, match="enqueued_at must be a datetime object or a string"
    ):
        QueueJob(
            queue_name="test-queue",
            job_id="12345678-1234-4678-9234-567812345678",
            enqueued_at=123,
        )

    with pytest.raises(
        ValueError, match="enqueued_at must be a datetime object or a string"
    ):
        QueueJob(
            queue_name="test-queue",
            job_id="12345678-1234-4678-9234-567812345678",
            enqueued_at=None,
        )


@pytest.mark.unit
def test_queue_job_init_invalid_priority():
    """QueueJob constructor raises a ValueError with invalid priority."""
    with pytest.raises(ValueError, match="priority must be one of"):
        QueueJob(
            queue_name="test-queue",
            job_id="12345678-1234-4678-9234-567812345678",
            enqueued_at=datetime.now(),
            priority="invalid",
        )

    with pytest.raises(ValueError, match="priority must be one of"):
        QueueJob(
            queue_name="test-queue",
            job_id="12345678-1234-4678-9234-567812345678",
            enqueued_at=datetime.now(),
            priority=123,
        )


@pytest.mark.unit
def test_queue_job_init_invalid_position():
    """QueueJob constructor raises a ValueError with invalid position."""
    with pytest.raises(ValueError, match="position must be a non-negative integer"):
        QueueJob(
            queue_name="test-queue",
            job_id="12345678-1234-4678-9234-567812345678",
            enqueued_at=datetime.now(),
            position=-1,
        )

    with pytest.raises(ValueError, match="position must be a non-negative integer"):
        QueueJob(
            queue_name="test-queue",
            job_id="12345678-1234-4678-9234-567812345678",
            enqueued_at=datetime.now(),
            position="3",
        )


@pytest.mark.unit
def test_queue_job_key(example_queue_job):
    """QueueJob.key method returns the correct primary key."""
    key = example_queue_job.key
    assert key["PK"] == {"S": "QUEUE#test-queue"}
    assert key["SK"] == {"S": "JOB#12345678-1234-4678-9234-567812345678"}


@pytest.mark.unit
def test_queue_job_gsi1_key(example_queue_job):
    """QueueJob.gsi1_key() method returns the correct GSI1 key."""
    gsi1_key = example_queue_job.gsi1_key()
    assert gsi1_key["GSI1PK"] == {"S": "JOB"}
    assert gsi1_key["GSI1SK"] == {
        "S": "JOB#12345678-1234-4678-9234-567812345678#QUEUE#test-queue"
    }


@pytest.mark.unit
def test_queue_job_to_item(example_queue_job, example_queue_job_minimal):
    """QueueJob.to_item() method returns the correct DynamoDB item."""
    # Test full queue job
    item = example_queue_job.to_item()
    assert item["PK"] == {"S": "QUEUE#test-queue"}
    assert item["SK"] == {"S": "JOB#12345678-1234-4678-9234-567812345678"}
    assert item["GSI1PK"] == {"S": "JOB"}
    assert item["GSI1SK"] == {
        "S": "JOB#12345678-1234-4678-9234-567812345678#QUEUE#test-queue"
    }
    assert item["TYPE"] == {"S": "QUEUE_JOB"}
    assert item["enqueued_at"] == {"S": "2023-01-01T12:00:00"}
    assert item["priority"] == {"S": "high"}
    assert item["position"] == {"N": "1"}

    # Test minimal queue job
    item = example_queue_job_minimal.to_item()
    assert item["PK"] == {"S": "QUEUE#minimal-queue"}
    assert item["SK"] == {"S": "JOB#12345678-1234-4678-9234-567812345678"}
    assert item["GSI1PK"] == {"S": "JOB"}
    assert item["GSI1SK"] == {
        "S": "JOB#12345678-1234-4678-9234-567812345678#QUEUE#minimal-queue"
    }
    assert item["TYPE"] == {"S": "QUEUE_JOB"}
    assert item["enqueued_at"] == {"S": "2023-01-01T12:00:00"}
    assert item["priority"] == {"S": "medium"}
    assert item["position"] == {"N": "0"}


@pytest.mark.unit
def test_queue_job_repr(example_queue_job):
    """QueueJob.__repr__() method returns the correct string representation."""
    repr_str = repr(example_queue_job)
    assert "QueueJob(" in repr_str
    assert "queue_name='test-queue'" in repr_str
    assert "job_id='12345678-1234-4678-9234-567812345678'" in repr_str
    assert "enqueued_at='2023-01-01T12:00:00'" in repr_str
    assert "priority='high'" in repr_str
    assert "position=1" in repr_str


@pytest.mark.unit
def test_queue_job_iter(example_queue_job):
    """QueueJob.__iter__() method yields all attributes."""
    queue_job_dict = dict(example_queue_job)
    assert queue_job_dict["queue_name"] == "test-queue"
    assert queue_job_dict["job_id"] == "12345678-1234-4678-9234-567812345678"
    assert queue_job_dict["enqueued_at"] == "2023-01-01T12:00:00"
    assert queue_job_dict["priority"] == "high"
    assert queue_job_dict["position"] == 1


@pytest.mark.unit
def test_queue_job_eq():
    """QueueJob.__eq__() method compares QueueJob objects."""
    queue_job1 = QueueJob(
        queue_name="test-queue",
        job_id="12345678-1234-4678-9234-567812345678",
        enqueued_at="2023-01-01T12:00:00",
        priority="high",
        position=1,
    )

    queue_job2 = QueueJob(
        queue_name="test-queue",
        job_id="12345678-1234-4678-9234-567812345678",
        enqueued_at="2023-01-01T12:00:00",
        priority="high",
        position=1,
    )

    queue_job3 = QueueJob(
        queue_name="different-queue",
        job_id="12345678-1234-4678-9234-567812345678",
        enqueued_at="2023-01-02T12:00:00",
        priority="medium",
        position=0,
    )

    assert queue_job1 == queue_job2
    assert queue_job1 != queue_job3
    assert queue_job1 != "not a queue job"


@pytest.mark.unit
def test_itemToQueueJob(example_queue_job, example_queue_job_minimal):
    """
    item_to_queue_job function converts a DynamoDB item to a QueueJob object
    """
    # Test full queue job
    item = example_queue_job.to_item()
    queue_job = item_to_queue_job(item)
    assert queue_job == example_queue_job

    # Test minimal queue job
    item = example_queue_job_minimal.to_item()
    queue_job = item_to_queue_job(item)
    assert queue_job == example_queue_job_minimal

    # Test invalid item
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_queue_job({})

    # Test missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_queue_job({"PK": {"S": "QUEUE#test-queue"}})

    # Test error handling for missing/invalid values
    item = example_queue_job.to_item()
    del item["priority"]
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_queue_job(item)
