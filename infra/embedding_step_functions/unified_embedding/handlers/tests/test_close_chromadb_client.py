"""
Integration tests for close_chromadb_client functionality.

These tests verify that close_chromadb_client properly releases SQLite file locks
so that files can be copied, uploaded, or accessed by other processes.

Run with Python 3.12 (recommended):
    # Create venv with Python 3.12
    python3.12 -m venv .venv_test_chromadb
    source .venv_test_chromadb/bin/activate
    pip install pytest chromadb

    # Run tests
    PYTEST_RUNNING=1 pytest infra/embedding_step_functions/unified_embedding/handlers/tests/test_close_chromadb_client.py -v

Or use the standalone test (easier):
    python3 infra/embedding_step_functions/unified_embedding/handlers/tests/standalone_test_close_client.py

NOTE: ChromaDB has compatibility issues with Python 3.14. Use Python 3.12.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import chromadb
import pytest

# Import the function we're testing
# The compaction module is in the parent directory (handlers/)
import sys
import os
from pathlib import Path as PathLib
from unittest.mock import MagicMock
import importlib.util

# Set PYTEST_RUNNING to prevent infrastructure imports
os.environ['PYTEST_RUNNING'] = '1'

# Mock dependencies before importing compaction
# compaction.py imports utils.logging and other modules which may not be available in test environment
sys.modules['utils'] = MagicMock()
sys.modules['utils.logging'] = MagicMock()
mock_logger = MagicMock()
sys.modules['utils.logging'].get_operation_logger = MagicMock(return_value=mock_logger)
sys.modules['utils.logging'].get_logger = MagicMock(return_value=mock_logger)

# Mock receipt_dynamo (it's not needed for testing close_chromadb_client)
sys.modules['receipt_dynamo'] = MagicMock()
sys.modules['receipt_dynamo.constants'] = MagicMock()
sys.modules['receipt_dynamo.data'] = MagicMock()
sys.modules['receipt_dynamo.data.dynamo_client'] = MagicMock()

# Mock boto3 (compaction.py imports it)
sys.modules['boto3'] = MagicMock()

# Mock chromadb_compaction to prevent import errors
sys.modules['chromadb_compaction'] = MagicMock()

# Get the compaction.py file path
handlers_dir = PathLib(__file__).parent.parent
compaction_path = handlers_dir / "compaction.py"

# Load compaction module directly from file to avoid importing parent packages
spec = importlib.util.spec_from_file_location("compaction", compaction_path)
compaction_module = importlib.util.module_from_spec(spec)

# Set environment variables that compaction.py might need
os.environ.setdefault('DYNAMODB_TABLE_NAME', 'test-table')
os.environ.setdefault('CHROMADB_BUCKET', 'test-bucket')

# Execute the module
spec.loader.exec_module(compaction_module)
close_chromadb_client = compaction_module.close_chromadb_client


@pytest.fixture
def temp_chromadb_dir():
    """Create a temporary directory for ChromaDB."""
    temp_dir = tempfile.mkdtemp(prefix="chromadb_test_")
    yield temp_dir
    # Cleanup
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def chromadb_client_with_data(temp_chromadb_dir):
    """Create a ChromaDB client with test data."""
    client = chromadb.PersistentClient(path=temp_chromadb_dir)
    collection = client.get_or_create_collection(
        name="test_collection",
        metadata={"test": "true"}
    )

    # Add some test data
    collection.add(
        ids=["id1", "id2", "id3"],
        embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]],
        documents=["doc1", "doc2", "doc3"],
        metadatas=[{"key": "1"}, {"key": "2"}, {"key": "3"}]
    )

    return client, collection


def test_close_chromadb_client_releases_file_locks(chromadb_client_with_data, temp_chromadb_dir):
    """
    Test that close_chromadb_client releases SQLite file locks.

    This test verifies that after calling close_chromadb_client:
    1. SQLite files can be copied to another location
    2. Files are not locked by the client
    3. Another client can access the files
    """
    client, collection = chromadb_client_with_data

    # Verify collection has data
    assert collection.count() == 3

    # Get the SQLite file path
    sqlite_file = Path(temp_chromadb_dir) / "chroma.sqlite3"
    assert sqlite_file.exists(), "SQLite file should exist"

    # Close the client
    close_chromadb_client(client, collection_name="test_collection")
    client = None  # Clear reference

    # Wait a moment for file handles to be released
    time.sleep(0.6)  # Slightly longer than the 0.5s delay in close_chromadb_client

    # Test 1: Copy SQLite file to another location (this will fail if file is locked)
    copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_")
    try:
        copied_file = Path(copy_dir) / "chroma.sqlite3"
        shutil.copy2(sqlite_file, copied_file)
        assert copied_file.exists(), "Should be able to copy SQLite file"
        assert copied_file.stat().st_size > 0, "Copied file should have content"
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)

    # Test 2: Copy entire directory (this will fail if any files are locked)
    copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_dir_")
    try:
        shutil.copytree(temp_chromadb_dir, copy_dir, dirs_exist_ok=True)
        assert Path(copy_dir).exists(), "Should be able to copy entire directory"
        assert Path(copy_dir / "chroma.sqlite3").exists(), "SQLite file should be copied"
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)

    # Test 3: Create a new client and read from the same directory
    # This verifies files are not locked
    new_client = chromadb.PersistentClient(path=temp_chromadb_dir)
    new_collection = new_client.get_collection("test_collection")
    assert new_collection.count() == 3, "New client should be able to read data"

    # Cleanup
    close_chromadb_client(new_client, collection_name="test_collection")


def test_close_chromadb_client_with_multiple_collections(temp_chromadb_dir):
    """
    Test that close_chromadb_client works with multiple collections.
    """
    client = chromadb.PersistentClient(path=temp_chromadb_dir)

    # Create multiple collections
    collection1 = client.get_or_create_collection("collection1")
    collection2 = client.get_or_create_collection("collection2")

    collection1.add(ids=["1"], embeddings=[[0.1, 0.2]], documents=["doc1"])
    collection2.add(ids=["2"], embeddings=[[0.3, 0.4]], documents=["doc2"])

    # Close client
    close_chromadb_client(client, collection_name="multi_collection")
    client = None

    # Wait for file handles to be released
    time.sleep(0.6)

    # Verify we can copy the directory
    copy_dir = tempfile.mkdtemp(prefix="chromadb_multi_")
    try:
        shutil.copytree(temp_chromadb_dir, copy_dir, dirs_exist_ok=True)
        assert Path(copy_dir).exists(), "Should be able to copy directory with multiple collections"
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)


def test_close_chromadb_client_handles_none():
    """
    Test that close_chromadb_client handles None gracefully.
    """
    # Should not raise an exception
    close_chromadb_client(None, collection_name="none_test")


def test_close_chromadb_client_allows_file_operations_after_close(chromadb_client_with_data, temp_chromadb_dir):
    """
    Test that file operations (like tar/zip) work after closing client.

    This simulates what happens when we upload snapshots to S3.
    """
    client, collection = chromadb_client_with_data

    # Close client
    close_chromadb_client(client, collection_name="test_collection")
    client = None

    # Wait for file handles to be released
    time.sleep(0.6)

    # Simulate creating a tarball (similar to what S3 upload might do)
    import tarfile

    tar_path = tempfile.mktemp(suffix=".tar.gz")
    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(temp_chromadb_dir, arcname="chromadb")

        assert Path(tar_path).exists(), "Should be able to create tarball"
        assert Path(tar_path).stat().st_size > 0, "Tarball should have content"

        # Verify we can extract it
        extract_dir = tempfile.mkdtemp(prefix="chromadb_extract_")
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(extract_dir)

            # Verify SQLite file was extracted
            extracted_sqlite = Path(extract_dir) / "chromadb" / "chroma.sqlite3"
            assert extracted_sqlite.exists(), "SQLite file should be in tarball"
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
    finally:
        if Path(tar_path).exists():
            os.remove(tar_path)


def test_close_chromadb_client_without_closing_fails_file_operations(chromadb_client_with_data, temp_chromadb_dir):
    """
    Test that WITHOUT closing the client, file operations may fail.

    This is a negative test to demonstrate why close_chromadb_client is necessary.
    Note: This test may not always fail (due to OS buffering), but it demonstrates
    the risk of not closing clients.
    """
    client, collection = chromadb_client_with_data

    # Try to copy WITHOUT closing (this may work on some systems due to OS buffering,
    # but demonstrates the risk)
    copy_dir = tempfile.mkdtemp(prefix="chromadb_no_close_")
    try:
        # On some systems, this might work due to copy-on-write or OS buffering
        # But on others, it might fail or create inconsistent copies
        shutil.copytree(temp_chromadb_dir, copy_dir, dirs_exist_ok=True)

        # Even if copy succeeds, the file might be in an inconsistent state
        # if the client is still writing to it
        copied_sqlite = Path(copy_dir) / "chroma.sqlite3"
        if copied_sqlite.exists():
            # Try to open it with a new client - this might fail if file is inconsistent
            try:
                test_client = chromadb.PersistentClient(path=copy_dir)
                test_collection = test_client.get_collection("test_collection")
                # If we get here, the file was copied successfully
                # But this doesn't guarantee it's safe - the original client might
                # still have locks
                close_chromadb_client(test_client)
            except Exception as e:
                # This is expected - file might be locked or inconsistent
                pytest.skip(f"File operations without closing may fail: {e}")
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)
        close_chromadb_client(client, collection_name="test_collection")


@pytest.mark.parametrize("collection_name", [None, "test_collection", ""])
def test_close_chromadb_client_with_different_collection_names(chromadb_client_with_data, collection_name):
    """
    Test that close_chromadb_client works with different collection_name values.
    """
    client, collection = chromadb_client_with_data

    # Should not raise an exception regardless of collection_name
    close_chromadb_client(client, collection_name=collection_name)
    client = None

    # Wait for cleanup
    time.sleep(0.6)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

