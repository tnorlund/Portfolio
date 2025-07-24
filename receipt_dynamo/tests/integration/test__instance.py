import uuid
from datetime import datetime

import botocore
import pytest

from receipt_dynamo import DynamoClient, Instance, InstanceJob
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)


@pytest.fixture
def instance_dynamo(dynamodb_table):
    """Returns a DynamoClient instance for testing."""
    return DynamoClient(table_name=dynamodb_table)


@pytest.fixture
def sample_instance():
    """Returns a sample Instance for testing."""
    return Instance(
        instance_id=str(uuid.uuid4()),
        instance_type="p3.2xlarge",
        gpu_count=4,
        status="running",
        launched_at=datetime.now().isoformat(),
        ip_address="192.168.1.1",
        availability_zone="us-east-1a",
        is_spot=True,
        health_status="healthy",
    )


@pytest.fixture
def sample_instance_job(sample_instance):
    """Returns a sample InstanceJob for testing."""
    return InstanceJob(
        instance_id=sample_instance.instance_id,
        job_id=str(uuid.uuid4()),
        assigned_at=datetime.now().isoformat(),
        status="running",
        resource_utilization={"cpu_utilization": 75, "memory_utilization": 60},
    )


@pytest.mark.integration
def test_addInstance_success(instance_dynamo, sample_instance):
    """Test adding an instance successfully."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Verify it was added
    retrieved_instance = instance_dynamo.get_instance(
        sample_instance.instance_id
    )
    assert retrieved_instance.instance_id == sample_instance.instance_id
    assert retrieved_instance.instance_type == sample_instance.instance_type
    assert retrieved_instance.status == sample_instance.status


@pytest.mark.integration
def test_addInstance_raises_value_error(instance_dynamo):
    """Test that addInstance raises EntityValidationError when instance is None."""
    with pytest.raises(ValueError, match="Instance parameter is required and cannot be None"):
        instance_dynamo.add_instance(None)


@pytest.mark.integration
def test_addInstance_raises_value_error_instance_not_instance(instance_dynamo):
    """
    Test that addInstance raises EntityValidationError when instance is not an Instance.
    """
    with pytest.raises(
        ValueError, match="Instance must be an instance of the Instance class"
    ):
        instance_dynamo.add_instance("not an instance")


@pytest.mark.integration
def test_addInstance_raises_conditional_check_failed(
    instance_dynamo, sample_instance
):
    """Test that addInstance raises ValueError when instance already exists."""
    # Add the instance once
    instance_dynamo.add_instance(sample_instance)

    # Try to add it again, should fail
    with pytest.raises(
        EntityAlreadyExistsError,
        match="Entity already exists",
    ):
        instance_dynamo.add_instance(sample_instance)


@pytest.mark.integration
def test_addInstance_raises_resource_not_found(
    instance_dynamo, sample_instance, mocker
):
    """
    Test that addInstance raises DynamoCriticalErrorException when the table
    doesn't exist.
    """
    # Mock the put_item method to raise a ResourceNotFoundException
    mock_client = mocker.patch.object(instance_dynamo, "_client")
    mock_client.put_item.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "Table not found",
            }
        },
        "PutItem",
    )

    # Verify the correct exception is raised
    with pytest.raises(
        Exception,
        match="Table not found for operation add_instance",
    ):
        instance_dynamo.add_instance(sample_instance)


@pytest.mark.integration
def test_addInstance_raises_provisioned_throughput_exceeded(
    instance_dynamo, sample_instance, mocker
):
    """
    Test that addInstance raises DynamoRetryableException when throughput is
    exceeded.
    """
    # Mock the put_item method to raise a
    # ProvisionedThroughputExceededException
    mock_client = mocker.patch.object(instance_dynamo, "_client")
    mock_client.put_item.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Throughput exceeded",
            }
        },
        "PutItem",
    )

    # Verify the correct exception is raised
    with pytest.raises(
        Exception,
        match="Provisioned throughput exceeded",
    ):
        instance_dynamo.add_instance(sample_instance)


@pytest.mark.integration
def test_addInstance_raises_internal_server_error(
    instance_dynamo, sample_instance, mocker
):
    """
    Test that addInstance raises DynamoRetryableException when there's an
    internal server error.
    """
    # Mock the put_item method to raise an InternalServerError
    mock_client = mocker.patch.object(instance_dynamo, "_client")
    mock_client.put_item.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "InternalServerError",
                "Message": "Internal server error",
            }
        },
        "PutItem",
    )

    # Verify the correct exception is raised
    with pytest.raises(Exception, match="Internal server error"):
        instance_dynamo.add_instance(sample_instance)


@pytest.mark.integration
def test_addInstance_raises_unknown_error(
    instance_dynamo, sample_instance, mocker
):
    """
    Test that addInstance raises DynamoCriticalErrorException for unknown
    errors.
    """
    # Mock the put_item method to raise an unknown error
    mock_client = mocker.patch.object(instance_dynamo, "_client")
    mock_client.put_item.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "UnknownError",
                "Message": "Unknown error",
            }
        },
        "PutItem",
    )

    # Verify the correct exception is raised
    with pytest.raises(
        Exception,
        match="Unknown error in add_instance",
    ):
        instance_dynamo.add_instance(sample_instance)


@pytest.mark.integration
def test_addInstances_success(instance_dynamo, sample_instance):
    """Test adding multiple instances successfully."""
    # Create multiple instances
    instances = [
        sample_instance,
        Instance(
            instance_id=str(uuid.uuid4()),
            instance_type="g4dn.xlarge",
            gpu_count=1,
            status="pending",
            launched_at=datetime.now().isoformat(),
            ip_address="192.168.1.2",
            availability_zone="us-east-1b",
            is_spot=False,
            health_status="unknown",
        ),
    ]

    # Add the instances
    instance_dynamo.add_instances(instances)

    # Verify they were added
    for instance in instances:
        retrieved_instance = instance_dynamo.get_instance(instance.instance_id)
        assert retrieved_instance.instance_id == instance.instance_id
        assert retrieved_instance.instance_type == instance.instance_type
        assert retrieved_instance.status == instance.status


@pytest.mark.integration
def test_addInstances_raises_value_error_instances_none(instance_dynamo):
    """Test that addInstances raises EntityValidationError when instances is None."""
    with pytest.raises(ValueError, match="Instances parameter is required and cannot be None"):
        instance_dynamo.add_instances(None)


@pytest.mark.integration
def test_addInstances_raises_value_error_instances_not_list(instance_dynamo):
    """
    Test that addInstances raises EntityValidationError when instances is not a list.
    """
    with pytest.raises(ValueError, match="Instances must be a list"):
        instance_dynamo.add_instances("not a list")


@pytest.mark.integration
def test_addInstances_raises_value_error_instances_not_list_of_instances(
    instance_dynamo, sample_instance
):
    """
    Test that addInstances raises ValueError when instances contains
    non-Instance items.
    """
    with pytest.raises(
        ValueError,
        match="All instances must be instances of the Instance class",
    ):
        instance_dynamo.add_instances([sample_instance, "not an instance"])


@pytest.mark.integration
def test_getInstance_success(instance_dynamo, sample_instance):
    """Test getting an instance successfully."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Get the instance
    retrieved_instance = instance_dynamo.get_instance(
        sample_instance.instance_id
    )

    # Verify the retrieved instance matches the original
    assert retrieved_instance.instance_id == sample_instance.instance_id
    assert retrieved_instance.instance_type == sample_instance.instance_type
    assert retrieved_instance.gpu_count == sample_instance.gpu_count
    assert retrieved_instance.status == sample_instance.status
    assert retrieved_instance.launched_at == sample_instance.launched_at
    assert retrieved_instance.ip_address == sample_instance.ip_address
    assert (
        retrieved_instance.availability_zone
        == sample_instance.availability_zone
    )
    assert retrieved_instance.is_spot == sample_instance.is_spot
    assert retrieved_instance.health_status == sample_instance.health_status


@pytest.mark.integration
def test_getInstance_raises_value_error_instance_id_none(instance_dynamo):
    """Test that getInstance raises ValueError when instance_id is None."""
    with pytest.raises(
        ValueError, match="instance_id cannot be None or empty"
    ):
        instance_dynamo.get_instance(None)


@pytest.mark.integration
def test_getInstance_raises_value_error_instance_not_found(instance_dynamo):
    """
    Test that getInstance raises ValueError when the instance doesn't exist.
    """
    with pytest.raises(ValueError, match="Instance .* does not exist"):
        instance_dynamo.get_instance(str(uuid.uuid4()))


@pytest.mark.integration
def test_updateInstance_success(instance_dynamo, sample_instance):
    """Test updating an instance successfully."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Modify the instance
    sample_instance.status = "stopped"
    sample_instance.health_status = "unhealthy"

    # Update the instance
    instance_dynamo.update_instance(sample_instance)

    # Verify the update was successful
    retrieved_instance = instance_dynamo.get_instance(
        sample_instance.instance_id
    )
    assert retrieved_instance.status == "stopped"
    assert retrieved_instance.health_status == "unhealthy"


@pytest.mark.integration
def test_updateInstance_raises_value_error_instance_none(instance_dynamo):
    """Test that updateInstance raises EntityValidationError when instance is None."""
    with pytest.raises(ValueError, match="Instance parameter is required and cannot be None"):
        instance_dynamo.update_instance(None)


@pytest.mark.integration
def test_updateInstance_raises_value_error_instance_not_instance(
    instance_dynamo,
):
    """
    Test that updateInstance raises ValueError when instance is not an
    Instance.
    """
    with pytest.raises(
        ValueError, match="Instance must be an instance of the Instance class"
    ):
        instance_dynamo.update_instance("not an instance")


@pytest.mark.integration
def test_updateInstance_raises_conditional_check_failed(
    instance_dynamo, sample_instance
):
    """
    Test that updateInstance raises ValueError when instance doesn't exist.
    """
    # Try to update without adding first
    with pytest.raises(
        EntityNotFoundError,
        match="Entity does not exist",
    ):
        instance_dynamo.update_instance(sample_instance)


@pytest.mark.integration
def test_deleteInstance_success(instance_dynamo, sample_instance):
    """Test deleting an instance successfully."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Delete the instance
    instance_dynamo.delete_instance(sample_instance)

    # Verify it was deleted
    with pytest.raises(
        ValueError,
        match=f"Instance {sample_instance.instance_id} does not exist",
    ):
        instance_dynamo.get_instance(sample_instance.instance_id)


@pytest.mark.integration
def test_deleteInstance_raises_value_error_instance_none(instance_dynamo):
    """Test that deleteInstance raises EntityValidationError when instance is None."""
    with pytest.raises(ValueError, match="Instance parameter is required and cannot be None"):
        instance_dynamo.delete_instance(None)


@pytest.mark.integration
def test_deleteInstance_raises_value_error_instance_not_instance(
    instance_dynamo,
):
    """
    Test that deleteInstance raises ValueError when instance is not an
    Instance.
    """
    with pytest.raises(
        ValueError, match="Instance must be an instance of the Instance class"
    ):
        instance_dynamo.delete_instance("not an instance")


@pytest.mark.integration
def test_deleteInstance_raises_conditional_check_failed(
    instance_dynamo, sample_instance
):
    """
    Test that deleteInstance raises ValueError when instance doesn't exist.
    """
    # Try to delete without adding first
    with pytest.raises(
        EntityNotFoundError,
        match="Entity does not exist",
    ):
        instance_dynamo.delete_instance(sample_instance)


@pytest.mark.integration
def test_addInstanceJob_success(
    instance_dynamo, sample_instance, sample_instance_job
):
    """Test adding an instance-job association successfully."""
    # Add the instance first
    instance_dynamo.add_instance(sample_instance)

    # Add the instance-job association
    instance_dynamo.add_instance_job(sample_instance_job)

    # Verify it was added
    retrieved_instance_job = instance_dynamo.get_instance_job(
        sample_instance_job.instance_id, sample_instance_job.job_id
    )
    assert (
        retrieved_instance_job.instance_id == sample_instance_job.instance_id
    )
    assert retrieved_instance_job.job_id == sample_instance_job.job_id
    assert retrieved_instance_job.status == sample_instance_job.status


@pytest.mark.integration
def test_addInstanceJob_raises_value_error_instance_job_none(instance_dynamo):
    """Test that addInstanceJob raises EntityValidationError when instance_job is None."""
    with pytest.raises(ValueError, match="Instance_job parameter is required and cannot be None"):
        instance_dynamo.add_instance_job(None)


@pytest.mark.integration
def test_addInstanceJob_raises_value_error_instance_job_not_instance_job(
    instance_dynamo,
):
    """
    Test that addInstanceJob raises ValueError when instance_job is not an
    InstanceJob.
    """
    with pytest.raises(
        ValueError, match="Instance_job must be an instance of the InstanceJob class"
    ):
        instance_dynamo.add_instance_job("not an instance job")


@pytest.mark.integration
def test_getInstanceWithJobs_success(
    instance_dynamo, sample_instance, sample_instance_job
):
    """Test getting an instance with its jobs successfully."""
    # Add the instance and instance-job
    instance_dynamo.add_instance(sample_instance)
    instance_dynamo.add_instance_job(sample_instance_job)

    # Get the instance with jobs
    instance, instance_jobs = instance_dynamo.get_instance_with_jobs(
        sample_instance.instance_id
    )

    # Verify the retrieved data
    assert instance.instance_id == sample_instance.instance_id
    assert len(instance_jobs) == 1
    assert instance_jobs[0].instance_id == sample_instance_job.instance_id
    assert instance_jobs[0].job_id == sample_instance_job.job_id


@pytest.mark.integration
def test_listInstances_success(instance_dynamo, sample_instance):
    """Test listing instances successfully."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Add another instance
    second_instance = Instance(
        instance_id=str(uuid.uuid4()),
        instance_type="g4dn.xlarge",
        gpu_count=1,
        status="pending",
        launched_at=datetime.now().isoformat(),
        ip_address="192.168.1.2",
        availability_zone="us-east-1b",
        is_spot=False,
        health_status="unknown",
    )
    instance_dynamo.add_instance(second_instance)

    # List instances
    instances, last_evaluated_key = instance_dynamo.list_instances()

    # Verify the result
    assert len(instances) >= 2  # Could be more if other tests added instances
    instance_ids = [instance.instance_id for instance in instances]
    assert sample_instance.instance_id in instance_ids
    assert second_instance.instance_id in instance_ids


@pytest.mark.integration
def test_listInstances_with_limit(instance_dynamo, sample_instance):
    """Test listing instances with a limit."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Add another instance
    second_instance = Instance(
        instance_id=str(uuid.uuid4()),
        instance_type="g4dn.xlarge",
        gpu_count=1,
        status="pending",
        launched_at=datetime.now().isoformat(),
        ip_address="192.168.1.2",
        availability_zone="us-east-1b",
        is_spot=False,
        health_status="unknown",
    )
    instance_dynamo.add_instance(second_instance)

    # List instances with a limit of 1
    instances, last_evaluated_key = instance_dynamo.list_instances(limit=1)

    # Verify the result
    assert len(instances) == 1
    # Since the order is not guaranteed, we can't verify which instance was
    # returned

    # List the next page
    if last_evaluated_key:
        more_instances, _ = instance_dynamo.list_instances(
            limit=1, last_evaluated_key=last_evaluated_key
        )
        assert len(more_instances) == 1


@pytest.mark.integration
def test_listInstancesByStatus_success(instance_dynamo, sample_instance):
    """Test listing instances by status successfully."""
    # Add the instance
    instance_dynamo.add_instance(sample_instance)

    # Add another instance with a different status
    second_instance = Instance(
        instance_id=str(uuid.uuid4()),
        instance_type="g4dn.xlarge",
        gpu_count=1,
        status="pending",
        launched_at=datetime.now().isoformat(),
        ip_address="192.168.1.2",
        availability_zone="us-east-1b",
        is_spot=False,
        health_status="unknown",
    )
    instance_dynamo.add_instance(second_instance)

    # List instances by status 'running'
    running_instances, _ = instance_dynamo.list_instances_by_status("running")

    # Verify the result
    assert (
        len(running_instances) >= 1
    )  # Could be more if other tests added running instances
    assert sample_instance.instance_id in [
        instance.instance_id for instance in running_instances
    ]

    # List instances by status 'pending'
    pending_instances, _ = instance_dynamo.list_instances_by_status("pending")

    # Verify the result
    assert (
        len(pending_instances) >= 1
    )  # Could be more if other tests added pending instances
    assert second_instance.instance_id in [
        instance.instance_id for instance in pending_instances
    ]


@pytest.mark.integration
def test_listInstanceJobs_success(
    instance_dynamo, sample_instance, sample_instance_job
):
    """Test listing jobs associated with an instance successfully."""
    # Add the instance and instance-job
    instance_dynamo.add_instance(sample_instance)
    instance_dynamo.add_instance_job(sample_instance_job)

    # Add another instance-job for the same instance
    second_instance_job = InstanceJob(
        instance_id=sample_instance.instance_id,
        job_id=str(uuid.uuid4()),
        assigned_at=datetime.now().isoformat(),
        status="completed",
        resource_utilization={"cpu_utilization": 50},
    )
    instance_dynamo.add_instance_job(second_instance_job)

    # List instance jobs
    instance_jobs, _ = instance_dynamo.list_instance_jobs(
        sample_instance.instance_id
    )

    # Verify the result
    assert len(instance_jobs) == 2
    instance_job_ids = [instance_job.job_id for instance_job in instance_jobs]
    assert sample_instance_job.job_id in instance_job_ids
    assert second_instance_job.job_id in instance_job_ids


@pytest.mark.integration
def test_listInstancesForJob_success(
    instance_dynamo, sample_instance, sample_instance_job
):
    """Test listing instances associated with a job successfully."""
    # Add the instance and instance-job
    instance_dynamo.add_instance(sample_instance)
    instance_dynamo.add_instance_job(sample_instance_job)

    # Add another instance with the same job
    second_instance = Instance(
        instance_id=str(uuid.uuid4()),
        instance_type="g4dn.xlarge",
        gpu_count=1,
        status="pending",
        launched_at=datetime.now().isoformat(),
        ip_address="192.168.1.2",
        availability_zone="us-east-1b",
        is_spot=False,
        health_status="unknown",
    )
    instance_dynamo.add_instance(second_instance)

    second_instance_job = InstanceJob(
        instance_id=second_instance.instance_id,
        job_id=sample_instance_job.job_id,
        assigned_at=datetime.now().isoformat(),
        status="assigned",
        resource_utilization={},
    )
    instance_dynamo.add_instance_job(second_instance_job)

    # List instances for job
    instance_jobs, _ = instance_dynamo.list_instances_for_job(
        sample_instance_job.job_id
    )

    # Verify the result
    assert len(instance_jobs) == 2
    instance_ids = [instance_job.instance_id for instance_job in instance_jobs]
    assert sample_instance.instance_id in instance_ids
    assert second_instance.instance_id in instance_ids
