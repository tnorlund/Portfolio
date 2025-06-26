"""Unit tests for AWS utilities."""

from unittest.mock import Mock, patch

import pytest

from receipt_trainer.utils.aws import get_dynamo_table, get_s3_bucket, load_env


@pytest.fixture
def mock_pulumi_outputs():
    """Create mock Pulumi stack outputs."""
    return {
        "dynamodb_table_name": "test-table",
        "s3_bucket_name": "test-bucket",
    }


@patch("receipt_trainer.utils.aws._load_pulumi_env")
def test_load_env(mock_load_pulumi):
    """Test loading Pulumi environment outputs."""
    mock_outputs = {"test": "output"}
    mock_load_pulumi.return_value = mock_outputs

    result = load_env("test")
    assert result == mock_outputs
    mock_load_pulumi.assert_called_once_with("test")


@patch("receipt_trainer.utils.aws._load_pulumi_env")
def test_load_env_error(mock_load_pulumi):
    """Test error handling in load_env."""
    mock_load_pulumi.side_effect = Exception("Test error")

    with pytest.raises(Exception) as exc_info:
        load_env("test")
    assert "Test error" in str(exc_info.value)


@patch("receipt_trainer.utils.aws.load_env")
def test_get_dynamo_table_explicit(mock_load_env):
    """Test getting DynamoDB table name with explicit value."""
    table_name = get_dynamo_table(table_name="explicit-table")
    assert table_name == "explicit-table"
    mock_load_env.assert_not_called()


@patch("receipt_trainer.utils.aws.load_env")
def test_get_dynamo_table_from_pulumi(mock_load_env, mock_pulumi_outputs):
    """Test getting DynamoDB table name from Pulumi outputs."""
    mock_load_env.return_value = mock_pulumi_outputs

    table_name = get_dynamo_table(env="test")
    assert table_name == "test-table"
    mock_load_env.assert_called_once_with("test")


@patch("receipt_trainer.utils.aws.load_env")
def test_get_dynamo_table_missing(mock_load_env):
    """Test error when DynamoDB table name is missing from Pulumi outputs."""
    mock_load_env.return_value = {}

    with pytest.raises(ValueError) as exc_info:
        get_dynamo_table(env="test")
    assert "Could not find DynamoDB table name" in str(exc_info.value)


@patch("receipt_trainer.utils.aws.load_env")
def test_get_s3_bucket_explicit(mock_load_env):
    """Test getting S3 bucket name with explicit value."""
    bucket_name = get_s3_bucket(bucket_name="explicit-bucket")
    assert bucket_name == "explicit-bucket"
    mock_load_env.assert_not_called()


@patch("receipt_trainer.utils.aws.load_env")
def test_get_s3_bucket_from_pulumi(mock_load_env, mock_pulumi_outputs):
    """Test getting S3 bucket name from Pulumi outputs."""
    mock_load_env.return_value = mock_pulumi_outputs

    bucket_name = get_s3_bucket(env="test")
    assert bucket_name == "test-bucket"
    mock_load_env.assert_called_once_with("test")


@patch("receipt_trainer.utils.aws.load_env")
def test_get_s3_bucket_missing(mock_load_env):
    """Test error when S3 bucket name is missing from Pulumi outputs."""
    mock_load_env.return_value = {}

    with pytest.raises(ValueError) as exc_info:
        get_s3_bucket(env="test")
    assert "Could not find S3 bucket name" in str(exc_info.value)
