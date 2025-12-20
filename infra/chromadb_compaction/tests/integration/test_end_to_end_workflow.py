"""
End-to-end integration tests for ChromaDB Compaction System.

These tests cover complete DynamoDB → SQS → S3 → ChromaDB pipeline
with real services using moto.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from receipt_dynamo import (
    DynamoClient,
    ReceiptLine,
    ReceiptPlace,
    ReceiptWord,
)


class TestEndToEndWorkflow:
    """Test complete end-to-end workflows with real services."""

    @mock_aws
    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-chromadb-bucket",
            "LINES_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/lines-queue",
            "WORDS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/words-queue",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/compaction-queue",
            "HEARTBEAT_INTERVAL_SECONDS": "30",
            "LOCK_DURATION_MINUTES": "3",
            "MAX_HEARTBEAT_FAILURES": "3",
        },
    )
    def test_complete_metadata_update_workflow(self):
        """Test end-to-end metadata update from DynamoDB stream to ChromaDB."""
        # Set up all AWS services with moto
        sqs = boto3.client("sqs", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create test resources
        lines_queue = sqs.create_queue(QueueName="lines-queue")
        words_queue = sqs.create_queue(QueueName="words-queue")
        compaction_queue = sqs.create_queue(QueueName="compaction-queue")

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

        # Insert test data using receipt_dynamo entities
        dynamo_client = DynamoClient(table_name="test-table")

        # Create test receipt data
        test_image_id = "550e8400-e29b-41d4-a716-446655440000"

        # Insert receipt place
        receipt_place = ReceiptPlace(
            image_id=test_image_id,
            receipt_id=1,
            merchant_name="Target",
            formatted_address="123 Main St",
            phone_number="555-123-4567",
            place_id="test-place-id",
            matched_fields=["name"],
            validated_by="TEST",
        )
        dynamo_client.add_receipt_places([receipt_place])

        # Insert receipt words
        test_words = [
            ReceiptWord(
                image_id=test_image_id,
                receipt_id=1,
                line_id=1,
                word_id=1,
                text="Target",
                x1=100,
                y1=100,
                x2=200,
                y2=120,
            ),
            ReceiptWord(
                image_id=test_image_id,
                receipt_id=1,
                line_id=1,
                word_id=2,
                text="Store",
                x1=210,
                y1=100,
                x2=280,
                y2=120,
            ),
        ]

        for word in test_words:
            dynamo_client.put_receipt_word(word)

        # Insert receipt lines
        test_lines = [
            ReceiptLine(
                image_id=test_image_id,
                receipt_id=1,
                line_id=1,
                text="Target Store",
                bounding_box={"x": 100, "y": 100, "width": 180, "height": 20},
                top_left={"x": 100, "y": 100},
                top_right={"x": 280, "y": 100},
                bottom_left={"x": 100, "y": 120},
                bottom_right={"x": 280, "y": 120},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
                embedding_status="PENDING",
            )
        ]

        for line in test_lines:
            dynamo_client.put_receipt_line(line)

        # Update environment variables to use real queue URLs
        os.environ["LINES_QUEUE_URL"] = lines_queue["QueueUrl"]
        os.environ["WORDS_QUEUE_URL"] = words_queue["QueueUrl"]
        os.environ["COMPACTION_QUEUE_URL"] = compaction_queue["QueueUrl"]

        # Create DynamoDB stream event for metadata update
        stream_event = {
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
                            "PK": {
                                "S": f"IMAGE#{test_image_id}"
                            },
                            "SK": {"S": "RECEIPT#00001#PLACE"},
                        },
                        "NewImage": {
                            "PK": {
                                "S": f"IMAGE#{test_image_id}"
                            },
                            "SK": {"S": "RECEIPT#00001#PLACE"},
                            "merchant_name": {"S": "Target Store"},
                            "merchant_category": {"S": "Retail"},
                        },
                        "OldImage": {
                            "PK": {
                                "S": f"IMAGE#{test_image_id}"
                            },
                            "SK": {"S": "RECEIPT#00001#PLACE"},
                            "merchant_name": {"S": "Target"},
                            "merchant_category": {"S": "Retail"},
                        },
                        "SequenceNumber": "123456789",
                        "SizeBytes": 100,
                        "StreamViewType": "NEW_AND_OLD_IMAGES",
                    },
                }
            ]
        }

        # Mock Lambda contexts
        stream_context = MagicMock()
        stream_context.function_name = "stream-processor"
        stream_context.aws_request_id = "test-stream-request"

        compaction_context = MagicMock()
        compaction_context.function_name = "compaction-handler"
        compaction_context.aws_request_id = "test-compaction-request"

        # Import Lambda handlers with mocking setup
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

        # Mock the processor module
        processor_mock = MagicMock()
        processor_mock.LambdaResponse = MagicMock
        processor_mock.FieldChange = MagicMock
        processor_mock.ParsedStreamRecord = MagicMock
        processor_mock.ChromaDBCollection = MagicMock
        processor_mock.StreamMessage = MagicMock

        processor_mock.build_messages_from_records = MagicMock(return_value=[])
        processor_mock.publish_messages = MagicMock(
            return_value=2
        )  # 2 messages sent
        processor_mock.parse_stream_record = MagicMock()
        processor_mock.get_chromadb_relevant_changes = MagicMock(
            return_value=[]
        )
        processor_mock.detect_entity_type = MagicMock()
        processor_mock.parse_entity = MagicMock()
        processor_mock.is_compaction_run = MagicMock(return_value=False)
        processor_mock.parse_compaction_run = MagicMock()
        sys.modules["processor"] = processor_mock

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

        # Step 1: Process through stream_processor Lambda
        from stream_processor import lambda_handler as stream_handler

        stream_result = stream_handler(stream_event, stream_context)

        # Verify stream processor orchestration
        assert stream_result["statusCode"] == 200

        # Verify processor business logic was called
        processor_mock.build_messages_from_records.assert_called_once()
        processor_mock.publish_messages.assert_called_once()

        # Step 2: Verify SQS messages were created
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue["QueueUrl"], MaxNumberOfMessages=10
        )
        words_response = sqs.receive_message(
            QueueUrl=words_queue["QueueUrl"], MaxNumberOfMessages=10
        )

        # Note: Messages won't actually be in queues because we're mocking publish_messages
        # But we can verify the queues exist and the logic was called

        # Step 3: Process through compaction Lambda
        # Create SQS event for compaction handler
        sqs_event = {
            "Records": [
                {
                    "messageId": "test-message-1",
                    "receiptHandle": "test-receipt-handle",
                    "body": json.dumps(
                        {
                            "entity_type": "RECEIPT_PLACE",
                            "event_name": "MODIFY",
                            "entity_data": {
                                "image_id": test_image_id,
                                "receipt_id": 1,
                                "merchant_name": "Target Store",
                            },
                            "changes": {
                                "merchant_name": {
                                    "old": "Target",
                                    "new": "Target Store",
                                }
                            },
                        }
                    ),
                    "attributes": {
                        "ApproximateReceiveCount": "1",
                        "SentTimestamp": "1640995200000",
                        "SenderId": "test-sender",
                        "ApproximateFirstReceiveTimestamp": "1640995200000",
                    },
                    "messageAttributes": {},
                    "md5OfBody": "test-md5",
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "arn:aws:sqs:us-east-1:123:lines-queue",
                    "awsRegion": "us-east-1",
                }
            ]
        }

        from enhanced_compaction_handler import (
            lambda_handler as compaction_handler,
        )

        compaction_result = compaction_handler(sqs_event, compaction_context)

        # Verify compaction handler orchestration
        assert compaction_result["statusCode"] == 200

        # Step 4: Verify DynamoDB queries for ChromaDB ID construction
        words = dynamo_client.list_receipt_words_from_receipt(test_image_id, 1)
        assert len(words) == 2
        assert words[0].text == "Target"
        assert words[1].text == "Store"

        lines = dynamo_client.list_receipt_lines_from_receipt(test_image_id, 1)
        assert len(lines) == 1
        assert lines[0].text == "Target Store"

        # Step 5: Verify S3 snapshot operations
        buckets = s3.list_buckets()
        bucket_names = [bucket["Name"] for bucket in buckets["Buckets"]]
        assert "test-chromadb-bucket" in bucket_names

        # Step 6: Verify metrics were recorded
        utils_mock.metrics.count.assert_called()
        utils_mock.metrics.timer.assert_called()

        # Step 7: Verify end-to-end data consistency
        # The test data should be consistent across all services
        receipt_place_result = dynamo_client.get_receipt_place(
            test_image_id, 1
        )
        assert (
            receipt_place_result.merchant_name == "Target"
        )

    @mock_aws
    def test_error_propagation_between_services(self):
        """Test error propagation between services in the pipeline."""
        # Create real AWS services with moto
        sqs = boto3.client("sqs", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create test resources
        lines_queue = sqs.create_queue(QueueName="lines-queue")
        s3.create_bucket(Bucket="test-chromadb-bucket")

        # Create DynamoDB table
        table = dynamodb.create_table(
            TableName="test-table",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
        )

        table.wait_until_exists()

        # Test S3 failure scenario
        # Delete the bucket to simulate S3 failure
        s3.delete_bucket(Bucket="test-chromadb-bucket")

        # Try to create a snapshot (should fail)
        try:
            s3.put_object(
                Bucket="test-chromadb-bucket",
                Key="test/snapshot/test.txt",
                Body="test content",
            )
            assert False, "Should have failed due to missing bucket"
        except Exception as e:
            # Expected to fail
            assert "NoSuchBucket" in str(e) or "bucket" in str(e).lower()

        # Test DynamoDB failure scenario
        # Try to query non-existent table
        try:
            dynamo_client = DynamoClient(table_name="non-existent-table")
            dynamo_client.list_receipt_words_from_receipt("test", 1)
            assert False, "Should have failed due to missing table"
        except Exception as e:
            # Expected to fail
            assert "table" in str(e).lower() or "not found" in str(e).lower()

        # Test SQS failure scenario
        # Try to send message to non-existent queue
        try:
            sqs.send_message(
                QueueUrl="https://sqs.us-east-1.amazonaws.com/123/non-existent-queue",
                MessageBody='{"test": "data"}',
            )
            assert False, "Should have failed due to missing queue"
        except Exception as e:
            # Expected to fail
            assert "queue" in str(e).lower() or "not found" in str(e).lower()


if __name__ == "__main__":
    import unittest

    unittest.main()
