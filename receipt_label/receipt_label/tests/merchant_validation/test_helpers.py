"""
Test helper utilities for merchant validation tests.

This module provides common test setup functionality that needs to run
before imports, such as mocking the agents module.
"""

import os
import sys
from unittest.mock import MagicMock


def setup_test_environment():
    """
    Set up the test environment with required mocks and environment variables.
    
    This function should be called at the top of test modules before any
    imports from the merchant_validation package.
    """
    # Set environment variables if not already set
    env_defaults = {
        "DYNAMO_TABLE_NAME": "test-table",
        "OPENAI_API_KEY": "test-openai-key",
        "PINECONE_API_KEY": "test-pinecone-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test-host",
    }
    
    for key, value in env_defaults.items():
        os.environ.setdefault(key, value)
    
    # Mock the agents module if not already mocked
    if 'agents' not in sys.modules:
        mock_agents = MagicMock()
        mock_agents.Agent = MagicMock()
        mock_agents.Runner = MagicMock()
        mock_agents.function_tool = MagicMock()
        sys.modules['agents'] = mock_agents
    
    return sys.modules['agents']