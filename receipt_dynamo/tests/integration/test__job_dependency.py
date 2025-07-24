import uuid
from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data.shared_exceptions import DynamoDBError, EntityAlreadyExistsError, EntityNotFoundError
from receipt_dynamo.entities.job_dependency import JobDependency


@pytest.fixture
def job_dependency_dynamo(dynamodb_table):
    """
    Creates a DynamoClient instance configured for testing job dependencies.
    """
    from receipt_dynamo import DynamoClient

    return DynamoClient(table_name=dynamodb_table, region="us-east-1")


@pytest.fixture
def sample_job_dependency():
    """Provides a sample JobDependency for testing."""
    dependent_job_id = str(uuid.uuid4())
    dependency_job_id = str(uuid.uuid4())
    return JobDependency(
        dependent_job_id=dependent_job_id,
        dependency_job_id=dependency_job_id,
        type="SUCCESS",
        created_at=datetime.now().isoformat(),
        condition="Test dependency condition",
    )


@pytest.fixture
def multiple_job_dependencies():
    """Provides multiple sample JobDependencies for the same dependent job."""
    dependent_job_id = str(uuid.uuid4())
    base_time = datetime.now()
    return [
        JobDependency(
            dependent_job_id=dependent_job_id,
            dependency_job_id=str(uuid.uuid4()),
            type=dep_type,
            created_at=(base_time - timedelta(minutes=i)).isoformat(),
            condition=f"Test condition {i}",
        )
        for i, dep_type in enumerate(
            ["SUCCESS", "COMPLETION", "ARTIFACT", "FAILURE"]
        )
    ]


@pytest.mark.integration
def test_addJobDependency_success(
    job_dependency_dynamo, sample_job_dependency
):
    """Test adding a job dependency successfully."""
    # Add the job dependency
    job_dependency_dynamo.add_job_dependency(sample_job_dependency)

    # Verify it was added by retrieving it
    retrieved_dependency = job_dependency_dynamo.get_job_dependency(
        dependent_job_id=sample_job_dependency.dependent_job_id,
        dependency_job_id=sample_job_dependency.dependency_job_id,
    )
    assert retrieved_dependency == sample_job_dependency


@pytest.mark.integration
def test_addJobDependency_raises_value_error(job_dependency_dynamo):
    """
    Test that addJobDependency raises ValueError when job_dependency is None.
    """
    with pytest.raises(ValueError, match="job_dependency cannot be None"):
        job_dependency_dynamo.add_job_dependency(None)


@pytest.mark.integration
def test_addJobDependency_raises_value_error_job_not_instance(
    job_dependency_dynamo,
):
    """
    Test that addJobDependency raises ValueError when job_dependency is not a
    JobDependency instance.
    """
    with pytest.raises(
        ValueError, match="job_dependency must be an instance of the JobDependency class"
    ):
        job_dependency_dynamo.add_job_dependency("not a job dependency")


@pytest.mark.integration
def test_addJobDependency_raises_conditional_check_failed(
    job_dependency_dynamo, sample_job_dependency
):
    """
    Test that addJobDependency raises ValueError when trying to add a
    duplicate job dependency.
    """
    # Add the job dependency
    job_dependency_dynamo.add_job_dependency(sample_job_dependency)

    # Try to add it again, which should raise an error
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        job_dependency_dynamo.add_job_dependency(sample_job_dependency)


@pytest.mark.integration
def test_addJobDependency_raises_resource_not_found(
    job_dependency_dynamo, sample_job_dependency, mocker
):
    """
    Test that addJobDependency handles ResourceNotFoundException properly.
    """
    # Mock the put_item method to raise ResourceNotFoundException
    mock_client = mocker.patch.object(job_dependency_dynamo, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "The table does not exist",
            }
        },
        "PutItem",
    )

    # Attempt to add the job dependency
    with pytest.raises(
        DynamoDBError, match="Table not found for operation add_job_dependency"
    ):
        job_dependency_dynamo.add_job_dependency(sample_job_dependency)


@pytest.mark.integration
def test_getJobDependency_success(
    job_dependency_dynamo, sample_job_dependency
):
    """Test retrieving a job dependency successfully."""
    # Add the job dependency
    job_dependency_dynamo.add_job_dependency(sample_job_dependency)

    # Retrieve the job dependency
    retrieved_dependency = job_dependency_dynamo.get_job_dependency(
        dependent_job_id=sample_job_dependency.dependent_job_id,
        dependency_job_id=sample_job_dependency.dependency_job_id,
    )
    assert retrieved_dependency == sample_job_dependency


@pytest.mark.integration
def test_getJobDependency_raises_value_error_dependent_job_id_none(
    job_dependency_dynamo,
):
    """
    Test that getJobDependency raises ValueError when dependent_job_id is None.
    """
    with pytest.raises(ValueError, match="dependent_job_id cannot be None"):
        job_dependency_dynamo.get_job_dependency(
            dependent_job_id=None, dependency_job_id="some-job-id"
        )


@pytest.mark.integration
def test_getJobDependency_raises_value_error_dependency_job_id_none(
    job_dependency_dynamo,
):
    """
    Test that getJobDependency raises ValueError when dependency_job_id is
    None.
    """
    with pytest.raises(ValueError, match="dependency_job_id cannot be None"):
        job_dependency_dynamo.get_job_dependency(
            dependent_job_id="some-job-id", dependency_job_id=None
        )


@pytest.mark.integration
def test_getJobDependency_raises_value_error_dependency_not_found(
    job_dependency_dynamo,
):
    """
    Test that getJobDependency raises EntityNotFoundError when the job dependency is
    not found.
    """
    with pytest.raises(EntityNotFoundError, match="not found"):
        job_dependency_dynamo.get_job_dependency(
            dependent_job_id="non-existent-job",
            dependency_job_id="another-non-existent-job",
        )


@pytest.mark.integration
def test_listDependencies_success(
    job_dependency_dynamo, multiple_job_dependencies
):
    """Test listing job dependencies successfully."""
    # Add the job dependencies
    for dependency in multiple_job_dependencies:
        job_dependency_dynamo.add_job_dependency(dependency)

    # List the job dependencies
    dependent_job_id = multiple_job_dependencies[
        0
    ].dependent_job_id  # All have the same dependent_job_id
    dependencies, last_key = job_dependency_dynamo.list_dependencies(
        dependent_job_id=dependent_job_id
    )

    # Check that all dependencies are returned
    assert len(dependencies) == len(multiple_job_dependencies)

    # Check the content matches
    for dependency in multiple_job_dependencies:
        # Find the corresponding dependency in the returned list
        matching_dependency = next(
            (
                d
                for d in dependencies
                if d.dependency_job_id == dependency.dependency_job_id
            ),
            None,
        )
        assert (
            matching_dependency is not None
        ), f"Dependency with ID {dependency.dependency_job_id} not found"
        assert matching_dependency == dependency


@pytest.mark.integration
def test_listDependencies_with_limit(
    job_dependency_dynamo, multiple_job_dependencies
):
    """Test listing job dependencies with a limit."""
    # Add the job dependencies
    for dependency in multiple_job_dependencies:
        job_dependency_dynamo.add_job_dependency(dependency)

    # List the job dependencies with a limit
    dependent_job_id = multiple_job_dependencies[
        0
    ].dependent_job_id  # All have the same dependent_job_id
    limit = 2
    dependencies, last_key = job_dependency_dynamo.list_dependencies(
        dependent_job_id=dependent_job_id, limit=limit
    )

    # Check that only the specified number of dependencies are returned
    assert len(dependencies) <= limit

    # Check that the last evaluated key is returned if there are more
    # dependencies
    if len(multiple_job_dependencies) > limit:
        assert last_key is not None

    # Use the last key to get the next batch
    if last_key is not None:
        next_dependencies, next_last_key = (
            job_dependency_dynamo.list_dependencies(
                dependent_job_id=dependent_job_id,
                limit=limit,
                last_evaluated_key=last_key,
            )
        )

        # Check that we got more dependencies
        assert len(next_dependencies) > 0

        # Ensure we got different dependencies
        assert all(
            dep1.dependency_job_id != dep2.dependency_job_id
            for dep1 in dependencies
            for dep2 in next_dependencies
        ), "Should get different dependencies"


@pytest.mark.integration
def test_listDependencies_raises_value_error_dependent_job_id_none(
    job_dependency_dynamo,
):
    """
    Test that listDependencies raises ValueError when dependent_job_id is None.
    """
    with pytest.raises(ValueError, match="dependent_job_id cannot be None"):
        job_dependency_dynamo.list_dependencies(dependent_job_id=None)


@pytest.mark.integration
def test_listDependencies_empty_result(job_dependency_dynamo):
    """
    Test listing job dependencies when there are none returns an empty list.
    """
    dependent_job_id = str(uuid.uuid4())
    dependencies, last_key = job_dependency_dynamo.list_dependencies(
        dependent_job_id=dependent_job_id
    )
    assert dependencies == []
    assert last_key is None


@pytest.mark.integration
def test_listDependents_success(job_dependency_dynamo):
    """Test listing job dependents successfully."""
    # Create a dependency chain with multiple dependent jobs
    dependency_job_id = str(uuid.uuid4())
    dependent_jobs = []

    # Create 3 jobs that depend on the same job
    for i in range(3):
        dependent_job_id = str(uuid.uuid4())
        dependency = JobDependency(
            dependent_job_id=dependent_job_id,
            dependency_job_id=dependency_job_id,
            type="SUCCESS",
            created_at=datetime.now().isoformat(),
            condition=f"Test condition {i}",
        )
        job_dependency_dynamo.add_job_dependency(dependency)
        dependent_jobs.append(dependency)

    # List the dependents
    dependents, last_key = job_dependency_dynamo.list_dependents(
        dependency_job_id=dependency_job_id
    )

    # Check that all dependents are returned
    assert len(dependents) == len(dependent_jobs)

    # Check the content matches
    for dependent in dependent_jobs:
        # Find the corresponding dependent in the returned list
        matching_dependent = next(
            (
                d
                for d in dependents
                if d.dependent_job_id == dependent.dependent_job_id
            ),
            None,
        )
        assert (
            matching_dependent is not None
        ), f"Dependent with ID {dependent.dependent_job_id} not found"
        assert matching_dependent == dependent


@pytest.mark.integration
def test_listDependents_with_limit(job_dependency_dynamo):
    """Test listing job dependents with a limit."""
    # Create a dependency chain with multiple dependent jobs
    dependency_job_id = str(uuid.uuid4())
    dependent_jobs = []

    # Create 4 jobs that depend on the same job
    for i in range(4):
        dependent_job_id = str(uuid.uuid4())
        dependency = JobDependency(
            dependent_job_id=dependent_job_id,
            dependency_job_id=dependency_job_id,
            type="SUCCESS",
            created_at=datetime.now().isoformat(),
            condition=f"Test condition {i}",
        )
        job_dependency_dynamo.add_job_dependency(dependency)
        dependent_jobs.append(dependency)

    # List the dependents with a limit
    limit = 2
    dependents, last_key = job_dependency_dynamo.list_dependents(
        dependency_job_id=dependency_job_id, limit=limit
    )

    # Check that only the specified number of dependents are returned
    assert len(dependents) <= limit

    # Check that the last evaluated key is returned if there are more
    # dependents
    if len(dependent_jobs) > limit:
        assert last_key is not None

    # Use the last key to get the next batch
    if last_key is not None:
        next_dependents, next_last_key = job_dependency_dynamo.list_dependents(
            dependency_job_id=dependency_job_id,
            limit=limit,
            last_evaluated_key=last_key,
        )

        # Check that we got more dependents
        assert len(next_dependents) > 0

        # Ensure we got different dependents
        assert all(
            dep1.dependent_job_id != dep2.dependent_job_id
            for dep1 in dependents
            for dep2 in next_dependents
        ), "Should get different dependents"


@pytest.mark.integration
def test_listDependents_raises_value_error_dependency_job_id_none(
    job_dependency_dynamo,
):
    """
    Test that listDependents raises ValueError when dependency_job_id is None.
    """
    with pytest.raises(ValueError, match="dependency_job_id cannot be None"):
        job_dependency_dynamo.list_dependents(dependency_job_id=None)


@pytest.mark.integration
def test_listDependents_empty_result(job_dependency_dynamo):
    """
    Test listing job dependents when there are none returns an empty list.
    """
    dependency_job_id = str(uuid.uuid4())
    dependents, last_key = job_dependency_dynamo.list_dependents(
        dependency_job_id=dependency_job_id
    )
    assert dependents == []
    assert last_key is None


@pytest.mark.integration
def test_deleteJobDependency_success(
    job_dependency_dynamo, sample_job_dependency
):
    """Test deleting a job dependency successfully."""
    # Add the job dependency
    job_dependency_dynamo.add_job_dependency(sample_job_dependency)

    # Verify it was added
    retrieved_dependency = job_dependency_dynamo.get_job_dependency(
        dependent_job_id=sample_job_dependency.dependent_job_id,
        dependency_job_id=sample_job_dependency.dependency_job_id,
    )
    assert retrieved_dependency == sample_job_dependency

    # Delete the job dependency
    job_dependency_dynamo.delete_job_dependency(sample_job_dependency)

    # Verify it was deleted
    with pytest.raises(EntityNotFoundError, match="not found"):
        job_dependency_dynamo.get_job_dependency(
            dependent_job_id=sample_job_dependency.dependent_job_id,
            dependency_job_id=sample_job_dependency.dependency_job_id,
        )


@pytest.mark.integration
def test_deleteJobDependency_raises_value_error_dependency_none(
    job_dependency_dynamo,
):
    """
    Test that deleteJobDependency raises ValueError when job_dependency is
    None.
    """
    with pytest.raises(ValueError, match="job_dependency cannot be None"):
        job_dependency_dynamo.delete_job_dependency(None)


@pytest.mark.integration
def test_deleteJobDependency_raises_value_error_dependency_not_instance(
    job_dependency_dynamo,
):
    """
    Test that deleteJobDependency raises ValueError when job_dependency is not
    a JobDependency instance.
    """
    with pytest.raises(
        ValueError, match="job_dependency must be an instance of the JobDependency class"
    ):
        job_dependency_dynamo.delete_job_dependency("not a job dependency")


@pytest.mark.integration
def test_deleteJobDependency_raises_conditional_check_failed(
    job_dependency_dynamo, sample_job_dependency
):
    """
    Test that deleteJobDependency raises EntityNotFoundError when the job dependency
    does not exist.
    """
    # Try to delete a job dependency that doesn't exist
    with pytest.raises(EntityNotFoundError, match="Entity does not exist: JobDependency"):
        job_dependency_dynamo.delete_job_dependency(sample_job_dependency)


@pytest.mark.integration
def test_deleteAllDependencies_success(
    job_dependency_dynamo, multiple_job_dependencies
):
    """Test deleting all dependencies for a job successfully."""
    # Add the job dependencies
    for dependency in multiple_job_dependencies:
        job_dependency_dynamo.add_job_dependency(dependency)

    # Verify they were added
    dependent_job_id = multiple_job_dependencies[0].dependent_job_id
    dependencies, _ = job_dependency_dynamo.list_dependencies(
        dependent_job_id=dependent_job_id
    )
    assert len(dependencies) == len(multiple_job_dependencies)

    # Delete all dependencies
    job_dependency_dynamo.delete_all_dependencies(
        dependent_job_id=dependent_job_id
    )

    # Verify they were deleted
    dependencies, _ = job_dependency_dynamo.list_dependencies(
        dependent_job_id=dependent_job_id
    )
    assert len(dependencies) == 0


@pytest.mark.integration
def test_deleteAllDependencies_raises_value_error_dependent_job_id_none(
    job_dependency_dynamo,
):
    """
    Test that deleteAllDependencies raises ValueError when dependent_job_id is
    None.
    """
    with pytest.raises(ValueError, match="dependent_job_id cannot be None"):
        job_dependency_dynamo.delete_all_dependencies(dependent_job_id=None)


@pytest.mark.integration
def test_deleteAllDependencies_no_dependencies(job_dependency_dynamo):
    """Test deleteAllDependencies when there are no dependencies to delete."""
    dependent_job_id = str(uuid.uuid4())
    # This should not raise an error
    job_dependency_dynamo.delete_all_dependencies(
        dependent_job_id=dependent_job_id
    )
