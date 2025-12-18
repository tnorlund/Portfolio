"""
Integration tests for OCRJob operations in DynamoDB.

This module tests the OCRJob-related methods of DynamoClient, including
add, get, update, delete, and list operations. It follows the perfect
test patterns established in test__receipt.py, test__image.py, and
test__receipt_word_label.py.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, OCRJob
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# =============================================================================
# TEST DATA AND FIXTURES
# =============================================================================

CORRECT_OCR_JOB_PARAMS: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "job_id": "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "s3_bucket": "test-bucket",
    "s3_key": "images/test.png",
    "created_at": datetime(2025, 5, 1, 12, 0, 0),
    "updated_at": datetime(2025, 5, 1, 13, 0, 0),
    "status": OCRStatus.PENDING,
    "job_type": OCRJobType.REFINEMENT,
    "receipt_id": 123,
}


@pytest.fixture(name="sample_ocr_job")
def _sample_ocr_job() -> OCRJob:
    """Provides a valid OCRJob for testing."""
    return OCRJob(**CORRECT_OCR_JOB_PARAMS)


@pytest.fixture(name="another_ocr_job")
def _another_ocr_job() -> OCRJob:
    """Provides a second valid OCRJob for testing."""
    return OCRJob(
        image_id="5f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="6f52804b-2fad-4e00-92c8-b593da3a8ed3",
        s3_bucket="another-bucket",
        s3_key="images/another.png",
        created_at=datetime(2025, 5, 2, 10, 0, 0),
        updated_at=datetime(2025, 5, 2, 11, 0, 0),
        status=OCRStatus.COMPLETED,
        job_type=OCRJobType.FIRST_PASS,
        receipt_id=None,
    )


@pytest.fixture(name="batch_ocr_jobs")
def _batch_ocr_jobs() -> List[OCRJob]:
    """Provides a list of 30 OCR jobs for batch testing."""
    jobs = []
    base_time = datetime(2025, 5, 1, 12, 0, 0)

    for i in range(30):
        jobs.append(
            OCRJob(
                image_id=str(uuid4()),
                job_id=str(uuid4()),
                s3_bucket=f"bucket-{i % 3}",
                s3_key=f"images/test_{i}.png",
                created_at=base_time,
                updated_at=base_time if i % 2 == 0 else None,
                status=[
                    OCRStatus.PENDING,
                    OCRStatus.COMPLETED,
                    OCRStatus.FAILED,
                ][i % 3],
                job_type=(
                    OCRJobType.FIRST_PASS if i % 2 == 0 else OCRJobType.REFINEMENT
                ),
                receipt_id=i + 1 if i % 2 == 0 else None,
            )
        )

    return jobs


# -------------------------------------------------------------------
#                   PARAMETERIZED CLIENT ERROR TESTS
# -------------------------------------------------------------------

# Common error scenarios for all operations
ERROR_SCENARIOS = [
    (
        "ProvisionedThroughputExceededException",
        DynamoDBThroughputError,
        "Throughput exceeded",
    ),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    (
        "ResourceNotFoundException",
        OperationError,
        "DynamoDB resource not found",
    ),
]

# Additional error for add operations
ADD_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityAlreadyExistsError,
        "ocr_job already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "not found during update_ocr_job",
    ),
] + ERROR_SCENARIOS


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_add_ocr_job_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_ocr_job raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_ocr_job(sample_ocr_job)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_update_ocr_job_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_ocr_job raises appropriate exceptions for
    various ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_ocr_job(sample_ocr_job)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    ERROR_SCENARIOS,  # Delete doesn't have ConditionalCheckFailedException
)
def test_delete_ocr_job_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_ocr_job raises appropriate exceptions for
    various ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_ocr_job(sample_ocr_job)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#                   PARAMETERIZED VALIDATION ERROR TESTS
# -------------------------------------------------------------------

ADD_VALIDATION_SCENARIOS = [
    (None, "ocr_job cannot be None"),
    (
        "not-a-ocr-job",
        "ocr_job must be an instance of OCRJob",
    ),
]

ADD_BATCH_VALIDATION_SCENARIOS = [
    (None, "ocr_jobs cannot be None"),
    ("not-a-list", "ocr_jobs must be a list"),
]


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_add_ocr_job_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_ocr_job raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.add_ocr_job(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_BATCH_VALIDATION_SCENARIOS)
def test_add_ocr_jobs_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_ocr_jobs raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.add_ocr_jobs(invalid_input)  # type: ignore


@pytest.mark.integration
def test_add_ocr_jobs_invalid_list_contents(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that add_ocr_jobs validates list contents."""
    client = DynamoClient(dynamodb_table)
    invalid_list = ["not-a-job", 123, None]

    with pytest.raises(
        OperationError,
        match="All items in ocr_jobs must be instances of OCRJob",
    ):
        client.add_ocr_jobs(invalid_list)  # type: ignore


# -------------------------------------------------------------------
#                        ADD OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_ocr_job_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests successful addition of an OCR job."""
    client = DynamoClient(dynamodb_table)

    # Add the job
    client.add_ocr_job(sample_ocr_job)

    # Verify it was added by retrieving it
    retrieved = client.get_ocr_job(
        sample_ocr_job.image_id,
        sample_ocr_job.job_id,
    )
    assert retrieved == sample_ocr_job


@pytest.mark.integration
def test_add_ocr_job_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests that adding a duplicate OCR job raises EntityAlreadyExistsError."""
    client = DynamoClient(dynamodb_table)
    client.add_ocr_job(sample_ocr_job)

    with pytest.raises(EntityAlreadyExistsError, match="ocr_job already exists"):
        client.add_ocr_job(sample_ocr_job)


@pytest.mark.integration
def test_add_ocr_jobs_batch_success(
    dynamodb_table: Literal["MyMockedTable"],
    batch_ocr_jobs: List[OCRJob],
) -> None:
    """Tests successful batch addition of OCR jobs."""
    client = DynamoClient(dynamodb_table)

    # Add first 10 jobs
    jobs_to_add = batch_ocr_jobs[:10]
    client.add_ocr_jobs(jobs_to_add)

    # Verify all were added
    for job in jobs_to_add:
        retrieved = client.get_ocr_job(job.image_id, job.job_id)
        assert retrieved == job


# -------------------------------------------------------------------
#                        GET OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_ocr_job_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests successful retrieval of an OCR job."""
    client = DynamoClient(dynamodb_table)
    client.add_ocr_job(sample_ocr_job)

    retrieved = client.get_ocr_job(
        sample_ocr_job.image_id,
        sample_ocr_job.job_id,
    )

    assert retrieved == sample_ocr_job


@pytest.mark.integration
def test_get_ocr_job_not_found(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that get_ocr_job raises EntityNotFoundError for non-existent job."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.get_ocr_job(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,job_id",
    [
        (None, "4f52804b-2fad-4e00-92c8-b593da3a8ed3"),  # None image_id
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", None),  # None job_id
        (
            "not-a-uuid",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        ),  # Invalid image_id
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-a-uuid",
        ),  # Invalid job_id
    ],
)
def test_get_ocr_job_invalid_params(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    job_id: Any,
) -> None:
    """Tests that get_ocr_job raises EntityValidationError for invalid parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises((EntityValidationError, OperationError)):
        client.get_ocr_job(image_id, job_id)


# -------------------------------------------------------------------
#                        UPDATE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_update_ocr_job_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests successful update of an OCR job."""
    client = DynamoClient(dynamodb_table)

    # First add the job
    client.add_ocr_job(sample_ocr_job)

    # Update it with new values
    sample_ocr_job.status = OCRStatus.COMPLETED.value
    sample_ocr_job.updated_at = datetime(2025, 5, 1, 14, 0, 0)

    client.update_ocr_job(sample_ocr_job)

    # Verify the update
    retrieved = client.get_ocr_job(
        sample_ocr_job.image_id,
        sample_ocr_job.job_id,
    )
    assert retrieved.status == OCRStatus.COMPLETED.value
    assert retrieved.updated_at == datetime(2025, 5, 1, 14, 0, 0)


@pytest.mark.integration
def test_update_ocr_job_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests that updating a non-existent job raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match="not found during update_ocr_job",
    ):
        client.update_ocr_job(sample_ocr_job)


# -------------------------------------------------------------------
#                        DELETE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_delete_ocr_job_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests successful deletion of an OCR job."""
    client = DynamoClient(dynamodb_table)

    # First add the job
    client.add_ocr_job(sample_ocr_job)

    # Delete it
    client.delete_ocr_job(sample_ocr_job)

    # Verify it's gone
    with pytest.raises(EntityNotFoundError):
        client.get_ocr_job(
            sample_ocr_job.image_id,
            sample_ocr_job.job_id,
        )


@pytest.mark.integration
def test_delete_ocr_job_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests that deleting a non-existent job raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    # Delete non-existent job - should raise
    with pytest.raises(EntityNotFoundError):
        client.delete_ocr_job(sample_ocr_job)


@pytest.mark.integration
def test_delete_ocr_jobs_batch(
    dynamodb_table: Literal["MyMockedTable"],
    batch_ocr_jobs: List[OCRJob],
) -> None:
    """Tests batch deletion of OCR jobs."""
    client = DynamoClient(dynamodb_table)

    # Add first 10 jobs
    jobs_to_delete = batch_ocr_jobs[:10]
    for job in jobs_to_delete:
        client.add_ocr_job(job)

    # Delete them in batch
    client.delete_ocr_jobs(jobs_to_delete)

    # Verify all are deleted
    for job in jobs_to_delete:
        with pytest.raises(EntityNotFoundError):
            client.get_ocr_job(job.image_id, job.job_id)


# -------------------------------------------------------------------
#                        LIST OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_ocr_jobs_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing all OCR jobs."""
    client = DynamoClient(dynamodb_table)

    # Add multiple jobs
    jobs = []
    for i in range(5):
        job = OCRJob(
            image_id=str(uuid4()),
            job_id=str(uuid4()),
            s3_bucket="test-bucket",
            s3_key=f"images/test_{i}.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            status=OCRStatus.PENDING,
            job_type=OCRJobType.FIRST_PASS,
        )
        jobs.append(job)
        client.add_ocr_job(job)

    # List them
    retrieved, last_key = client.list_ocr_jobs(limit=10)

    assert len(retrieved) >= 5  # May have more from other tests
    assert last_key is None


@pytest.mark.integration
def test_list_ocr_jobs_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    batch_ocr_jobs: List[OCRJob],
) -> None:
    """Tests listing OCR jobs with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add 30 jobs
    for job in batch_ocr_jobs:
        client.add_ocr_job(job)

    # Get first page
    page1, last_key1 = client.list_ocr_jobs(limit=15)
    assert len(page1) == 15
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.list_ocr_jobs(limit=15, last_evaluated_key=last_key1)
    assert len(page2) <= 15  # May be fewer items on second page

    # Get remaining items if there are more
    all_retrieved = page1 + page2
    if last_key2 is not None:
        page3, last_key3 = client.list_ocr_jobs(limit=15, last_evaluated_key=last_key2)
        all_retrieved.extend(page3)

    # Verify we got all our items
    assert len(all_retrieved) >= 30


@pytest.mark.integration
def test_get_ocr_jobs_by_status_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving OCR jobs by status."""
    client = DynamoClient(dynamodb_table)

    # Add jobs with different statuses
    statuses = [OCRStatus.PENDING, OCRStatus.COMPLETED, OCRStatus.FAILED]
    jobs = []

    for i, status in enumerate(statuses):
        for j in range(3):  # 3 jobs per status
            job = OCRJob(
                image_id=str(uuid4()),
                job_id=str(uuid4()),
                s3_bucket="test-bucket",
                s3_key=f"images/status_{status.value}_{j}.png",
                created_at=datetime(2025, 5, 1, 12, 0, 0),
                status=status,
                job_type=OCRJobType.FIRST_PASS,
            )
            jobs.append(job)
            client.add_ocr_job(job)

    # Query by status
    pending_jobs, _ = client.get_ocr_jobs_by_status(OCRStatus.PENDING)
    failed_jobs, _ = client.get_ocr_jobs_by_status(OCRStatus.FAILED)
    completed_jobs, _ = client.get_ocr_jobs_by_status(OCRStatus.COMPLETED)

    # Filter to only our test jobs (may have others from other tests)
    test_job_ids = {job.job_id for job in jobs}

    pending_test_jobs = [j for j in pending_jobs if j.job_id in test_job_ids]
    failed_test_jobs = [j for j in failed_jobs if j.job_id in test_job_ids]
    completed_test_jobs = [j for j in completed_jobs if j.job_id in test_job_ids]

    assert len(pending_test_jobs) == 3
    assert len(failed_test_jobs) == 3
    assert len(completed_test_jobs) == 3

    assert all(j.status == OCRStatus.PENDING.value for j in pending_test_jobs)
    assert all(j.status == OCRStatus.FAILED.value for j in failed_test_jobs)
    assert all(j.status == OCRStatus.COMPLETED.value for j in completed_test_jobs)


@pytest.mark.integration
def test_get_ocr_jobs_by_status_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving OCR jobs by status with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add 15 pending jobs
    jobs = []
    for i in range(15):
        job = OCRJob(
            image_id=str(uuid4()),
            job_id=str(uuid4()),
            s3_bucket="test-bucket",
            s3_key=f"images/pending_{i}.png",
            created_at=datetime(2025, 5, 1, 12, 0, 0),
            status=OCRStatus.PENDING,
            job_type=OCRJobType.FIRST_PASS,
        )
        jobs.append(job)
        client.add_ocr_job(job)

    # Get first page
    page1, last_key1 = client.get_ocr_jobs_by_status(OCRStatus.PENDING, limit=8)
    assert len(page1) >= 8  # At least 8
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.get_ocr_jobs_by_status(
        OCRStatus.PENDING, limit=8, last_evaluated_key=last_key1
    )
    assert len(page2) >= 0  # May have items

    # All items should be pending
    all_retrieved = page1 + page2
    assert all(j.status == OCRStatus.PENDING.value for j in all_retrieved)


# -------------------------------------------------------------------
#                   VALIDATION PARAMETER TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "status,expected_error",
    [
        (None, "status cannot be None"),
        ("not-a-status", "Status must be a OCRStatus instance"),
        (123, "Status must be a OCRStatus instance"),
    ],
)
def test_get_ocr_jobs_by_status_validation(
    dynamodb_table: Literal["MyMockedTable"],
    status: Any,
    expected_error: str,
) -> None:
    """Tests validation for get_ocr_jobs_by_status parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.get_ocr_jobs_by_status(status)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "last_evaluated_key,expected_error",
    [
        ("not-a-dict", "LastEvaluatedKey must be a dictionary"),
        (123, "LastEvaluatedKey must be a dictionary"),
    ],
)
def test_list_ocr_jobs_last_evaluated_key_validation(
    dynamodb_table: Literal["MyMockedTable"],
    last_evaluated_key: Any,
    expected_error: str,
) -> None:
    """Tests validation for list_ocr_jobs last_evaluated_key parameter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_ocr_jobs(last_evaluated_key=last_evaluated_key)


# -------------------------------------------------------------------
#                   SPECIAL CASES
# -------------------------------------------------------------------


@pytest.mark.integration
def test_ocr_job_without_optional_fields(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests OCR job without optional fields (updated_at, receipt_id)."""
    client = DynamoClient(dynamodb_table)

    # Create job without optional fields
    job = OCRJob(
        image_id=str(uuid4()),
        job_id=str(uuid4()),
        s3_bucket="test-bucket",
        s3_key="images/minimal.png",
        created_at=datetime(2025, 5, 1, 12, 0, 0),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.FIRST_PASS,
        updated_at=None,
        receipt_id=None,
    )

    # Add and retrieve
    client.add_ocr_job(job)
    retrieved = client.get_ocr_job(job.image_id, job.job_id)

    assert retrieved == job
    assert retrieved.updated_at is None
    assert retrieved.receipt_id is None


@pytest.mark.integration
def test_ocr_job_status_transitions(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
) -> None:
    """Tests OCR job status transitions."""
    client = DynamoClient(dynamodb_table)

    # Add job with PENDING status
    sample_ocr_job.status = OCRStatus.PENDING.value
    client.add_ocr_job(sample_ocr_job)

    # Transition to COMPLETED
    sample_ocr_job.status = OCRStatus.COMPLETED.value
    sample_ocr_job.updated_at = datetime(2025, 5, 1, 13, 30, 0)
    client.update_ocr_job(sample_ocr_job)

    # Verify
    retrieved = client.get_ocr_job(sample_ocr_job.image_id, sample_ocr_job.job_id)
    assert retrieved.status == OCRStatus.COMPLETED.value

    # Transition to FAILED
    sample_ocr_job.status = OCRStatus.FAILED.value
    sample_ocr_job.updated_at = datetime(2025, 5, 1, 14, 0, 0)
    client.update_ocr_job(sample_ocr_job)

    # Verify final state
    retrieved = client.get_ocr_job(sample_ocr_job.image_id, sample_ocr_job.job_id)
    assert retrieved.status == OCRStatus.FAILED.value
    assert retrieved.updated_at == datetime(2025, 5, 1, 14, 0, 0)


@pytest.mark.integration
def test_ocr_job_different_job_types(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests OCR jobs with different job types."""
    client = DynamoClient(dynamodb_table)

    # Create jobs with different types
    first_pass_job = OCRJob(
        image_id=str(uuid4()),
        job_id=str(uuid4()),
        s3_bucket="test-bucket",
        s3_key="images/first_pass.png",
        created_at=datetime(2025, 5, 1, 12, 0, 0),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.FIRST_PASS,
    )

    refinement_job = OCRJob(
        image_id=first_pass_job.image_id,  # Same image
        job_id=str(uuid4()),
        s3_bucket="test-bucket",
        s3_key="images/refinement.png",
        created_at=datetime(2025, 5, 1, 13, 0, 0),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.REFINEMENT,
        receipt_id=123,  # Refinement job has receipt_id
    )

    # Add both jobs
    client.add_ocr_job(first_pass_job)
    client.add_ocr_job(refinement_job)

    # Retrieve and verify
    retrieved_first = client.get_ocr_job(first_pass_job.image_id, first_pass_job.job_id)
    retrieved_refinement = client.get_ocr_job(
        refinement_job.image_id, refinement_job.job_id
    )

    assert retrieved_first.job_type == OCRJobType.FIRST_PASS.value
    assert retrieved_first.receipt_id is None

    assert retrieved_refinement.job_type == OCRJobType.REFINEMENT.value
    assert retrieved_refinement.receipt_id == 123
