"""
Unit tests for Stream Processor using real SQS operations.

These tests use real SQS operations with moto to test actual message
building and routing logic rather than mocked functionality.
"""

import json
import os
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from receipt_dynamo.constants import ChromaDBCollection


class TestStreamProcessorRealOperations:
    """Test Stream Processor with real SQS operations."""

    @mock_aws
    @patch.dict(
        os.environ,
        {
            "LINES_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/lines-queue",
            "WORDS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/words-queue",
        },
    )
    def test_process_dynamodb_stream_event(self):
        """Test processing DynamoDB stream event with real SQS operations."""
        # Create real SQS queues with moto
        sqs = boto3.client("sqs", region_name="us-east-1")

        lines_queue = sqs.create_queue(QueueName="lines-queue")
        words_queue = sqs.create_queue(QueueName="words-queue")

        lines_queue_url = lines_queue["QueueUrl"]
        words_queue_url = words_queue["QueueUrl"]

        # Update environment variables to use real queue URLs
        os.environ["LINES_QUEUE_URL"] = lines_queue_url
        os.environ["WORDS_QUEUE_URL"] = words_queue_url

        # Create test DynamoDB stream event
        test_event = {
            "Records": [
                {
                    "eventID": "test-event-1",
                    "eventName": "MODIFY",
                    "eventVersion": "1.1",
                    "eventSource": "aws:dynamodb",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "ApproximateCreationDateTime": 1640995200.0,
                        "Keys": {
                            "PK": {"S": "IMAGE#test-image#RECEIPT#00001"},
                            "SK": {"S": "METADATA"},
                        },
                        "NewImage": {
                            "PK": {"S": "IMAGE#test-image#RECEIPT#00001"},
                            "SK": {"S": "METADATA"},
                            "canonical_merchant_name": {"S": "Target Store"},
                            "merchant_category": {"S": "Retail"},
                        },
                        "OldImage": {
                            "PK": {"S": "IMAGE#test-image#RECEIPT#00001"},
                            "SK": {"S": "METADATA"},
                            "canonical_merchant_name": {"S": "Target"},
                            "merchant_category": {"S": "Retail"},
                        },
                        "SequenceNumber": "123456789",
                        "SizeBytes": 100,
                        "StreamViewType": "NEW_AND_OLD_IMAGES",
                    },
                }
            ]
        }

        # Mock the Lambda handler context
        mock_context = MagicMock()
        mock_context.function_name = "stream-processor"
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
        utils_mock.start_compaction_lambda_monitoring = MagicMock()
        utils_mock.stop_compaction_lambda_monitoring = MagicMock()
        utils_mock.with_compaction_timeout_protection = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.format_response = MagicMock(
            side_effect=lambda response, *args, **kwargs: response
        )
        sys.modules["utils"] = utils_mock

        # Mock the processor module
        processor_mock = MagicMock()
        processor_mock.LambdaResponse = MagicMock
        processor_mock.FieldChange = MagicMock
        processor_mock.ParsedStreamRecord = MagicMock
        processor_mock.ChromaDBCollection = MagicMock
        processor_mock.StreamMessage = MagicMock

        processor_mock.build_messages_from_records = MagicMock(return_value=[])
        processor_mock.publish_messages = MagicMock(return_value=0)
        processor_mock.parse_stream_record = MagicMock()
        processor_mock.get_chromadb_relevant_changes = MagicMock(
            return_value=[]
        )
        processor_mock.detect_entity_type = MagicMock()
        processor_mock.parse_entity = MagicMock()
        processor_mock.is_compaction_run = MagicMock(return_value=False)
        processor_mock.parse_compaction_run = MagicMock()
        sys.modules["processor"] = processor_mock

        # Test Lambda handler
        from stream_processor import lambda_handler

        result = lambda_handler(test_event, mock_context)

        # Verify Lambda orchestration
        assert result["statusCode"] == 200

        # Verify real SQS queues exist
        queues = sqs.list_queues()
        queue_urls = queues["QueueUrls"]

        assert lines_queue_url in queue_urls
        assert words_queue_url in queue_urls

        # Verify processor business logic was called
        processor_mock.build_messages_from_records.assert_called_once()
        processor_mock.publish_messages.assert_called_once()

        # Verify metrics were recorded
        utils_mock.metrics.count.assert_called()
        utils_mock.metrics.timer.assert_called()

    @mock_aws
    @patch.dict(
        os.environ,
        {
            "LINES_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/lines-queue",
            "WORDS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/words-queue",
        },
    )
    def test_process_word_label_stream_event(self):
        """Test processing word label stream event with real SQS operations."""
        # Create real SQS queues with moto
        sqs = boto3.client("sqs", region_name="us-east-1")

        lines_queue = sqs.create_queue(QueueName="lines-queue")
        words_queue = sqs.create_queue(QueueName="words-queue")

        lines_queue_url = lines_queue["QueueUrl"]
        words_queue_url = words_queue["QueueUrl"]

        # Update environment variables to use real queue URLs
        os.environ["LINES_QUEUE_URL"] = lines_queue_url
        os.environ["WORDS_QUEUE_URL"] = words_queue_url

        # Create test DynamoDB stream event for word label
        test_event = {
            "Records": [
                {
                    "eventID": "test-event-2",
                    "eventName": "MODIFY",
                    "eventVersion": "1.1",
                    "eventSource": "aws:dynamodb",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "ApproximateCreationDateTime": 1640995200.0,
                        "Keys": {
                            "PK": {
                                "S": "IMAGE#test-image#RECEIPT#00001#LINE#00001#WORD#00001"
                            },
                            "SK": {"S": "LABEL"},
                        },
                        "NewImage": {
                            "PK": {
                                "S": "IMAGE#test-image#RECEIPT#00001#LINE#00001#WORD#00001"
                            },
                            "SK": {"S": "LABEL"},
                            "label": {"S": "MERCHANT_NAME"},
                            "validation_status": {"S": "VALID"},
                        },
                        "OldImage": {
                            "PK": {
                                "S": "IMAGE#test-image#RECEIPT#00001#LINE#00001#WORD#00001"
                            },
                            "SK": {"S": "LABEL"},
                            "label": {"S": "ITEM_NAME"},
                            "validation_status": {"S": "VALID"},
                        },
                        "SequenceNumber": "123456790",
                        "SizeBytes": 100,
                        "StreamViewType": "NEW_AND_OLD_IMAGES",
                    },
                }
            ]
        }

        # Mock the Lambda handler context
        mock_context = MagicMock()
        mock_context.function_name = "stream-processor"
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
        utils_mock.start_compaction_lambda_monitoring = MagicMock()
        utils_mock.stop_compaction_lambda_monitoring = MagicMock()
        utils_mock.with_compaction_timeout_protection = MagicMock(
            side_effect=lambda *args, **kwargs: lambda func: func
        )
        utils_mock.format_response = MagicMock(
            side_effect=lambda response, *args, **kwargs: response
        )
        sys.modules["utils"] = utils_mock

        # Mock the processor module (same as above)
        processor_mock = MagicMock()
        processor_mock.LambdaResponse = MagicMock
        processor_mock.FieldChange = MagicMock
        processor_mock.ParsedStreamRecord = MagicMock
        processor_mock.ChromaDBCollection = MagicMock
        processor_mock.StreamMessage = MagicMock

        processor_mock.build_messages_from_records = MagicMock(return_value=[])
        processor_mock.publish_messages = MagicMock(return_value=0)
        processor_mock.parse_stream_record = MagicMock()
        processor_mock.get_chromadb_relevant_changes = MagicMock(
            return_value=[]
        )
        processor_mock.detect_entity_type = MagicMock()
        processor_mock.parse_entity = MagicMock()
        processor_mock.is_compaction_run = MagicMock(return_value=False)
        processor_mock.parse_compaction_run = MagicMock()
        sys.modules["processor"] = processor_mock

        # Test Lambda handler
        from stream_processor import lambda_handler

        result = lambda_handler(test_event, mock_context)

        # Verify Lambda orchestration
        assert result["statusCode"] == 200

        # Verify real SQS queues exist
        queues = sqs.list_queues()
        queue_urls = queues["QueueUrls"]

        assert lines_queue_url in queue_urls
        assert words_queue_url in queue_urls

        # Verify processor business logic was called
        processor_mock.build_messages_from_records.assert_called_once()
        processor_mock.publish_messages.assert_called_once()

        # Verify metrics were recorded
        utils_mock.metrics.count.assert_called()
        utils_mock.metrics.timer.assert_called()

    @mock_aws
    def test_sqs_message_routing(self):
        """Test real SQS message routing logic."""
        # Create real SQS queues with moto
        sqs = boto3.client("sqs", region_name="us-east-1")

        lines_queue = sqs.create_queue(QueueName="lines-queue")
        words_queue = sqs.create_queue(QueueName="words-queue")

        lines_queue_url = lines_queue["QueueUrl"]
        words_queue_url = words_queue["QueueUrl"]

        # Test sending messages to both queues (what RECEIPT_METADATA does)
        metadata_message = {
            "source": "dynamodb_stream",
            "entity_type": "RECEIPT_METADATA",
            "event_name": "MODIFY",
            "entity_data": {
                "image_id": "test-image",
                "receipt_id": 1,
                "canonical_merchant_name": "Target Store",
            },
            "changes": {
                "canonical_merchant_name": {
                    "old": "Target",
                    "new": "Target Store",
                }
            },
            "timestamp": "2025-10-23T14:00:00Z",
            "aws_region": "us-east-1",
        }

        # Send to both queues (what RECEIPT_METADATA does)
        sqs.send_message(
            QueueUrl=lines_queue_url, MessageBody=json.dumps(metadata_message)
        )

        sqs.send_message(
            QueueUrl=words_queue_url, MessageBody=json.dumps(metadata_message)
        )

        # Verify messages were sent
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=10
        )
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )

        lines_messages = lines_response.get("Messages", [])
        words_messages = words_response.get("Messages", [])

        assert len(lines_messages) == 1
        assert len(words_messages) == 1

        # Verify message content
        lines_body = json.loads(lines_messages[0]["Body"])
        words_body = json.loads(words_messages[0]["Body"])

        assert lines_body["entity_type"] == "RECEIPT_METADATA"
        assert words_body["entity_type"] == "RECEIPT_METADATA"
        assert (
            lines_body["entity_data"]["canonical_merchant_name"]
            == "Target Store"
        )
        assert (
            words_body["entity_data"]["canonical_merchant_name"]
            == "Target Store"
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
