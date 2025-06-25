#!/usr/bin/env python3
"""
Simple test script for the label count cache updater Lambda function.
This can be used to test the function logic locally.
"""

import os
import sys
import time
from datetime import datetime
from unittest.mock import Mock, patch

# Add the handler directory to the path so we can import the handler
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "handler"))

# Mock environment variables
os.environ["DYNAMODB_TABLE_NAME"] = "test-table"


def create_mock_dynamo_client():
    """Create a mock DynamoClient for testing."""
    mock_client = Mock()

    # Mock receipt word labels
    mock_word_labels = [
        Mock(validation_status=Mock(value="VALID")),
        Mock(validation_status=Mock(value="VALID")),
        Mock(validation_status=Mock(value="INVALID")),
        Mock(validation_status=Mock(value="PENDING")),
        Mock(validation_status=Mock(value="NONE")),
    ]

    # Mock the getReceiptWordLabelsByLabel method
    mock_client.getReceiptWordLabelsByLabel.return_value = (
        mock_word_labels,
        None,
    )

    # Mock the cache methods
    mock_client.getLabelCountCache.return_value = None  # No existing cache
    mock_client.listLabelCountCaches.return_value = (
        [],
        None,
    )  # Empty cache list
    mock_client.addLabelCountCache.return_value = None
    mock_client.updateLabelCountCache.side_effect = ValueError("does not exist")

    return mock_client


def test_fetch_label_counts():
    """Test the fetch_label_counts function."""
    print("Testing fetch_label_counts...")

    with patch("handler.index.dynamo_client", create_mock_dynamo_client()):
        from handler.index import fetch_label_counts

        label, counts = fetch_label_counts("DATE")

        print(f"Label: {label}")
        print(f"Counts: {counts}")

        # Verify expected counts
        expected = {
            "VALID": 2,
            "INVALID": 1,
            "PENDING": 1,
            "NEEDS_REVIEW": 0,
            "NONE": 1,
        }

        assert counts == expected, f"Expected {expected}, got {counts}"
        print("✓ fetch_label_counts test passed")


def test_update_label_cache():
    """Test the update_label_cache function."""
    print("\nTesting update_label_cache...")

    mock_client = create_mock_dynamo_client()

    with patch("handler.index.dynamo_client", mock_client):
        from handler.index import update_label_cache

        timestamp = datetime.now().isoformat()
        ttl = int(time.time()) + 360
        counts = {
            "VALID": 2,
            "INVALID": 1,
            "PENDING": 1,
            "NEEDS_REVIEW": 0,
            "NONE": 1,
        }

        update_label_cache("DATE", counts, timestamp, ttl)

        # Verify that addLabelCountCache was called (since updateLabelCountCache fails)
        assert (
            mock_client.addLabelCountCache.called
        ), "addLabelCountCache should have been called"
        print("✓ update_label_cache test passed")


def test_handler():
    """Test the main handler function."""
    print("\nTesting handler...")

    mock_client = create_mock_dynamo_client()

    with patch("handler.index.dynamo_client", mock_client):
        with patch(
            "handler.index.CORE_LABELS", ["DATE", "AMOUNT"]
        ):  # Use smaller set for testing
            from handler.index import handler

            event = {}
            context = Mock()

            result = handler(event, context)

            print(f"Handler result: {result}")

            # Verify successful response
            assert (
                result["statusCode"] == 200
            ), f"Expected 200, got {result['statusCode']}"
            assert (
                "updated_labels" in result["body"]
            ), "Response should contain updated_labels"

            print("✓ handler test passed")


def test_validation_status_enum():
    """Test that ValidationStatus enum works as expected."""
    print("\nTesting ValidationStatus enum...")

    # We need to import this to make sure it works
    try:
        from receipt_dynamo.constants import ValidationStatus

        # Check that we can iterate over validation statuses
        statuses = [status.value for status in ValidationStatus]
        print(f"Available validation statuses: {statuses}")

        expected_statuses = [
            "VALID",
            "INVALID",
            "PENDING",
            "NEEDS_REVIEW",
            "NONE",
        ]
        for expected in expected_statuses:
            assert expected in statuses, f"Missing expected status: {expected}"

        print("✓ ValidationStatus enum test passed")
    except ImportError as e:
        print(f"⚠️  Cannot test ValidationStatus enum (import error): {e}")
        print(
            "   This is expected if receipt_dynamo is not available in the test environment"
        )


if __name__ == "__main__":
    print("Running tests for label count cache updater...")

    try:
        test_validation_status_enum()
        test_fetch_label_counts()
        test_update_label_cache()
        test_handler()

        print("\n🎉 All tests passed!")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
