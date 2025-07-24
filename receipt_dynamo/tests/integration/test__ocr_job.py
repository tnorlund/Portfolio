from datetime import datetime
from typing import Literal

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities import OCRJob
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)


@pytest.fixture
def sample_ocr_job():
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


# -------------------------------------------------------------------
#                        addRefinementJob
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addOCRJob_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_ocr_job(sample_ocr_job)

    # Assert
    retrieved_job = client.get_ocr_job(
        sample_ocr_job.image_id, sample_ocr_job.job_id
    )
    assert retrieved_job == sample_ocr_job


def test_addOCRJob_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_ocr_job(sample_ocr_job)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError, match="Entity already exists"):
        client.add_ocr_job(sample_ocr_job)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Ocr_job parameter is required and cannot be None"),
        (
            "not-a-ocr-job",
            "Ocr_job must be an instance of the OCRJob class",
        ),
    ],
)
def test_addOCRJob_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
    mocker,
    invalid_input,
    expected_error,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.add_ocr_job(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item already exists",
            "already exists",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_ocr_job",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Unknown error in add_ocr_job",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addOCRJob_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_ocr_job: OCRJob,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.add_ocr_job(sample_ocr_job)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        getOCRJob
# -------------------------------------------------------------------
