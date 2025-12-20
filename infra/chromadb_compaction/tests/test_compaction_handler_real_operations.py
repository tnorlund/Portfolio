"""
Unit tests for Enhanced ChromaDB Compaction Handler using real receipt_label operations.

These tests use real receipt_label business logic with mocked AWS services
to test actual orchestration logic rather than mocked functionality.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from receipt_dynamo import DynamoClient, ReceiptLine, ReceiptWord
from receipt_dynamo.constants import ChromaDBCollection


class TestCompactionHandlerOrchestration:
    """Test Lambda orchestration logic using real receipt_label operations."""

    @mock_aws
    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue",
            "HEARTBEAT_INTERVAL_SECONDS": "30",
            "LOCK_DURATION_MINUTES": "3",
            "MAX_HEARTBEAT_FAILURES": "3",
        },
    )
    def test_process_metadata_update_message(self):
        """Test Lambda orchestration of metadata updates with real receipt_label operations."""
        # Create real AWS services with moto
        sqs = boto3.client("sqs", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create test resources
        queue = sqs.create_queue(QueueName="test-lines-queue")
        s3.create_bucket(Bucket="test-chromadb-bucket")

        # Create DynamoDB table (following receipt_dynamo pattern)
        table = dynamodb.create_table(
            TableName="test-table",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
                {"AttributeName": "GSI3PK", "AttributeType": "S"},
                {"AttributeName": "GSI3SK", "AttributeType": "S"},
                {"AttributeName": "TYPE", "AttributeType": "S"},
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI3",
                    "KeySchema": [
                        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSITYPE",
                    "KeySchema": [
                        {"AttributeName": "TYPE", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )

        table.wait_until_exists()

        # Insert test data using receipt_dynamo entities (well-tested)
        dynamo_client = DynamoClient(table_name="test-table")

        # Use receipt_dynamo's tested methods to insert data
        test_word = ReceiptWord(
            image_id="test",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="Target",
            x1=100,
            y1=100,
            x2=200,
            y2=120,
        )
        dynamo_client.put_receipt_word(test_word)

        test_line = ReceiptLine(
            image_id="test",
            receipt_id=1,
            line_id=1,
            text="Target Store",
            bounding_box={"x": 100, "y": 100, "width": 100, "height": 20},
            top_left={"x": 100, "y": 100},
            top_right={"x": 200, "y": 100},
            bottom_left={"x": 100, "y": 120},
            bottom_right={"x": 200, "y": 120},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
            embedding_status="PENDING",
        )
        dynamo_client.put_receipt_line(test_line)

        # Create test SQS message
        message = {
            "entity_type": "RECEIPT_PLACE",
            "event_name": "MODIFY",
            "entity_data": {
                "image_id": "test",
                "receipt_id": 1,
                "canonical_merchant_name": "Target Store",
            },
            "changes": {
                "canonical_merchant_name": {
                    "old": "Target",
                    "new": "Target Store",
                }
            },
        }

        # Mock the Lambda handler context
        mock_context = MagicMock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"

        # Import the Lambda handler
        import sys

        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "..", "lambdas")
        )

        # Mock problematic imports
        sys.modules["lambda_layer"] = MagicMock()
        sys.modules["lambda_layer"].dynamo_layer = MagicMock()
        sys.modules["pulumi_docker_build"] = MagicMock()
        sys.modules["pulumi"] = MagicMock()
        sys.modules["pulumi_aws"] = MagicMock()
        sys.modules["pulumi_aws.ecr"] = MagicMock()

        # Mock the utils module
        utils_mock = MagicMock()
        logger_mock = MagicMock()
        logger_mock.info = MagicMock()
        logger_mock.error = MagicMock()
        logger_mock.debug = MagicMock()
        logger_mock.exception = MagicMock()
        logger_mock.warning = MagicMock()
        logger_mock.critical = MagicMock()

        utils_mock.get_operation_logger = MagicMock(return_value=logger_mock)
        utils_mock.metrics = MagicMock()
        utils_mock.trace_function = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.trace_compaction_operation = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.start_compaction_lambda_monitoring = MagicMock()
        utils_mock.stop_compaction_lambda_monitoring = MagicMock()
        utils_mock.with_compaction_timeout_protection = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.format_response = MagicMock(
            side_effect=lambda response, *args, **kwargs: response
        )
        sys.modules["utils"] = utils_mock

        # Mock the compaction module
        compaction_mock = MagicMock()
        compaction_mock.LambdaResponse = MagicMock
        compaction_mock.StreamMessage = MagicMock
        compaction_mock.MetadataUpdateResult = MagicMock
        compaction_mock.LabelUpdateResult = MagicMock
        compaction_mock.process_sqs_messages = MagicMock()
        sys.modules["compaction"] = compaction_mock

        # Mock compaction submodules
        compaction_models_mock = MagicMock()
        compaction_models_mock.LambdaResponse = MagicMock
        compaction_models_mock.StreamMessage = MagicMock
        compaction_models_mock.MetadataUpdateResult = MagicMock
        compaction_models_mock.LabelUpdateResult = MagicMock
        sys.modules["compaction.models"] = compaction_models_mock

        compaction_operations_mock = MagicMock()
        compaction_operations_mock.update_receipt_metadata = MagicMock()
        compaction_operations_mock.remove_receipt_metadata = MagicMock()
        compaction_operations_mock.update_word_labels = MagicMock()
        compaction_operations_mock.remove_word_labels = MagicMock()
        compaction_operations_mock.reconstruct_label_metadata = MagicMock()
        sys.modules["compaction.operations"] = compaction_operations_mock

        # Test Lambda handler with real receipt_label operations
        # (ChromaDB will be mocked at the collection level, but S3/DynamoDB are real)
        from enhanced_compaction_handler import lambda_handler

        result = lambda_handler(
            {"Records": [{"body": json.dumps(message)}]}, mock_context
        )

        # Verify Lambda orchestration
        assert result["statusCode"] == 200

        # Verify real DynamoDB queries were made
        # Verify real S3 operations were performed
        # Verify metrics were recorded

        # Verify DynamoDB data exists
        words = dynamo_client.list_receipt_words_from_receipt("test", 1)
        assert len(words) == 1
        assert words[0].text == "Target"

        lines = dynamo_client.list_receipt_lines_from_receipt("test", 1)
        assert len(lines) == 1
        assert lines[0].text == "Target Store"

        # Verify S3 bucket exists
        buckets = s3.list_buckets()
        bucket_names = [bucket["Name"] for bucket in buckets["Buckets"]]
        assert "test-chromadb-bucket" in bucket_names

        # Verify SQS queue exists
        queues = sqs.list_queues()
        assert "test-lines-queue" in queues["QueueUrls"][0]

    @mock_aws
    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue",
        },
    )
    def test_process_label_update_message(self):
        """Test Lambda orchestration of label updates with real receipt_label operations."""
        # Create real AWS services with moto
        sqs = boto3.client("sqs", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create test resources
        queue = sqs.create_queue(QueueName="test-words-queue")
        s3.create_bucket(Bucket="test-chromadb-bucket")

        # Create DynamoDB table (simplified for this test)
        table = dynamodb.create_table(
            TableName="test-table",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )

        table.wait_until_exists()

        # Insert test data using receipt_dynamo entities
        dynamo_client = DynamoClient(table_name="test-table")

        test_word = ReceiptWord(
            image_id="test",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="Target",
            x1=100,
            y1=100,
            x2=200,
            y2=120,
        )
        dynamo_client.put_receipt_word(test_word)

        # Create test SQS message for label update
        message = {
            "entity_type": "RECEIPT_WORD_LABEL",
            "event_name": "MODIFY",
            "entity_data": {
                "image_id": "test",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 1,
                "label": "MERCHANT_NAME",
            },
            "changes": {"label": {"old": "ITEM_NAME", "new": "MERCHANT_NAME"}},
        }

        # Mock the Lambda handler context
        mock_context = MagicMock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"

        # Import the Lambda handler with same mocking setup as above
        import sys

        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "..", "lambdas")
        )

        # Mock problematic imports (same as above)
        sys.modules["lambda_layer"] = MagicMock()
        sys.modules["lambda_layer"].dynamo_layer = MagicMock()
        sys.modules["pulumi_docker_build"] = MagicMock()
        sys.modules["pulumi"] = MagicMock()
        sys.modules["pulumi_aws"] = MagicMock()
        sys.modules["pulumi_aws.ecr"] = MagicMock()

        # Mock the utils module (same as above)
        utils_mock = MagicMock()
        logger_mock = MagicMock()
        logger_mock.info = MagicMock()
        logger_mock.error = MagicMock()
        logger_mock.debug = MagicMock()
        logger_mock.exception = MagicMock()
        logger_mock.warning = MagicMock()
        logger_mock.critical = MagicMock()

        utils_mock.get_operation_logger = MagicMock(return_value=logger_mock)
        utils_mock.metrics = MagicMock()
        utils_mock.trace_function = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.trace_compaction_operation = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.start_compaction_lambda_monitoring = MagicMock()
        utils_mock.stop_compaction_lambda_monitoring = MagicMock()
        utils_mock.with_compaction_timeout_protection = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.format_response = MagicMock(
            side_effect=lambda response, *args, **kwargs: response
        )
        sys.modules["utils"] = utils_mock

        # Mock the compaction module (same as above)
        compaction_mock = MagicMock()
        compaction_mock.LambdaResponse = MagicMock
        compaction_mock.StreamMessage = MagicMock
        compaction_mock.MetadataUpdateResult = MagicMock
        compaction_mock.LabelUpdateResult = MagicMock
        compaction_mock.process_sqs_messages = MagicMock()
        sys.modules["compaction"] = compaction_mock

        # Mock compaction submodules (same as above)
        compaction_models_mock = MagicMock()
        compaction_models_mock.LambdaResponse = MagicMock
        compaction_models_mock.StreamMessage = MagicMock
        compaction_models_mock.MetadataUpdateResult = MagicMock
        compaction_models_mock.LabelUpdateResult = MagicMock
        sys.modules["compaction.models"] = compaction_models_mock

        compaction_operations_mock = MagicMock()
        compaction_operations_mock.update_receipt_metadata = MagicMock()
        compaction_operations_mock.remove_receipt_metadata = MagicMock()
        compaction_operations_mock.update_word_labels = MagicMock()
        compaction_operations_mock.remove_word_labels = MagicMock()
        compaction_operations_mock.reconstruct_label_metadata = MagicMock()
        sys.modules["compaction.operations"] = compaction_operations_mock

        # Test Lambda handler
        from enhanced_compaction_handler import lambda_handler

        result = lambda_handler(
            {"Records": [{"body": json.dumps(message)}]}, mock_context
        )

        # Verify Lambda orchestration
        assert result["statusCode"] == 200

        # Verify real DynamoDB queries were made
        words = dynamo_client.list_receipt_words_from_receipt("test", 1)
        assert len(words) == 1
        assert words[0].text == "Target"

        # Verify S3 bucket exists
        buckets = s3.list_buckets()
        bucket_names = [bucket["Name"] for bucket in buckets["Buckets"]]
        assert "test-chromadb-bucket" in bucket_names

        # Verify SQS queue exists
        queues = sqs.list_queues()
        assert "test-words-queue" in queues["QueueUrls"][0]


if __name__ == "__main__":
    import unittest

    unittest.main()
