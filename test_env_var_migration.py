#!/usr/bin/env python3
"""
Test script to verify environment variable standardization works correctly.
"""

import os
import warnings
from unittest.mock import patch

from receipt_label.utils.client_manager import ClientConfig


def test_new_variable_name():
    """Test that DYNAMODB_TABLE_NAME works correctly."""
    env_vars = {
        "DYNAMODB_TABLE_NAME": "new-table-name",
        "OPENAI_API_KEY": "test-key",
        "PINECONE_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
    }

    with patch.dict(os.environ, env_vars, clear=True):
        config = ClientConfig.from_env()
        assert config.dynamo_table == "new-table-name"
        print("✅ DYNAMODB_TABLE_NAME works correctly")


def test_old_variable_with_warning():
    """Test that DYNAMO_TABLE_NAME still works but shows deprecation warning."""
    env_vars = {
        "DYNAMO_TABLE_NAME": "old-table-name",  # Old variable name
        "OPENAI_API_KEY": "test-key",
        "PINECONE_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
    }

    with patch.dict(os.environ, env_vars, clear=True):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config = ClientConfig.from_env()

            # Check that it still works
            assert config.dynamo_table == "old-table-name"

            # Check that warning was issued
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "DYNAMO_TABLE_NAME is deprecated" in str(w[0].message)
            print("✅ DYNAMO_TABLE_NAME works with deprecation warning")


def test_new_variable_takes_precedence():
    """Test that DYNAMODB_TABLE_NAME takes precedence when both are set."""
    env_vars = {
        "DYNAMODB_TABLE_NAME": "new-table-name",
        "DYNAMO_TABLE_NAME": "old-table-name",
        "OPENAI_API_KEY": "test-key",
        "PINECONE_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
    }

    with patch.dict(os.environ, env_vars, clear=True):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config = ClientConfig.from_env()

            # New variable should take precedence
            assert config.dynamo_table == "new-table-name"

            # No warning should be issued when new variable is present
            assert len(w) == 0
            print("✅ DYNAMODB_TABLE_NAME takes precedence (no warning)")


def test_missing_both_variables():
    """Test that missing both variables raises appropriate error."""
    env_vars = {
        "OPENAI_API_KEY": "test-key",
        "PINECONE_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
    }

    with patch.dict(os.environ, env_vars, clear=True):
        try:
            config = ClientConfig.from_env()
            assert False, "Should have raised KeyError"
        except KeyError as e:
            assert (
                "Either DYNAMODB_TABLE_NAME or DYNAMO_TABLE_NAME must be set"
                in str(e)
            )
            print("✅ Missing both variables raises clear error")


if __name__ == "__main__":
    print("Testing environment variable migration...")

    test_new_variable_name()
    test_old_variable_with_warning()
    test_new_variable_takes_precedence()
    test_missing_both_variables()

    print(
        "\n🎉 All tests passed! Environment variable migration is working correctly."
    )
