import pytest
from datetime import datetime
from typing import Literal
from botocore.exceptions import ClientError, ParamValidationError
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import RefinementJob
from receipt_dynamo.constants import RefinementStatus


@pytest.fixture
def sample_refinement_job():
    return RefinementJob(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        s3_bucket="test-bucket",
        s3_key="images/test.png",
        created_at=datetime(2025, 5, 1, 12, 0, 0),
        updated_at=datetime(2025, 5, 1, 13, 0, 0),
        status=RefinementStatus.PENDING,
    )


# -------------------------------------------------------------------
#                        addRefinementJob
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addRefinementJob_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_refinement_job: RefinementJob,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addRefinementJob(sample_refinement_job)

    # Assert
    retrieved_job = client.getRefinementJob(
        sample_refinement_job.image_id, sample_refinement_job.job_id
    )
    assert retrieved_job == sample_refinement_job


def test_addRefinementJob_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_refinement_job: RefinementJob,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addRefinementJob(sample_refinement_job)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addRefinementJob(sample_refinement_job)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "refinement_job parameter is required and cannot be None."),
        (
            "not-a-refinement-job",
            "refinement_job must be an instance of the RefinementJob class.",
        ),
    ],
)
def test_addRefinementJob_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    sample_refinement_job: RefinementJob,
    mocker,
    invalid_input,
    expected_error,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.addRefinementJob(invalid_input)  # type: ignore


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
            "Could not add refinement job to DynamoDB",
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
            "Could not add refinement job to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addRefinementJob_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_refinement_job: RefinementJob,
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
        client.addRefinementJob(sample_refinement_job)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        getRefinementJob
# -------------------------------------------------------------------
