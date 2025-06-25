"""
Pytest configuration for merchant validation tests.

This module provides fixtures specifically for merchant validation tests.
"""

import os
from typing import Dict, Iterator

import pytest


@pytest.fixture(scope="function")
def clean_env_vars() -> Iterator[Dict[str, str]]:
    """
    Fixture to temporarily override environment variables for tests.

    This fixture:
    1. Saves the current environment state
    2. Allows tests to modify environment variables
    3. Restores the original environment state after the test

    Usage:
        def test_something(clean_env_vars):
            os.environ["SOME_VAR"] = "test_value"
            # Test code here
            # Environment will be restored automatically
    """
    # Save current environment
    original_env = dict(os.environ)

    # Define the test environment defaults
    test_env = {
        "DYNAMO_TABLE_NAME": "test-table",
        "OPENAI_API_KEY": "test-openai-key",
        "PINECONE_API_KEY": "test-pinecone-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test-host",
    }

    try:
        yield test_env
    finally:
        # Restore original environment
        os.environ.clear()
        os.environ.update(original_env)


@pytest.fixture
def mock_agents():
    """
    Fixture to get the mocked agents module.

    Note: The actual mocking happens in test_helpers.setup_test_environment()
    which must be called before imports. This fixture just provides access
    to the already-mocked module for tests that need to configure it.
    """
    import sys

    return sys.modules.get("agents")
