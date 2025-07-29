#!/usr/bin/env python
"""Test ParamValidationError behavior with negative limits."""

import sys
import pytest
from unittest.mock import Mock, patch
from botocore.exceptions import ParamValidationError
from receipt_dynamo import DynamoClient

# Mock the client initialization to avoid table creation
def test_negative_limit():
    """Test that negative limits cause ParamValidationError"""
    
    # Create a mock client that will be used in the test
    with patch('receipt_dynamo.data.dynamo_client.boto3') as mock_boto3:
        # Mock the boto3.client call
        mock_dynamodb_client = Mock()
        mock_boto3.client.return_value = mock_dynamodb_client
        
        # Mock table description to avoid real AWS calls
        mock_dynamodb_client.describe_table.return_value = {
            'Table': {'TableName': 'test'}
        }
        
        # Create client
        client = DynamoClient('test')
        
        # Mock the query call to raise ParamValidationError for negative limit
        mock_dynamodb_client.query.side_effect = ParamValidationError(
            report="Invalid value for parameter Limit: -1"
        )
        
        # Test that negative limit raises ParamValidationError
        try:
            client.list_receipt_validation_results(limit=-1)
            print("No exception raised - this is the problem")
        except ParamValidationError as e:
            print(f"SUCCESS: ParamValidationError raised: {e}")
        except Exception as e:
            print(f"WRONG exception type: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_negative_limit()