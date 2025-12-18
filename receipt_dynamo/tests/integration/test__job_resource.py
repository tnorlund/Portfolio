import uuid
from datetime import datetime
from typing import Type

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo.data._job_resource import validate_last_evaluated_key
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_resource import JobResource

pytestmark = pytest.mark.integration


@pytest.fixture
def job_resource_dynamo(dynamodb_table):
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
def sample_job_resource(sample_job):
    """Provides a sample JobResource for testing."""
    return JobResource(
        job_id=sample_job.job_id,
        resource_id=str(uuid.uuid4()),
        instance_id="i-0123456789abcdef",
        instance_type="p3.2xlarge",
        resource_type="gpu",
        status="allocated",
        allocated_at=datetime.now().isoformat(),
        released_at=None,
        resource_config={"memory": "16GB", "model": "NVIDIA A100"},
    )


@pytest.fixture
def sample_job_resource_2(sample_job):
    """Provides a second sample JobResource for testing."""
    return JobResource(
        job_id=sample_job.job_id,
        resource_id=str(uuid.uuid4()),
        instance_id="i-9876543210abcdef",
        instance_type="c5.4xlarge",
        resource_type="cpu",
        status="allocated",
        allocated_at=datetime.now().isoformat(),
        released_at=None,
        resource_config={
            "cpu_cores": 16,
            "memory": "64GB",
            "instance_type": "c5.4xlarge",
        },
    )


# ---
# addJobResource
# ---


@pytest.mark.integration
def test_addJobResource_success(job_resource_dynamo, sample_job, sample_job_resource):
    """Test adding a job resource successfully"""
    # Add the job first (since it's a foreign key reference)
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)

    # Verify the job resource was added by retrieving it
    resource = job_resource_dynamo.get_job_resource(
        sample_job_resource.job_id, sample_job_resource.resource_id
    )
    assert resource.job_id == sample_job_resource.job_id
    assert resource.resource_id == sample_job_resource.resource_id
    assert resource.resource_type == sample_job_resource.resource_type
    assert resource.status == sample_job_resource.status
    assert resource.allocated_at == sample_job_resource.allocated_at
    assert resource.released_at == sample_job_resource.released_at
    assert resource.resource_config == sample_job_resource.resource_config


@pytest.mark.integration
def test_addJobResource_raises_value_error(job_resource_dynamo):
    """Test that addJobResource raises ValueError when job_resource is None"""
    with pytest.raises(
        OperationError,
        match="job_resource cannot be None",
    ):
        job_resource_dynamo.add_job_resource(None)


@pytest.mark.integration
def test_addJobResource_raises_value_error_not_instance(job_resource_dynamo):
    """
    Test that addJobResource raises ValueError when job_resource is not an
    instance of JobResource
    """
    with pytest.raises(
        OperationError,
        match="job_resource must be an instance of JobResource",
    ):
        job_resource_dynamo.add_job_resource("not a job resource")


@pytest.mark.integration
def test_addJobResource_raises_conditional_check_failed(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """
    Test that addJobResource raises ValueError when the job resource
    already exists
    """
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource first
    job_resource_dynamo.add_job_resource(sample_job_resource)

    # Try to add it again
    with pytest.raises(
        EntityAlreadyExistsError,
        match="job_resource already exists",
    ):
        job_resource_dynamo.add_job_resource(sample_job_resource)


@pytest.mark.integration
def test_addJobResource_raises_resource_not_found(
    job_resource_dynamo, sample_job_resource, mocker
):
    """Simulate a ResourceNotFoundException when adding a job resource"""
    mock_put = mocker.patch.object(
        job_resource_dynamo._client,
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

    with pytest.raises(
        OperationError,
        match="DynamoDB resource not found during add_job_resource",
    ):
        job_resource_dynamo.add_job_resource(sample_job_resource)
    mock_put.assert_called_once()


# ---
# getJobResource
# ---


@pytest.mark.integration
def test_getJobResource_success(job_resource_dynamo, sample_job, sample_job_resource):
    """Test getting a job resource successfully"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)

    # Get the job resource
    resource = job_resource_dynamo.get_job_resource(
        sample_job_resource.job_id, sample_job_resource.resource_id
    )

    # Verify
    assert resource.job_id == sample_job_resource.job_id
    assert resource.resource_id == sample_job_resource.resource_id
    assert resource.resource_type == sample_job_resource.resource_type
    assert resource.status == sample_job_resource.status
    assert resource.allocated_at == sample_job_resource.allocated_at
    assert resource.released_at == sample_job_resource.released_at
    assert resource.resource_config == sample_job_resource.resource_config


@pytest.mark.integration
def test_getJobResource_raises_value_error_job_id_none(job_resource_dynamo):
    """Test that getJobResource raises ValueError when job_id is None"""
    with pytest.raises(EntityValidationError, match="job_id cannot be None"):
        job_resource_dynamo.get_job_resource(None, "resource-123")


@pytest.mark.integration
def test_getJobResource_raises_value_error_resource_id_none(
    job_resource_dynamo, sample_job
):
    """Test that getJobResource raises ValueError when resource_id is None"""
    with pytest.raises(
        ValueError,
        match="Resource ID is required and must be a non-empty string.",
    ):
        job_resource_dynamo.get_job_resource(sample_job.job_id, None)


@pytest.mark.integration
def test_getJobResource_raises_value_error_not_found(job_resource_dynamo, sample_job):
    """
    Test that getJobResource raises ValueError when the job resource does
    not exist
    """
    with pytest.raises(
        EntityNotFoundError, match="No job resource found with job ID.*"
    ):
        job_resource_dynamo.get_job_resource(sample_job.job_id, "nonexistent-resource")


# ---
# updateJobResourceStatus
# ---


@pytest.mark.integration
def test_updateJobResourceStatus_success(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """Test updating a job resource status successfully"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)

    # Update the job resource status
    released_at = datetime.now().isoformat()
    job_resource_dynamo.update_job_resource_status(
        sample_job_resource.job_id,
        sample_job_resource.resource_id,
        "released",
        released_at,
    )

    # Get the job resource to verify the update
    job_resource = job_resource_dynamo.get_job_resource(
        sample_job_resource.job_id, sample_job_resource.resource_id
    )

    # Verify
    assert job_resource.status == "released"
    assert job_resource.released_at == released_at


@pytest.mark.integration
def test_updateJobResourceStatus_raises_value_error_job_id_none(
    job_resource_dynamo,
):
    """
    Test that updateJobResourceStatus raises ValueError when job_id is None
    """
    with pytest.raises(EntityValidationError, match="job_id cannot be None"):
        job_resource_dynamo.update_job_resource_status(None, "resource-123", "released")


@pytest.mark.integration
def test_updateJobResourceStatus_raises_value_error_resource_id_none(
    job_resource_dynamo, sample_job
):
    """
    Test that updateJobResourceStatus raises ValueError when resource_id is
    None
    """
    with pytest.raises(
        ValueError,
        match="Resource ID is required and must be a non-empty string.",
    ):
        job_resource_dynamo.update_job_resource_status(
            sample_job.job_id, None, "released"
        )


@pytest.mark.integration
def test_updateJobResourceStatus_raises_value_error_status_none(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """
    Test that updateJobResourceStatus raises ValueError when status is None
    """
    with pytest.raises(
        EntityValidationError,
        match="Status is required and must be a non-empty string",
    ):
        job_resource_dynamo.update_job_resource_status(
            sample_job.job_id, sample_job_resource.resource_id, None
        )


@pytest.mark.integration
def test_updateJobResourceStatus_raises_value_error_invalid_status(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """
    Test that updateJobResourceStatus raises ValueError when status is invalid
    """
    with pytest.raises(EntityValidationError, match="Invalid status.*"):
        job_resource_dynamo.update_job_resource_status(
            sample_job.job_id,
            sample_job_resource.resource_id,
            "invalid_status",
        )


@pytest.mark.integration
def test_updateJobResourceStatus_not_found(job_resource_dynamo, sample_job, mocker):
    """Test updating a non-existent job resource"""
    mock_update = mocker.patch.object(
        job_resource_dynamo._client,
        "update_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "The conditional request failed",
                }
            },
            "UpdateItem",
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="No job resource found with job ID.*"
    ):
        job_resource_dynamo.update_job_resource_status(
            sample_job.job_id,
            "nonexistent-resource",
            "released",
            released_at=datetime.now().isoformat(),
        )
    mock_update.assert_called_once()


# ---
# listJobResources
# ---


@pytest.mark.integration
def test_listJobResources_success(
    job_resource_dynamo, sample_job, sample_job_resource, sample_job_resource_2
):
    """Test listing job resources successfully"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resources
    job_resource_dynamo.add_job_resource(sample_job_resource)
    job_resource_dynamo.add_job_resource(sample_job_resource_2)

    # List the job resources
    resources, last_evaluated_key = job_resource_dynamo.list_job_resources(
        sample_job.job_id
    )

    # Verify
    assert len(resources) == 2
    resource_ids = [r.resource_id for r in resources]
    assert sample_job_resource.resource_id in resource_ids
    assert sample_job_resource_2.resource_id in resource_ids


@pytest.mark.integration
def test_listJobResources_with_limit(
    job_resource_dynamo, sample_job, sample_job_resource, sample_job_resource_2
):
    """Test listing job resources with a limit"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resources
    job_resource_dynamo.add_job_resource(sample_job_resource)
    job_resource_dynamo.add_job_resource(sample_job_resource_2)

    # List the job resources with limit=1
    resources, last_evaluated_key = job_resource_dynamo.list_job_resources(
        sample_job.job_id, limit=1
    )

    # Verify
    assert len(resources) == 1
    assert last_evaluated_key is not None  # There should be a last evaluated key


@pytest.mark.integration
def test_listJobResources_with_pagination(
    job_resource_dynamo, sample_job, sample_job_resource, sample_job_resource_2
):
    """Test listing job resources with pagination"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resources
    job_resource_dynamo.add_job_resource(sample_job_resource)
    job_resource_dynamo.add_job_resource(sample_job_resource_2)

    # List the first page
    resources_page1, last_evaluated_key = job_resource_dynamo.list_job_resources(
        sample_job.job_id, limit=1
    )

    # Verify first page
    assert len(resources_page1) == 1
    assert last_evaluated_key is not None

    # List the second page
    resources_page2, last_evaluated_key2 = job_resource_dynamo.list_job_resources(
        sample_job.job_id, limit=1, last_evaluated_key=last_evaluated_key
    )

    # Verify second page
    assert len(resources_page2) == 1
    assert (
        resources_page1[0].resource_id != resources_page2[0].resource_id
    )  # Different resource on each page


@pytest.mark.integration
def test_listJobResources_empty(job_resource_dynamo, sample_job):
    """Test listing job resources when none exist"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # List the job resources
    resources, last_evaluated_key = job_resource_dynamo.list_job_resources(
        sample_job.job_id
    )

    # Verify
    assert len(resources) == 0
    assert last_evaluated_key is None


# ---
# listResourcesByType
# ---


@pytest.mark.integration
def test_listResourcesByType_success(
    job_resource_dynamo, sample_job, sample_job_resource, sample_job_resource_2
):
    """Test listing resources by type successfully"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resources
    job_resource_dynamo.add_job_resource(sample_job_resource)  # gpu
    job_resource_dynamo.add_job_resource(sample_job_resource_2)  # compute_instance

    # List the resources by type
    resources, last_evaluated_key = job_resource_dynamo.list_resources_by_type("gpu")

    # Verify
    assert len(resources) >= 1
    assert all(r.resource_type == "gpu" for r in resources)
    job_resources = [r for r in resources if r.job_id == sample_job.job_id]
    assert len(job_resources) == 1
    assert job_resources[0].resource_id == sample_job_resource.resource_id


@pytest.mark.integration
def test_listResourcesByType_with_limit(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """Test listing resources by type with a limit"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)  # gpu

    # List the resources by type with limit=1
    resources, last_evaluated_key = job_resource_dynamo.list_resources_by_type(
        "gpu", limit=1
    )

    # Verify
    assert len(resources) == 1


@pytest.mark.integration
def test_listResourcesByType_not_found(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """Test listing resources by type when none match"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)  # gpu

    # List resources by a non-existent type
    resources, last_evaluated_key = job_resource_dynamo.list_resources_by_type(
        "nonexistent-type"
    )

    # Verify
    assert len(resources) == 0
    assert last_evaluated_key is None


@pytest.mark.integration
def test_listResourcesByType_raises_value_error(job_resource_dynamo):
    """
    Test that listResourcesByType raises ValueError when resource_type is None
    """
    with pytest.raises(
        ValueError,
        match="Resource type is required and must be a non-empty string.",
    ):
        job_resource_dynamo.list_resources_by_type(None)


# ---
# getResourceById
# ---


@pytest.mark.integration
def test_getResourceById_success(job_resource_dynamo, sample_job, sample_job_resource):
    """Test getting a resource by ID successfully"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)

    # Get the resource by ID
    resources, last_evaluated_key = job_resource_dynamo.get_resource_by_id(
        sample_job_resource.resource_id
    )

    # Verify
    assert len(resources) >= 1
    job_resources = [r for r in resources if r.job_id == sample_job.job_id]
    assert len(job_resources) == 1
    assert job_resources[0].resource_id == sample_job_resource.resource_id


@pytest.mark.integration
def test_getResourceById_with_limit(
    job_resource_dynamo, sample_job, sample_job_resource
):
    """Test getting a resource by ID with a limit"""
    # Add the job first
    job_resource_dynamo.add_job(sample_job)

    # Add the job resource
    job_resource_dynamo.add_job_resource(sample_job_resource)

    # Get the resource by ID
    resources, last_evaluated_key = job_resource_dynamo.get_resource_by_id(
        sample_job_resource.resource_id
    )

    # Verify
    assert len(resources) == 1


@pytest.mark.integration
def test_getResourceById_not_found(job_resource_dynamo):
    """Test getting a resource by ID when it doesn't exist"""
    # Get a resource by a non-existent ID
    resources, last_evaluated_key = job_resource_dynamo.get_resource_by_id(
        "nonexistent-resource-id"
    )

    # Verify
    assert len(resources) == 0


@pytest.mark.integration
def test_getResourceById_raises_value_error(job_resource_dynamo):
    """Test that getResourceById raises ValueError when resource_id is None"""
    with pytest.raises(
        ValueError,
        match="Resource ID is required and must be a non-empty string.",
    ):
        job_resource_dynamo.get_resource_by_id(None)


# ---
# Error Handling
# ---


@pytest.mark.integration
def test_validate_last_evaluated_key_raises_value_error_missing_keys():
    """
    Test that validate_last_evaluated_key raises ValueError when keys are
    missing
    """
    with pytest.raises(
        EntityValidationError, match="LastEvaluatedKey must contain keys"
    ):
        validate_last_evaluated_key({"PK": {"S": "value"}})  # Missing SK


@pytest.mark.integration
def test_validate_last_evaluated_key_raises_value_error_invalid_format():
    """
    Test that validate_last_evaluated_key raises ValueError when the format
    is invalid
    """
    with pytest.raises(
        ValueError,
        match="LastEvaluatedKey.* must be a dict containing a key 'S'",
    ):
        validate_last_evaluated_key({"PK": {"S": "value"}, "SK": "not a dict"})


@pytest.mark.integration
def test_listJobResources_raises_client_error(job_resource_dynamo, sample_job, mocker):
    """
    Test that listJobResources raises an exception when a ClientError occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_resource_dynamo._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "Query",
        ),
    )

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        OperationError,
        match="DynamoDB resource not found during list_job_resources",
    ):
        job_resource_dynamo.list_job_resources(sample_job.job_id)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listResourcesByType_raises_client_error(job_resource_dynamo, mocker):
    """
    Test that listResourcesByType raises an exception when a ClientError occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_resource_dynamo._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "Query",
        ),
    )

    # Call the method and verify it raises the expected exception
    with pytest.raises(
        OperationError,
        match="DynamoDB resource not found during list_resources_by_type",
    ):
        job_resource_dynamo.list_resources_by_type("gpu")
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getResourceById_raises_client_error(job_resource_dynamo, mocker):
    """
    Test that getResourceById raises an exception when a ClientError occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_resource_dynamo._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "Query",
        ),
    )

    # Call the method and verify it raises the expected exception
    from receipt_dynamo.data.shared_exceptions import DynamoDBError

    with pytest.raises(
        OperationError,
        match="DynamoDB resource not found during get_resource_by_id",
    ):
        job_resource_dynamo.get_resource_by_id("resource-123")
    mock_query.assert_called_once()
