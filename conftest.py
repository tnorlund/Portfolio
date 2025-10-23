"""
Root-level pytest configuration to mock Pulumi and avoid infrastructure import issues.

This conftest.py is placed at the root level to ensure it runs before any
infrastructure code is imported, preventing Pulumi-related import errors.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest

# Mock Pulumi modules before any infrastructure code is imported
@pytest.fixture(scope="session", autouse=True)
def mock_pulumi():
    """Mock Pulumi modules to prevent infrastructure import issues."""
    
    # Mock pulumi_aws
    pulumi_aws_mock = MagicMock()
    pulumi_aws_mock.s3 = MagicMock()
    pulumi_aws_mock.s3.BucketVersioning = MagicMock()
    pulumi_aws_mock.s3.BucketVersioningV2 = MagicMock()
    pulumi_aws_mock.lambda_ = MagicMock()
    pulumi_aws_mock.sqs = MagicMock()
    pulumi_aws_mock.dynamodb = MagicMock()
    pulumi_aws_mock.iam = MagicMock()
    pulumi_aws_mock.cloudwatch = MagicMock()
    pulumi_aws_mock.logs = MagicMock()
    
    sys.modules['pulumi_aws'] = pulumi_aws_mock
    sys.modules['pulumi_aws.s3'] = pulumi_aws_mock.s3
    sys.modules['pulumi_aws.lambda_'] = pulumi_aws_mock.lambda_
    sys.modules['pulumi_aws.sqs'] = pulumi_aws_mock.sqs
    sys.modules['pulumi_aws.dynamodb'] = pulumi_aws_mock.dynamodb
    sys.modules['pulumi_aws.iam'] = pulumi_aws_mock.iam
    sys.modules['pulumi_aws.cloudwatch'] = pulumi_aws_mock.cloudwatch
    sys.modules['pulumi_aws.logs'] = pulumi_aws_mock.logs
    
    # Mock pulumi
    pulumi_mock = MagicMock()
    pulumi_mock.Output = MagicMock()
    pulumi_mock.ResourceOptions = MagicMock()
    pulumi_mock.ComponentResource = MagicMock()
    pulumi_mock.Resource = MagicMock()
    pulumi_mock.CustomResource = MagicMock()
    pulumi_mock.StackReference = MagicMock()
    pulumi_mock.Config = MagicMock()
    pulumi_mock.get_stack = MagicMock(return_value="test")
    pulumi_mock.get_project = MagicMock(return_value="test")
    
    sys.modules['pulumi'] = pulumi_mock
    
    # Mock other Pulumi-related modules
    sys.modules['pulumi_awsx'] = MagicMock()
    sys.modules['pulumi_awsx.ec2'] = MagicMock()
    sys.modules['pulumi_awsx.ecs'] = MagicMock()
    sys.modules['pulumi_awsx.lb'] = MagicMock()
    
    yield pulumi_mock

# Mock AWS services
@pytest.fixture(autouse=True)
def mock_aws_services():
    """Mock AWS services to avoid infrastructure import issues."""
    with patch('boto3.client') as mock_client, \
         patch('boto3.resource') as mock_resource:
        
        # Mock SQS client
        mock_sqs = MagicMock()
        mock_sqs.send_message.return_value = {'MessageId': 'test-message-id'}
        mock_sqs.send_message_batch.return_value = {'Successful': [], 'Failed': []}
        mock_client.return_value = mock_sqs
        
        # Mock DynamoDB client
        mock_dynamo = MagicMock()
        mock_dynamo.get_item.return_value = {'Item': {}}
        mock_dynamo.put_item.return_value = {}
        mock_dynamo.update_item.return_value = {}
        mock_dynamo.delete_item.return_value = {}
        mock_resource.return_value = mock_dynamo
        
        yield mock_client, mock_resource