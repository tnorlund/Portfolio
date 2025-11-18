"""
Pytest configuration for handler tests.

This prevents parent package imports that require Pulumi infrastructure.
"""
import sys
import os
from unittest.mock import MagicMock

# Set PYTEST_RUNNING before any imports
os.environ['PYTEST_RUNNING'] = '1'

# Mock parent packages BEFORE pytest tries to import them
# This must happen before any imports from parent packages
if 'embedding_step_functions' not in sys.modules:
    sys.modules['embedding_step_functions'] = MagicMock()
    sys.modules['embedding_step_functions.infrastructure'] = MagicMock()
    
if 'chromadb_compaction' not in sys.modules:
    sys.modules['chromadb_compaction'] = MagicMock()
    # Mock the specific import that's failing
    mock_buckets = MagicMock()
    sys.modules['chromadb_compaction'].ChromaDBBuckets = mock_buckets

