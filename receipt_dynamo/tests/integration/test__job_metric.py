import uuid
from datetime import datetime, timedelta
from typing import Type

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo.data._job_metric import validate_last_evaluated_key
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
from receipt_dynamo.entities.job_metric import JobMetric

pytestmark = pytest.mark.integration


@pytest.fixture
def job_metric_dynamo(dynamodb_table):
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
def sample_job_metric(sample_job):
    """Provides a sample JobMetric for testing."""
    return JobMetric(
        sample_job.job_id,
        "loss",
        datetime.now().isoformat(),
        0.15,
        unit="dimensionless",
        step=100,
        epoch=2,
    )


@pytest.fixture
def sample_job_metric_2(sample_job):
    """Provides a second sample JobMetric for testing."""
    return JobMetric(
        sample_job.job_id,
        "accuracy",
        datetime.now().isoformat(),
        0.85,
        unit="percent",
        step=100,
        epoch=2,
    )


@pytest.fixture
def sample_job_metric_later(sample_job, sample_job_metric):
    """Provides a sample JobMetric with a later timestamp."""
    return JobMetric(
        sample_job.job_id,
        sample_job_metric.metric_name,
        (
            datetime.fromisoformat(sample_job_metric.timestamp) + timedelta(hours=1)
        ).isoformat(),
        0.1,
        unit="dimensionless",
        step=200,
        epoch=3,
    )


# ---
# addJobMetric
# ---


@pytest.mark.integration
def test_addJobMetric_success(job_metric_dynamo, sample_job, sample_job_metric):
    """Test adding a job metric successfully"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metric
    job_metric_dynamo.add_job_metric(sample_job_metric)

    # Verify the job metric was added by retrieving it
    metrics, _ = job_metric_dynamo.get_metrics_by_name(sample_job_metric.metric_name)

    # Find the metric matching our job_id
    job_metrics = [m for m in metrics if m.job_id == sample_job.job_id]

    # Verify
    assert len(job_metrics) >= 1
    metric = job_metrics[0]
    assert metric.job_id == sample_job.job_id
    assert metric.metric_name == sample_job_metric.metric_name
    assert metric.value == sample_job_metric.value
    assert metric.unit == sample_job_metric.unit
    assert metric.step == sample_job_metric.step
    assert metric.epoch == sample_job_metric.epoch


@pytest.mark.integration
def test_addJobMetric_raises_value_error(job_metric_dynamo):
    """Test that addJobMetric raises ValueError when job_metric is None"""
    with pytest.raises(OperationError, match="job_metric cannot be None"):
        job_metric_dynamo.add_job_metric(None)


@pytest.mark.integration
def test_addJobMetric_raises_value_error_not_instance(job_metric_dynamo):
    """
    Test that addJobMetric raises ValueError when job_metric is not an
    instance of JobMetric
    """
    with pytest.raises(
        OperationError,
        match="job_metric must be an instance of JobMetric",
    ):
        job_metric_dynamo.add_job_metric("not a job metric")


@pytest.mark.integration
def test_addJobMetric_raises_conditional_check_failed(
    job_metric_dynamo, sample_job, sample_job_metric
):
    """
    Test that addJobMetric raises ValueError when the job metric already exists
    """
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metric first
    job_metric_dynamo.add_job_metric(sample_job_metric)

    # Try to add it again
    with pytest.raises(
        EntityAlreadyExistsError,
        match="already exists",
    ):
        job_metric_dynamo.add_job_metric(sample_job_metric)


@pytest.mark.integration
def test_addJobMetric_raises_resource_not_found(
    job_metric_dynamo, sample_job_metric, mocker
):
    """Simulate a ResourceNotFoundException when adding a job metric"""
    mock_put = mocker.patch.object(
        job_metric_dynamo._client,
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

    with pytest.raises(Exception, match="Table not found"):
        job_metric_dynamo.add_job_metric(sample_job_metric)
    mock_put.assert_called_once()


# ---
# getJobMetric
# ---


@pytest.mark.integration
def test_getJobMetric_success(job_metric_dynamo, sample_job, sample_job_metric):
    """Test getting a job metric successfully"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metric
    job_metric_dynamo.add_job_metric(sample_job_metric)

    # Get the job metric
    metric = job_metric_dynamo.get_job_metric(
        sample_job_metric.job_id,
        sample_job_metric.metric_name,
        sample_job_metric.timestamp,
    )

    # Verify
    assert metric.job_id == sample_job_metric.job_id
    assert metric.metric_name == sample_job_metric.metric_name
    assert metric.value == sample_job_metric.value
    assert metric.timestamp == sample_job_metric.timestamp
    assert metric.unit == sample_job_metric.unit
    assert metric.step == sample_job_metric.step
    assert metric.epoch == sample_job_metric.epoch


@pytest.mark.integration
def test_getJobMetric_raises_value_error_job_id_none(job_metric_dynamo):
    """Test that getJobMetric raises ValueError when job_id is None"""
    with pytest.raises(EntityValidationError, match="job_id cannot be None"):
        job_metric_dynamo.get_job_metric(None, "loss", "2021-01-01T12:30:45")


@pytest.mark.integration
def test_getJobMetric_raises_value_error_metric_name_none(
    job_metric_dynamo, sample_job
):
    """Test that getJobMetric raises ValueError when metric_name is None"""
    with pytest.raises(
        ValueError,
        match="Metric name is required and must be a non-empty string.",
    ):
        job_metric_dynamo.get_job_metric(sample_job.job_id, None, "2021-01-01T12:30:45")


@pytest.mark.integration
def test_getJobMetric_raises_value_error_timestamp_none(job_metric_dynamo, sample_job):
    """Test that getJobMetric raises ValueError when timestamp is None"""
    with pytest.raises(
        ValueError,
        match="Timestamp is required and must be a non-empty string.",
    ):
        job_metric_dynamo.get_job_metric(sample_job.job_id, "loss", None)


@pytest.mark.integration
def test_getJobMetric_raises_value_error_not_found(job_metric_dynamo, sample_job):
    """
    Test that getJobMetric raises ValueError when the job metric does not exist
    """
    with pytest.raises(EntityNotFoundError, match="No job metric found with job ID.*"):
        job_metric_dynamo.get_job_metric(
            sample_job.job_id, "nonexistent", "2021-01-01T12:30:45"
        )


# ---
# listJobMetrics
# ---


@pytest.mark.integration
def test_listJobMetrics_success(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_2
):
    """Test listing job metrics successfully"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)
    job_metric_dynamo.add_job_metric(sample_job_metric_2)

    # List the job metrics
    metrics, last_evaluated_key = job_metric_dynamo.list_job_metrics(sample_job.job_id)

    # Verify
    assert len(metrics) == 2
    metric_names = [m.metric_name for m in metrics]
    assert sample_job_metric.metric_name in metric_names
    assert sample_job_metric_2.metric_name in metric_names


@pytest.mark.integration
def test_listJobMetrics_with_metric_name(
    job_metric_dynamo,
    sample_job,
    sample_job_metric,
    sample_job_metric_2,
    sample_job_metric_later,
):
    """Test listing job metrics filtered by metric name"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)  # loss
    job_metric_dynamo.add_job_metric(sample_job_metric_2)  # accuracy
    job_metric_dynamo.add_job_metric(sample_job_metric_later)  # loss (later)

    # List the job metrics filtered by name
    metrics, last_evaluated_key = job_metric_dynamo.list_job_metrics(
        sample_job.job_id, metric_name="loss"
    )

    # Verify
    assert len(metrics) == 2
    assert all(m.metric_name == "loss" for m in metrics)

    # Get values to check chronological order
    values = [m.value for m in metrics]
    assert values[0] == sample_job_metric.value
    assert values[1] == sample_job_metric_later.value


@pytest.mark.integration
def test_listJobMetrics_with_limit(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_2
):
    """Test listing job metrics with a limit"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)
    job_metric_dynamo.add_job_metric(sample_job_metric_2)

    # List the job metrics with limit=1
    metrics, last_evaluated_key = job_metric_dynamo.list_job_metrics(
        sample_job.job_id, limit=1
    )

    # Verify
    assert len(metrics) == 1
    assert last_evaluated_key is not None  # There should be a last evaluated key


@pytest.mark.integration
def test_listJobMetrics_with_pagination(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_2
):
    """Test listing job metrics with pagination"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)
    job_metric_dynamo.add_job_metric(sample_job_metric_2)

    # List the first page
    metrics_page1, last_evaluated_key = job_metric_dynamo.list_job_metrics(
        sample_job.job_id, limit=1
    )

    # Verify first page
    assert len(metrics_page1) == 1
    assert last_evaluated_key is not None

    # List the second page
    metrics_page2, last_evaluated_key2 = job_metric_dynamo.list_job_metrics(
        sample_job.job_id, limit=1, last_evaluated_key=last_evaluated_key
    )

    # Verify second page
    assert len(metrics_page2) == 1
    assert (
        metrics_page1[0].metric_name != metrics_page2[0].metric_name
    )  # Different metric on each page


@pytest.mark.integration
def test_listJobMetrics_empty(job_metric_dynamo, sample_job):
    """Test listing job metrics when none exist"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # List the job metrics
    metrics, last_evaluated_key = job_metric_dynamo.list_job_metrics(sample_job.job_id)

    # Verify
    assert len(metrics) == 0
    assert last_evaluated_key is None


# ---
# getMetricsByName
# ---


@pytest.mark.integration
def test_getMetricsByName_success(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_later
):
    """Test getting metrics by name successfully"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)  # loss
    job_metric_dynamo.add_job_metric(sample_job_metric_later)  # loss (later)

    # Get metrics by name
    metrics, last_evaluated_key = job_metric_dynamo.get_metrics_by_name("loss")

    # Verify
    assert len(metrics) >= 2
    assert all(m.metric_name == "loss" for m in metrics)
    # Find metrics for our job
    job_metrics = [m for m in metrics if m.job_id == sample_job.job_id]
    assert len(job_metrics) == 2
    # Check we have both metrics
    values = sorted([m.value for m in job_metrics])
    assert (
        values[0] == sample_job_metric.value
        or values[0] == sample_job_metric_later.value
    )
    assert (
        values[1] == sample_job_metric.value
        or values[1] == sample_job_metric_later.value
    )


@pytest.mark.integration
def test_getMetricsByName_with_limit(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_later
):
    """Test getting metrics by name with a limit"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)
    job_metric_dynamo.add_job_metric(sample_job_metric_later)

    # Get metrics by name with limit=1
    metrics, last_evaluated_key = job_metric_dynamo.get_metrics_by_name("loss", limit=1)

    # Verify
    assert len(metrics) == 1
    assert metrics[0].metric_name == "loss"


@pytest.mark.integration
def test_getMetricsByName_not_found(job_metric_dynamo, sample_job, sample_job_metric):
    """Test getting metrics by name when none match"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metric
    job_metric_dynamo.add_job_metric(sample_job_metric)  # loss

    # Get metrics by a non-existent name
    metrics, last_evaluated_key = job_metric_dynamo.get_metrics_by_name("nonexistent")

    # Verify
    assert len(metrics) == 0
    assert last_evaluated_key is None


@pytest.mark.integration
def test_getMetricsByName_raises_value_error(job_metric_dynamo):
    """Test that getMetricsByName raises ValueError when metric_name is None"""
    with pytest.raises(
        ValueError,
        match="Metric name is required and must be a non-empty string.",
    ):
        job_metric_dynamo.get_metrics_by_name(None)


# ---
# getMetricsByNameAcrossJobs
# ---


@pytest.mark.integration
def test_getMetricsByNameAcrossJobs_success(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_later
):
    """Test getting metrics by name across jobs successfully"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Create a second job with different ID but same metric name
    from uuid import uuid4

    from receipt_dynamo import Job

    second_job_id = str(uuid4())
    second_job = Job(
        second_job_id,
        "Second Test Job",
        "Test description for second job",
        "2021-01-02T00:00:00",
        "test_user",
        "pending",
        "medium",
        {},
    )
    job_metric_dynamo.add_job(second_job)

    # Create a metric for the second job with same name but different value
    from receipt_dynamo import JobMetric

    second_job_metric = JobMetric(
        second_job_id,
        "loss",  # Same metric name as sample_job_metric
        "2021-01-02T12:30:45",
        0.25,  # Different value
        unit="dimensionless",
        step=50,
        epoch=1,
    )

    # Add all job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)  # Original job, loss
    job_metric_dynamo.add_job_metric(
        sample_job_metric_later
    )  # Original job, loss (later)
    job_metric_dynamo.add_job_metric(second_job_metric)  # Second job, loss

    # Get metrics by name across jobs
    metrics, last_evaluated_key = job_metric_dynamo.get_metrics_by_name_across_jobs(
        "loss"
    )

    # Verify
    assert len(metrics) >= 3
    assert all(m.metric_name == "loss" for m in metrics)

    # Verify job grouping - first check original job metrics
    original_job_metrics = [m for m in metrics if m.job_id == sample_job.job_id]
    assert len(original_job_metrics) == 2

    # Then check second job metrics
    second_job_metrics = [m for m in metrics if m.job_id == second_job_id]
    assert len(second_job_metrics) == 1
    assert second_job_metrics[0].value == 0.25

    # Verify metrics are grouped by job and ordered by timestamp
    # The GSI2 should return metrics grouped by job since the sort key starts
    # with JOB#
    job_ids_in_order = [m.job_id for m in metrics]

    # Get all unique job IDs in the order they appear
    unique_job_ids = []
    for job_id in job_ids_in_order:
        if job_id not in unique_job_ids:
            unique_job_ids.append(job_id)

    # Check that we have at least 2 different job IDs
    assert len(unique_job_ids) >= 2


@pytest.mark.integration
def test_getMetricsByNameAcrossJobs_with_limit(
    job_metric_dynamo, sample_job, sample_job_metric, sample_job_metric_later
):
    """Test getting metrics by name across jobs with a limit"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Create a second job with different ID
    from uuid import uuid4

    from receipt_dynamo import Job

    second_job_id = str(uuid4())
    second_job = Job(
        second_job_id,
        "Second Test Job",
        "Test description for second job",
        "2021-01-02T00:00:00",
        "test_user",
        "pending",
        "medium",
        {},
    )
    job_metric_dynamo.add_job(second_job)

    # Create metrics for both jobs
    from receipt_dynamo import JobMetric

    second_job_metric = JobMetric(
        second_job_id,
        "loss",
        "2021-01-02T12:30:45",
        0.25,
        unit="dimensionless",
        step=50,
        epoch=1,
    )

    # Add all job metrics
    job_metric_dynamo.add_job_metric(sample_job_metric)
    job_metric_dynamo.add_job_metric(sample_job_metric_later)
    job_metric_dynamo.add_job_metric(second_job_metric)

    # Get metrics by name across jobs with limit=2
    metrics, last_evaluated_key = job_metric_dynamo.get_metrics_by_name_across_jobs(
        "loss", limit=2
    )

    # Verify
    assert len(metrics) == 2
    assert all(m.metric_name == "loss" for m in metrics)
    assert last_evaluated_key is not None  # Should have more metrics to fetch


@pytest.mark.integration
def test_getMetricsByNameAcrossJobs_not_found(
    job_metric_dynamo, sample_job, sample_job_metric
):
    """Test getting metrics by name across jobs when none match"""
    # Add the job first
    job_metric_dynamo.add_job(sample_job)

    # Add the job metric
    job_metric_dynamo.add_job_metric(sample_job_metric)  # loss

    # Get metrics by a non-existent name
    metrics, last_evaluated_key = job_metric_dynamo.get_metrics_by_name_across_jobs(
        "nonexistent"
    )

    # Verify
    assert len(metrics) == 0
    assert last_evaluated_key is None


@pytest.mark.integration
def test_getMetricsByNameAcrossJobs_raises_value_error(job_metric_dynamo):
    """
    Test that getMetricsByNameAcrossJobs raises ValueError when metric_name is
    None
    """
    with pytest.raises(
        ValueError,
        match="Metric name is required and must be a non-empty string.",
    ):
        job_metric_dynamo.get_metrics_by_name_across_jobs(None)


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
    Test that validate_last_evaluated_key raises ValueError when the format is
    invalid
    """
    with pytest.raises(
        ValueError,
        match="LastEvaluatedKey.* must be a dict containing a key 'S'",
    ):
        validate_last_evaluated_key({"PK": {"S": "value"}, "SK": "not a dict"})


@pytest.mark.integration
def test_listJobMetrics_raises_client_error(job_metric_dynamo, sample_job, mocker):
    """
    Test that listJobMetrics raises an exception when a ClientError occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_metric_dynamo._client,
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
    with pytest.raises(Exception, match="Table not found"):
        job_metric_dynamo.list_job_metrics(sample_job.job_id)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getMetricsByName_raises_client_error(job_metric_dynamo, mocker):
    """
    Test that getMetricsByName raises an exception when a ClientError occurs
    """
    # Mock the client to raise a ClientError
    mock_query = mocker.patch.object(
        job_metric_dynamo._client,
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
    with pytest.raises(Exception, match="Table not found"):
        job_metric_dynamo.get_metrics_by_name("loss")
    mock_query.assert_called_once()
