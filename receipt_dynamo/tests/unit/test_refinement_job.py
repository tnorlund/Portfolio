# pylint: disable=redefined-outer-name
from datetime import datetime

import pytest

from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities import OCRJob, item_to_ocr_job


@pytest.fixture
def example_ocr_job():
    return OCRJob(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        s3_bucket="test-bucket",
        s3_key="images/test.png",
        created_at=datetime(2025, 5, 1, 12, 0, 0),
        updated_at=datetime(2025, 5, 1, 13, 0, 0),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.REFINEMENT,
        receipt_id=123,
    )


@pytest.mark.unit
def test_ocr_job_init_valid(example_ocr_job):
    assert example_ocr_job.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_ocr_job.job_id == "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_ocr_job.status == OCRStatus.PENDING.value
    assert example_ocr_job.job_type == OCRJobType.REFINEMENT.value
    assert example_ocr_job.receipt_id == 123


@pytest.mark.unit
def test_ocr_job_invalid_uuid():
    with pytest.raises(ValueError):
        OCRJob(
            image_id="not-a-uuid",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_ocr_job_invalid_status_value():
    with pytest.raises(
        ValueError,
        match="OCRStatus must be one of:",
    ):
        OCRJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            status="NOT_A_STATUS",
        )
    with pytest.raises(
        ValueError,
        match="OCRStatus must be a str or OCRStatus instance",
    ):
        OCRJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            status=123,
        )


@pytest.mark.unit
def test_ocr_job_key_generation(example_ocr_job):
    assert example_ocr_job.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "OCR_JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
    }


@pytest.mark.unit
def test_ocr_job_gsi1_key(example_ocr_job):
    assert example_ocr_job.gsi1_key() == {
        "GSI1PK": {"S": "OCR_JOB_STATUS#PENDING"},
        "GSI1SK": {"S": "OCR_JOB#4f52804b-2fad-4e00-92c8-b593da3a8ed3"},
    }


@pytest.mark.unit
def test_ocr_job_to_item(example_ocr_job):
    item = example_ocr_job.to_item()
    assert item["status"]["S"] == "PENDING"
    assert item["TYPE"]["S"] == "OCR_JOB"


@pytest.mark.unit
def test_ocr_job_repr(example_ocr_job):
    out = repr(example_ocr_job)
    assert "OCRJob(" in out
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in out


@pytest.mark.unit
def test_ocr_job_iter(example_ocr_job):
    keys = dict(example_ocr_job).keys()
    assert "image_id" in keys
    assert "s3_bucket" in keys
    assert "status" in keys


@pytest.mark.unit
def test_ocr_job_equality(example_ocr_job):
    clone = OCRJob(**dict(example_ocr_job))
    assert example_ocr_job == clone
    data = dict(example_ocr_job)
    data["status"] = OCRStatus.COMPLETED.value
    altered = OCRJob(**data)
    assert example_ocr_job != altered
    assert example_ocr_job != "not-a-ocr-job"


@pytest.mark.unit
def test_item_to_ocr_job_roundtrip(example_ocr_job):
    item = example_ocr_job.to_item()
    reconstructed = item_to_ocr_job(item)
    assert reconstructed == example_ocr_job


@pytest.mark.unit
def test_ocr_job_invalid_s3_bucket():
    with pytest.raises(ValueError, match="s3_bucket must be a string"):
        OCRJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket=123,
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_ocr_job_invalid_s3_key():
    with pytest.raises(ValueError, match="s3_key must be a string"):
        OCRJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key=123,
            created_at=datetime(2025, 5, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_ocr_job_invalid_created_at():
    with pytest.raises(ValueError, match="created_at must be a datetime"):
        OCRJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at="2025-05-01T12:00:00",
        )


@pytest.mark.unit
def test_ocr_job_invalid_updated_at():
    with pytest.raises(
        ValueError, match="updated_at must be a datetime or None"
    ):
        OCRJob(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
            s3_bucket="test-bucket",
            s3_key="images/test.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            updated_at="2025-05-01T13:00:00",
        )


@pytest.mark.unit
def test_item_to_ocr_job_missing_keys():
    # missing s3_bucket, s3_key, created_at, status
    item = {
        "PK": {"S": "IMAGE#uuid"},
        "SK": {"S": "OCR_JOB#uuid"},
        "TYPE": {"S": "OCR_JOB"},
    }
    with pytest.raises(ValueError, match=r"missing keys"):
        item_to_ocr_job(item)


@pytest.mark.unit
def test_item_to_ocr_job_malformed_updated_at():
    bad_item = {
        "PK": {"S": "IMAGE#uuid"},
        "SK": {"S": "OCR_JOB#uuid"},
        "TYPE": {"S": "OCR_JOB"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "created_at": {"S": datetime.now().isoformat()},
        "updated_at": {"S": "not-a-date"},
        "status": {"S": "PENDING"},
        "job_type": {"S": "REFINEMENT"},
        "receipt_id": {"N": "123"},
    }
    with pytest.raises(ValueError, match="Error converting item to OCRJob"):
        item_to_ocr_job(bad_item)


@pytest.mark.unit
def test_ocr_job_hash(example_ocr_job):
    job_set = {example_ocr_job}
    assert example_ocr_job in job_set
    h = hash(example_ocr_job)
    assert isinstance(h, int)
    # Hash is consistent
    assert h == hash(example_ocr_job)


@pytest.mark.unit
def test_item_to_ocr_job_unexpected_error():
    # updated_at is present but has None in ["S"], which will raise TypeError
    # in fromisoformat
    bad_item = {
        "PK": {"S": "IMAGE#uuid"},
        "SK": {"S": "OCR_JOB#uuid"},
        "TYPE": {"S": "OCR_JOB"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "created_at": {"S": datetime.now().isoformat()},
        "status": {"S": "PENDING"},
        "updated_at": {"S": None},
        "job_type": {"S": "REFINEMENT"},
        "receipt_id": {"N": "123"},
    }
    with pytest.raises(ValueError, match="Error converting item to OCRJob"):
        item_to_ocr_job(bad_item)
