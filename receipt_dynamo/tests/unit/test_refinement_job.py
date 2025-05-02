from datetime import datetime
import pytest

from receipt_dynamo.entities import (
    RefinementJob,
    itemToRefinementJob,
)
from receipt_dynamo.constants import RefinementStatus


@pytest.fixture
def example_refinement_job():
    return RefinementJob(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        s3_bucket="test-bucket",
        s3_key="images/test.png",
        created_at=datetime(2025, 5, 1, 12, 0, 0),
        updated_at=datetime(2025, 5, 1, 13, 0, 0),
        status=RefinementStatus.PENDING,
    )


@pytest.mark.unit
def test_refinement_job_init_valid(example_refinement_job):
    assert (
        example_refinement_job.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_refinement_job.job_id == "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_refinement_job.status == RefinementStatus.PENDING.value


@pytest.mark.unit
def test_refinement_job_invalid_uuid():
    with pytest.raises(ValueError):
        RefinementJob(
            image_id="not-a-uuid",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_refinement_job_invalid_status_value():
    with pytest.raises(
        ValueError,
        match="status must be one of: PENDING, COMPLETED, FAILED",
    ):
        RefinementJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            status="NOT_A_STATUS",
        )
    with pytest.raises(
        ValueError,
        match="status must be a RefinementStatus or a string",
    ):
        RefinementJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            status=123,
        )


@pytest.mark.unit
def test_refinement_job_key_generation(example_refinement_job):
    assert example_refinement_job.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "REFINEMENT_JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
    }


@pytest.mark.unit
def test_refinement_job_gsi1_key(example_refinement_job):
    assert example_refinement_job.gsi1_key() == {
        "PK": {"S": "REFINEMENT_JOB_STATUS#PENDING"},
        "SK": {"S": "REFINEMENT_JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
    }


@pytest.mark.unit
def test_refinement_job_to_item(example_refinement_job):
    item = example_refinement_job.to_item()
    assert item["status"]["S"] == "PENDING"
    assert item["TYPE"]["S"] == "REFINEMENT_JOB"


@pytest.mark.unit
def test_refinement_job_repr(example_refinement_job):
    out = repr(example_refinement_job)
    assert "RefinementJob(" in out
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in out


@pytest.mark.unit
def test_refinement_job_iter(example_refinement_job):
    keys = dict(example_refinement_job).keys()
    assert "image_id" in keys
    assert "s3_bucket" in keys
    assert "status" in keys


@pytest.mark.unit
def test_refinement_job_equality(example_refinement_job):
    clone = RefinementJob(**dict(example_refinement_job))
    assert example_refinement_job == clone
    data = dict(example_refinement_job)
    data["status"] = RefinementStatus.COMPLETED.value
    altered = RefinementJob(**data)
    assert example_refinement_job != altered
    assert example_refinement_job != "not-a-refinement-job"


@pytest.mark.unit
def test_item_to_refinement_job_roundtrip(example_refinement_job):
    item = example_refinement_job.to_item()
    reconstructed = itemToRefinementJob(item)
    assert reconstructed == example_refinement_job


@pytest.mark.unit
def test_refinement_job_invalid_s3_bucket():
    with pytest.raises(ValueError, match="s3_bucket must be a string"):
        RefinementJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket=123,
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_refinement_job_invalid_s3_key():
    with pytest.raises(ValueError, match="s3_key must be a string"):
        RefinementJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key=123,
            created_at=datetime(2025, 5, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_refinement_job_invalid_created_at():
    with pytest.raises(ValueError, match="created_at must be a datetime"):
        RefinementJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at="2025-05-01T12:00:00",
        )


@pytest.mark.unit
def test_refinement_job_invalid_updated_at():
    with pytest.raises(
        ValueError, match="updated_at must be a datetime or None"
    ):
        RefinementJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            updated_at="2025-05-01T13:00:00",
        )


@pytest.mark.unit
def test_item_to_refinement_job_missing_keys():
    # missing s3_bucket, s3_key, created_at, status
    item = {
        "PK": {"S": "IMAGE#uuid"},
        "SK": {"S": "REFINEMENT_JOB#uuid"},
        "TYPE": {"S": "REFINEMENT_JOB"},
    }
    with pytest.raises(ValueError, match=r"missing keys"):
        itemToRefinementJob(item)


@pytest.mark.unit
def test_item_to_refinement_job_malformed_updated_at():
    bad_item = {
        "PK": {"S": "IMAGE#uuid"},
        "SK": {"S": "REFINEMENT_JOB#uuid"},
        "TYPE": {"S": "REFINEMENT_JOB"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "created_at": {"S": datetime.now().isoformat()},
        "updated_at": {"S": "not-a-date"},
        "status": {"S": "PENDING"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to RefinementJob"
    ):
        itemToRefinementJob(bad_item)


@pytest.mark.unit
def test_refinement_job_hash(example_refinement_job):
    job_set = {example_refinement_job}
    assert example_refinement_job in job_set
    h = hash(example_refinement_job)
    assert isinstance(h, int)
    # Hash is consistent
    assert h == hash(example_refinement_job)


@pytest.mark.unit
def test_item_to_refinement_job_unexpected_error():
    # updated_at is present but has None in ["S"], which will raise TypeError in fromisoformat
    bad_item = {
        "PK": {"S": "IMAGE#uuid"},
        "SK": {"S": "REFINEMENT_JOB#uuid"},
        "TYPE": {"S": "REFINEMENT_JOB"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "created_at": {"S": datetime.now().isoformat()},
        "status": {"S": "PENDING"},
        "updated_at": {"S": None},
    }
    with pytest.raises(
        ValueError, match="Error converting item to RefinementJob"
    ):
        itemToRefinementJob(bad_item)
