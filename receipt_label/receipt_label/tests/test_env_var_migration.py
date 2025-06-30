"""
Tests for environment variable migration from DYNAMO_TABLE_NAME to DYNAMODB_TABLE_NAME.
"""

import os
import warnings
from unittest.mock import patch

import pytest

from receipt_label.utils.client_manager import ClientConfig


class TestEnvironmentVariableMigration:
    """Test the migration from DYNAMO_TABLE_NAME to DYNAMODB_TABLE_NAME."""

    def test_new_variable_name_works(self):
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

    def test_old_variable_with_deprecation_warning(self):
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

    def test_new_variable_takes_precedence(self):
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

    def test_missing_both_variables_raises_error(self):
        """Test that missing both variables raises appropriate error."""
        env_vars = {
            "OPENAI_API_KEY": "test-key",
            "PINECONE_API_KEY": "test-key",
            "PINECONE_INDEX_NAME": "test-index",
            "PINECONE_HOST": "test.pinecone.io",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(
                KeyError,
                match="Either DYNAMODB_TABLE_NAME or DYNAMO_TABLE_NAME must be set",
            ):
                ClientConfig.from_env()
