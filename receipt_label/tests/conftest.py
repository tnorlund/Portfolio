"""Shared pytest fixtures for receipt_label tests."""

import json
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from typing import Dict, List, Any, Optional

import pytest
from moto import mock_aws
import boto3

from receipt_dynamo.entities import (
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptMetadata)
from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.client_manager import ClientManager


def pytest_runtest_setup(item):
    """Setup for each test - skip performance tests if environment variable is set."""
    if item.get_closest_marker("performance"):
        if os.environ.get("SKIP_PERFORMANCE_TESTS", "").lower() == "true":
            pytest.skip(
                "Skipping performance tests (SKIP_PERFORMANCE_TESTS=true)"
            )


# ===== Test Data Fixtures =====


@pytest.fixture
def sample_receipt_words():
    """Sample receipt words for testing."""
    return [
        ReceiptWord(
            image_id="IMG001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="Walmart",
            x1=100,
            y1=100,
            x2=200,
            y2=120),
        ReceiptWord(
            image_id="IMG001",
            receipt_id=1,
            line_id=2,
            word_id=1,
            text="$12.99",
            x1=150,
            y1=150,
            x2=200,
            y2=170),
        ReceiptWord(
            image_id="IMG001",
            receipt_id=1,
            line_id=3,
            word_id=1,
            text="12/25/2023",
            x1=100,
            y1=200,
            x2=180,
            y2=220),
        ReceiptWord(
            image_id="IMG001",
            receipt_id=1,
            line_id=4,
            word_id=1,
            text="(555) 123-4567",
            x1=100,
            y1=250,
            x2=220,
            y2=270),
    ]


@pytest.fixture
def sample_receipt_labels():
    """Sample receipt labels for testing."""
    return [
        ReceiptWordLabel(
            image_id="IMG001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="MERCHANT_NAME",
            validation_status="NONE",
            timestamp_added=datetime.now(timezone.utc)),
        ReceiptWordLabel(
            image_id="IMG001",
            receipt_id=1,
            line_id=2,
            word_id=1,
            label="GRAND_TOTAL",
            validation_status="VALID",
            timestamp_added=datetime.now(timezone.utc)),
    ]


@pytest.fixture
def sample_receipt_metadata():
    """Sample receipt metadata for testing."""
    return ReceiptMetadata(
        image_id="IMG001",
        receipt_id=1,
        canonical_merchant_name="Walmart",
        canonical_address="123 Main St, City, State 12345",
        phone_number="555-123-4567",
        timestamp_processed=datetime.now(timezone.utc))


# ===== Pattern Detection Test Data =====


@pytest.fixture
def currency_test_cases():
    """Parameterized test cases for currency detection."""
    return [
        # (text, expected_match, confidence)
        ("$12.99", True, 0.95),
        ("12.99", True, 0.80),
        ("€45.50", True, 0.90),
        ("£100.00", True, 0.90),
        ("¥1,500", True, 0.85),
        ("$1,234.56", True, 0.95),
        ("not-currency", False, 0.0),
        ("$", False, 0.0),
        ("99", False, 0.0),
    ]


@pytest.fixture
def date_test_cases():
    """Parameterized test cases for date detection."""
    return [
        # (text, expected_match, confidence)
        ("12/25/2023", True, 0.95),
        ("12-25-2023", True, 0.90),
        ("2023-12-25", True, 0.95),
        ("Dec 25, 2023", True, 0.85),
        ("December 25, 2023", True, 0.80),
        ("25/12/2023", True, 0.85),
        ("not-a-date", False, 0.0),
        ("13/32/2023", False, 0.0),
        ("2023", False, 0.0),
    ]


@pytest.fixture
def phone_test_cases():
    """Parameterized test cases for phone number detection."""
    return [
        # (text, expected_match, confidence)
        ("(555) 123-4567", True, 0.95),
        ("555-123-4567", True, 0.90),
        ("555.123.4567", True, 0.85),
        ("5551234567", True, 0.80),
        ("1-555-123-4567", True, 0.95),
        ("+1 555 123 4567", True, 0.90),
        ("not-a-phone", False, 0.0),
        ("123", False, 0.0),
        ("555-CALL", False, 0.0),
    ]


@pytest.fixture
def time_test_cases():
    """Parameterized test cases for time detection."""
    return [
        # (text, expected_match, confidence)
        ("12:30 PM", True, 0.95),
        ("12:30:45", True, 0.90),
        ("23:59:59", True, 0.95),
        ("12:30", True, 0.85),
        ("3:45 am", True, 0.90),
        ("not-time", False, 0.0),
        ("25:00", False, 0.0),
        ("12:60", False, 0.0),
    ]


# ===== Mock Services =====


@pytest.fixture
def mock_chroma_client():
    """Mock ChromaDB client for testing."""
    client = Mock(spec=ChromaDBClient)

    # Mock successful operations
    client.get_by_ids.return_value = {
        "ids": ["test_id_1"],
        "metadatas": [{"valid_labels": [], "invalid_labels": []}],
        "documents": ["test document"],
    }

    client.query_collection.return_value = {
        "ids": [["test_id_1"]],
        "distances": [[0.1]],
        "metadatas": [[{"similarity": 0.9}]],
        "documents": [["similar text"]],
    }

    # Mock collection operations
    mock_collection = Mock()
    mock_collection.update.return_value = None
    mock_collection.upsert.return_value = None
    client.get_collection.return_value = mock_collection

    return client


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for testing."""
    client = Mock()

    # Mock embeddings
    mock_embedding_response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1] * 1536)]
    )
    client.embeddings.create.return_value = mock_embedding_response

    # Mock chat completion
    mock_completion_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"result": "test"}'),
                finish_reason="stop")
        ],
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, total_tokens=150
        ))
    client.chat.completions.create.return_value = mock_completion_response

    # Mock batch operations
    mock_batch = SimpleNamespace(
        id="batch_123", status="completed", output_file_id="file_123"
    )
    client.batches.create.return_value = mock_batch
    client.batches.retrieve.return_value = mock_batch

    # Mock file operations
    client.files.content.return_value = b'{"custom_id": "test", "response": {"body": {"choices": [{"message": {"content": "{\\"results\\": []}"}}]}}}'

    return client


@pytest.fixture
def mock_dynamo_client():
    """Mock DynamoDB client for testing."""
    client = Mock()

    # Mock successful operations
    client.put_item.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200}
    }
    client.batch_write_item.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200}
    }
    client.query.return_value = {
        "Items": [],
        "Count": 0,
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }

    return client


@pytest.fixture
def mock_client_manager(
    mock_chroma_client, mock_openai_client, mock_dynamo_client
):
    """Mock ClientManager with all services."""
    manager = Mock(spec=ClientManager)
    manager.chroma = mock_chroma_client
    manager.openai = mock_openai_client
    manager.dynamo = mock_dynamo_client
    return manager


# ===== AWS Mocking with Moto =====


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def mock_dynamodb(aws_credentials):
    """Mock DynamoDB service."""
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture
def mock_s3(aws_credentials):
    """Mock S3 service."""
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture
def mock_sqs(aws_credentials):
    """Mock SQS service."""
    with mock_aws():
        yield boto3.client("sqs", region_name="us-east-1")


@pytest.fixture
def dynamodb_tables(mock_dynamodb):
    """Create mock DynamoDB tables for testing."""
    # Receipt Word Labels table
    labels_table = mock_dynamodb.create_table(
        TableName="receipt-word-labels",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST")

    # AI Usage Tracking table
    tracking_table = mock_dynamodb.create_table(
        TableName="ai-usage-tracking",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST")

    return {"labels": labels_table, "tracking": tracking_table}


@pytest.fixture
def s3_buckets(mock_s3):
    """Create mock S3 buckets for testing."""
    buckets = {
        "chroma-vectors": "test-chroma-vectors",
        "batch-files": "test-batch-files",
    }

    for bucket_name in buckets.values():
        mock_s3.create_bucket(Bucket=bucket_name)

    return buckets


# ===== Test Environment Configuration =====


@pytest.fixture
def test_environment():
    """Configure test environment variables."""
    original_env = {}
    test_vars = {
        "OPENAI_API_KEY": "test-key",
        "CHROMA_DB_PATH": "/tmp/test_chroma",
        "AWS_REGION": "us-east-1",
        "ENVIRONMENT": "test",
        "USE_STUB_APIS": "true",
    }

    # Save original values
    for key in test_vars:
        if key in os.environ:
            original_env[key] = os.environ[key]

    # Set test values
    for key, value in test_vars.items():
        os.environ[key] = value

    yield test_vars

    # Restore original values
    for key in test_vars:
        if key in original_env:
            os.environ[key] = original_env[key]
        else:
            os.environ.pop(key, None)


# ===== Performance Testing Fixtures =====


@pytest.fixture
def performance_timer():
    """Timer fixture for performance testing."""
    import time

    class Timer:
        def __init__(self):
            self.start_time = None
            self.end_time = None

        def start(self):
            self.start_time = time.time()

        def stop(self):
            self.end_time = time.time()
            return self.elapsed()

        def elapsed(self):
            if self.start_time and self.end_time:
                return self.end_time - self.start_time
            return None

    return Timer()


# ===== Stub API Fixtures =====


@pytest.fixture
def stub_all_apis(mock_client_manager):
    """Stub all external APIs for cost-free testing."""

    with patch(
        "receipt_label.utils.get_client_manager",
        return_value=mock_client_manager):
        yield mock_client_manager


@pytest.fixture
def pattern_detection_test_data():
    """Comprehensive test data for pattern detection."""
    return {
        "receipts": [
            {
                "merchant": "Walmart",
                "words": [
                    {"text": "Walmart", "expected_labels": ["MERCHANT_NAME"]},
                    {
                        "text": "$12.99",
                        "expected_labels": ["CURRENCY", "GRAND_TOTAL"],
                    },
                    {"text": "12/25/2023", "expected_labels": ["DATE"]},
                    {
                        "text": "(555) 123-4567",
                        "expected_labels": ["PHONE_NUMBER"],
                    },
                ],
            },
            {
                "merchant": "McDonalds",
                "words": [
                    {
                        "text": "McDonald's",
                        "expected_labels": ["MERCHANT_NAME"],
                    },
                    {"text": "Big Mac", "expected_labels": ["PRODUCT_NAME"]},
                    {
                        "text": "$5.49",
                        "expected_labels": ["CURRENCY", "UNIT_PRICE"],
                    },
                    {"text": "3:45 PM", "expected_labels": ["TIME"]},
                ],
            },
        ]
    }
