import uuid
from datetime import datetime, timezone

import pytest

from receipt_dynamo.entities.job_checkpoint import (
    JobCheckpoint,
    _parse_dynamodb_map,
    _parse_dynamodb_value,
    item_to_job_checkpoint,
)


@pytest.fixture
def example_job_checkpoint():
    """Returns an example JobCheckpoint object with all fields populated"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    return JobCheckpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="my-checkpoint-bucket",
        s3_key=f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=1024000,
        step=1000,
        epoch=5,
        model_state=True,
        optimizer_state=True,
        metrics={
            "loss": 0.1234,
            "accuracy": 0.9876,
            "detailed": {"val_loss": 0.2345},
        },
        is_best=True,
    )


@pytest.fixture
def example_job_checkpoint_minimal():
    """Returns an example JobCheckpoint object with minimal fields"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    return JobCheckpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="my-checkpoint-bucket",
        s3_key=f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=512000,
        step=500,
        epoch=2,
    )


@pytest.mark.unit
def test_job_checkpoint_init_valid(example_job_checkpoint):
    """Test that a JobCheckpoint can be created with valid parameters"""
    assert isinstance(example_job_checkpoint, JobCheckpoint)
    assert isinstance(example_job_checkpoint.job_id, str)
    assert isinstance(example_job_checkpoint.timestamp, str)
    assert isinstance(example_job_checkpoint.s3_bucket, str)
    assert isinstance(example_job_checkpoint.s3_key, str)
    assert isinstance(example_job_checkpoint.size_bytes, int)
    assert isinstance(example_job_checkpoint.step, int)
    assert isinstance(example_job_checkpoint.epoch, int)
    assert isinstance(example_job_checkpoint.model_state, bool)
    assert isinstance(example_job_checkpoint.optimizer_state, bool)
    assert isinstance(example_job_checkpoint.metrics, dict)
    assert isinstance(example_job_checkpoint.is_best, bool)


@pytest.mark.unit
def test_job_checkpoint_init_invalid_id():
    """Test that a JobCheckpoint cannot be created with an invalid job_id"""
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobCheckpoint(
            job_id="invalid-uuid",
            timestamp=datetime.now(timezone.utc).isoformat(),
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobCheckpoint(
            job_id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobCheckpoint(
            job_id=123,
            timestamp=datetime.now(timezone.utc).isoformat(),
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_timestamp():
    """Test that a JobCheckpoint cannot be created with an invalid timestamp"""
    job_id = str(uuid.uuid4())
    with pytest.raises(
        ValueError, match="timestamp must be a non-empty string"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp="",
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="timestamp must be a non-empty string"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=None,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="timestamp must be a non-empty string"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=123,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_s3_bucket():
    """Test that a JobCheckpoint cannot be created with an invalid s3_bucket"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(
        ValueError, match="s3_bucket must be a non-empty string"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="s3_bucket must be a non-empty string"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket=None,
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="s3_bucket must be a non-empty string"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket=123,
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_s3_key():
    """Test that a JobCheckpoint cannot be created with an invalid s3_key"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="s3_key must be a non-empty string"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="",
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(ValueError, match="s3_key must be a non-empty string"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key=None,
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )
    with pytest.raises(ValueError, match="s3_key must be a non-empty string"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key=123,
            size_bytes=1024000,
            step=1000,
            epoch=5,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_size_bytes():
    """JobCheckpoint cannot be created with an invalid size_bytes"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(
        ValueError, match="size_bytes must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=-1,
            step=1000,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="size_bytes must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=None,
            step=1000,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="size_bytes must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes="1024",
            step=1000,
            epoch=5,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_step():
    """Test that a JobCheckpoint cannot be created with an invalid step"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(
        ValueError, match="step must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=-1,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="step must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=None,
            epoch=5,
        )
    with pytest.raises(
        ValueError, match="step must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step="1000",
            epoch=5,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_epoch():
    """Test that a JobCheckpoint cannot be created with an invalid epoch"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(
        ValueError, match="epoch must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=-1,
        )
    with pytest.raises(
        ValueError, match="epoch must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=None,
        )
    with pytest.raises(
        ValueError, match="epoch must be a non-negative integer"
    ):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch="5",
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_model_state():
    """JobCheckpoint cannot be created with an invalid model_state"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="model_state must be a boolean"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            model_state="True",
        )
    with pytest.raises(ValueError, match="model_state must be a boolean"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            model_state=1,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_optimizer_state():
    """JobCheckpoint cannot be created with an invalid optimizer_state"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="optimizer_state must be a boolean"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            optimizer_state="True",
        )
    with pytest.raises(ValueError, match="optimizer_state must be a boolean"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            optimizer_state=1,
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_metrics():
    """JobCheckpoint cannot be created with invalid metrics"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="metrics must be a dictionary"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            metrics="not a dict",
        )
    with pytest.raises(ValueError, match="metrics must be a dictionary"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            metrics=[1, 2, 3],
        )


@pytest.mark.unit
def test_job_checkpoint_init_invalid_is_best():
    """Test that a JobCheckpoint cannot be created with an invalid is_best"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="is_best must be a boolean"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            is_best="True",
        )
    with pytest.raises(ValueError, match="is_best must be a boolean"):
        JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="my-checkpoint-bucket",
            s3_key="jobs/12345/checkpoints/model.pt",
            size_bytes=1024000,
            step=1000,
            epoch=5,
            is_best=1,
        )


@pytest.mark.unit
def test_job_checkpoint_key(example_job_checkpoint):
    """Test that a JobCheckpoint generates the correct key"""
    key = example_job_checkpoint.key
    assert key["PK"]["S"] == f"JOB#{example_job_checkpoint.job_id}"
    assert key["SK"]["S"] == f"CHECKPOINT#{example_job_checkpoint.timestamp}"


@pytest.mark.unit
def test_job_checkpoint_gsi1_key(example_job_checkpoint):
    """Test that a JobCheckpoint generates the correct GSI1 key"""
    gsi1_key = example_job_checkpoint.gsi1_key()
    assert gsi1_key["GSI1PK"]["S"] == "CHECKPOINT"
    assert (
        gsi1_key["GSI1SK"]["S"] == f"JOB#{example_job_checkpoint.job_id}#"
        f"{example_job_checkpoint.timestamp}"
    )


@pytest.mark.unit
def test_job_checkpoint_to_item(
    example_job_checkpoint, example_job_checkpoint_minimal
):
    """Test that a JobCheckpoint generates the correct DynamoDB item"""
    # Test full example
    item = example_job_checkpoint.to_item()
    assert item["PK"]["S"] == f"JOB#{example_job_checkpoint.job_id}"
    assert item["SK"]["S"] == f"CHECKPOINT#{example_job_checkpoint.timestamp}"
    assert item["GSI1PK"]["S"] == "CHECKPOINT"
    assert (
        item["GSI1SK"]["S"] == f"JOB#{example_job_checkpoint.job_id}"
        f"#{example_job_checkpoint.timestamp}"
    )
    assert item["TYPE"]["S"] == "JOB_CHECKPOINT"
    assert item["job_id"]["S"] == example_job_checkpoint.job_id
    assert item["timestamp"]["S"] == example_job_checkpoint.timestamp
    assert item["s3_bucket"]["S"] == example_job_checkpoint.s3_bucket
    assert item["s3_key"]["S"] == example_job_checkpoint.s3_key
    assert item["size_bytes"]["N"] == str(example_job_checkpoint.size_bytes)
    assert item["step"]["N"] == str(example_job_checkpoint.step)
    assert item["epoch"]["N"] == str(example_job_checkpoint.epoch)
    assert item["model_state"]["BOOL"] == example_job_checkpoint.model_state
    assert (
        item["optimizer_state"]["BOOL"]
        == example_job_checkpoint.optimizer_state
    )
    assert item["is_best"]["BOOL"] == example_job_checkpoint.is_best
    assert "metrics" in item
    assert item["metrics"]["M"]["loss"]["N"] == "0.1234"
    assert item["metrics"]["M"]["accuracy"]["N"] == "0.9876"
    assert item["metrics"]["M"]["detailed"]["M"]["val_loss"]["N"] == "0.2345"

    # Test minimal example
    item_minimal = example_job_checkpoint_minimal.to_item()
    assert "metrics" not in item_minimal
    assert item_minimal["is_best"]["BOOL"] is False
    assert item_minimal["model_state"]["BOOL"] is True
    assert item_minimal["optimizer_state"]["BOOL"] is True


@pytest.mark.unit
def test_job_checkpoint_dict_to_dynamodb_map():
    """Test the _dict_to_dynamodb_map method"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    checkpoint = JobCheckpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="my-checkpoint-bucket",
        s3_key=f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=1024000,
        step=1000,
        epoch=5,
    )

    # Test with different types
    test_dict = {
        "string": "value",
        "number": 123,
        "float": 123.45,
        "bool": True,
        "null": None,
        "list": [1, "two", True, None, {"nested": "object"}],
        "nested": {
            "inner": "value",
            "innerNum": 456,
            "innerBool": False,
            "innerList": [7, 8, 9],
        },
    }

    result = checkpoint._dict_to_dynamodb_map(test_dict)

    assert result["string"]["S"] == "value"
    assert result["number"]["N"] == "123"
    assert result["float"]["N"] == "123.45"
    assert result["bool"]["BOOL"] is True
    assert result["null"]["NULL"] is True
    assert result["list"]["L"][0]["N"] == "1"
    assert result["list"]["L"][1]["S"] == "two"
    assert result["list"]["L"][2]["BOOL"] is True
    assert result["list"]["L"][3]["NULL"] is True
    assert result["list"]["L"][4]["M"]["nested"]["S"] == "object"
    assert result["nested"]["M"]["inner"]["S"] == "value"
    assert result["nested"]["M"]["innerNum"]["N"] == "456"
    assert result["nested"]["M"]["innerBool"]["BOOL"] is False
    assert result["nested"]["M"]["innerList"]["L"][0]["N"] == "7"


@pytest.mark.unit
def test_job_checkpoint_repr(example_job_checkpoint):
    """Test that a JobCheckpoint generates the correct string representation"""
    repr_str = repr(example_job_checkpoint)
    assert repr_str.startswith("JobCheckpoint(")
    assert f"job_id='{example_job_checkpoint.job_id}'" in repr_str
    assert f"timestamp='{example_job_checkpoint.timestamp}'" in repr_str
    assert f"s3_bucket='{example_job_checkpoint.s3_bucket}'" in repr_str
    assert f"s3_key='{example_job_checkpoint.s3_key}'" in repr_str
    assert f"size_bytes={example_job_checkpoint.size_bytes}" in repr_str
    assert f"step={example_job_checkpoint.step}" in repr_str
    assert f"epoch={example_job_checkpoint.epoch}" in repr_str
    assert f"model_state={example_job_checkpoint.model_state}" in repr_str
    assert (
        f"optimizer_state={example_job_checkpoint.optimizer_state}" in repr_str
    )
    assert f"is_best={example_job_checkpoint.is_best}" in repr_str
    assert "metrics=" in repr_str


@pytest.mark.unit
def test_job_checkpoint_iter(example_job_checkpoint):
    """Test that a JobCheckpoint can be iterated over"""
    attributes = dict(example_job_checkpoint)
    assert attributes["job_id"] == example_job_checkpoint.job_id
    assert attributes["timestamp"] == example_job_checkpoint.timestamp
    assert attributes["s3_bucket"] == example_job_checkpoint.s3_bucket
    assert attributes["s3_key"] == example_job_checkpoint.s3_key
    assert attributes["size_bytes"] == example_job_checkpoint.size_bytes
    assert attributes["step"] == example_job_checkpoint.step
    assert attributes["epoch"] == example_job_checkpoint.epoch
    assert attributes["model_state"] == example_job_checkpoint.model_state
    assert (
        attributes["optimizer_state"] == example_job_checkpoint.optimizer_state
    )
    assert attributes["metrics"] == example_job_checkpoint.metrics
    assert attributes["is_best"] == example_job_checkpoint.is_best


@pytest.mark.unit
def test_job_checkpoint_eq():
    """Test that JobCheckpoint equality works correctly"""
    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # Create two identical checkpoints
    checkpoint1 = JobCheckpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="my-checkpoint-bucket",
        s3_key=f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=1024000,
        step=1000,
        epoch=5,
        metrics={"loss": 0.1234},
        is_best=True,
    )

    checkpoint2 = JobCheckpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="my-checkpoint-bucket",
        s3_key=f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=1024000,
        step=1000,
        epoch=5,
        metrics={"loss": 0.1234},
        is_best=True,
    )

    # Create a different checkpoint
    checkpoint3 = JobCheckpoint(
        job_id=job_id,
        timestamp=timestamp,
        s3_bucket="different-bucket",
        s3_key=f"jobs/{job_id}/checkpoints/model_{timestamp}.pt",
        size_bytes=1024000,
        step=1000,
        epoch=5,
        metrics={"loss": 0.1234},
        is_best=True,
    )

    # Test equality
    assert checkpoint1 == checkpoint2
    assert checkpoint1 != checkpoint3
    assert checkpoint1 != "not a checkpoint"

    # Check that hash works
    checkpoints = {checkpoint1, checkpoint2, checkpoint3}
    assert len(checkpoints) == 2


@pytest.mark.unit
def test_parse_dynamodb_map():
    """Test the _parse_dynamodb_map function"""
    # Create a test map with various types
    dynamodb_map = {
        "string": {"S": "value"},
        "number": {"N": "123"},
        "float": {"N": "123.45"},
        "bool": {"BOOL": True},
        "null": {"NULL": True},
        "map": {"M": {"inner": {"S": "value"}, "innerNum": {"N": "456"}}},
        "list": {"L": [{"S": "item1"}, {"N": "789"}, {"BOOL": False}]},
    }

    result = _parse_dynamodb_map(dynamodb_map)

    assert result["string"] == "value"
    assert result["number"] == 123
    assert result["float"] == 123.45
    assert result["bool"] is True
    assert result["null"] is None
    assert result["map"]["inner"] == "value"
    assert result["map"]["innerNum"] == 456
    assert result["list"][0] == "item1"
    assert result["list"][1] == 789
    assert result["list"][2] is False


@pytest.mark.unit
def test_parse_dynamodb_value():
    """Test the _parse_dynamodb_value function"""
    assert _parse_dynamodb_value({"S": "value"}) == "value"
    assert _parse_dynamodb_value({"N": "123"}) == 123
    assert _parse_dynamodb_value({"N": "123.45"}) == 123.45
    assert _parse_dynamodb_value({"BOOL": True}) is True
    assert _parse_dynamodb_value({"NULL": True}) is None

    # Test list
    list_value = {"L": [{"S": "item1"}, {"N": "123"}]}
    result = _parse_dynamodb_value(list_value)
    assert result[0] == "item1"
    assert result[1] == 123

    # Test map
    map_value = {"M": {"key": {"S": "value"}, "num": {"N": "123"}}}
    result = _parse_dynamodb_value(map_value)
    assert result["key"] == "value"
    assert result["num"] == 123

    # Test empty value
    assert _parse_dynamodb_value({}) is None


@pytest.mark.unit
def test_itemToJobCheckpoint(
    example_job_checkpoint, example_job_checkpoint_minimal
):
    """Test that a DynamoDB item can be converted to a JobCheckpoint"""
    # Convert to item and back for the full example
    item = example_job_checkpoint.to_item()
    checkpoint = item_to_job_checkpoint(item)

    assert checkpoint.job_id == example_job_checkpoint.job_id
    assert checkpoint.timestamp == example_job_checkpoint.timestamp
    assert checkpoint.s3_bucket == example_job_checkpoint.s3_bucket
    assert checkpoint.s3_key == example_job_checkpoint.s3_key
    assert checkpoint.size_bytes == example_job_checkpoint.size_bytes
    assert checkpoint.step == example_job_checkpoint.step
    assert checkpoint.epoch == example_job_checkpoint.epoch
    assert checkpoint.model_state == example_job_checkpoint.model_state
    assert checkpoint.optimizer_state == example_job_checkpoint.optimizer_state
    assert checkpoint.metrics == example_job_checkpoint.metrics
    assert checkpoint.is_best == example_job_checkpoint.is_best

    # Convert to item and back for the minimal example
    item_minimal = example_job_checkpoint_minimal.to_item()
    checkpoint_minimal = item_to_job_checkpoint(item_minimal)

    assert checkpoint_minimal.job_id == example_job_checkpoint_minimal.job_id
    assert (
        checkpoint_minimal.timestamp
        == example_job_checkpoint_minimal.timestamp
    )
    assert checkpoint_minimal.metrics == {}
    assert checkpoint_minimal.is_best is False

    # Test with invalid item
    with pytest.raises(
        ValueError, match="Error converting item to JobCheckpoint"
    ):
        item_to_job_checkpoint({"job_id": {"S": "invalid-job-id"}})
