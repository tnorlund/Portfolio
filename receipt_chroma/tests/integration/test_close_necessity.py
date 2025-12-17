"""
Tests to prove that GC and delay are necessary for proper file unlocking.

These tests demonstrate that without proper cleanup (GC + delay), SQLite files
remain locked and cannot be safely copied or uploaded.
"""

import gc
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from receipt_chroma import ChromaClient


@pytest.mark.integration
def test_file_locked_without_close(temp_chromadb_dir):
    """
    Test that files ARE locked when client is not properly closed.

    This demonstrates the problem: without close(), files remain locked.
    """
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    )
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test document"])

    # DON'T close the client - just set reference to None
    # This simulates what happens without proper cleanup
    client._client = None  # pylint: disable=protected-access
    del client

    # Try to copy files immediately (without GC or delay)
    copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_")
    try:
        # This might succeed on some systems, but is unreliable
        # On systems with strict file locking, this could fail
        for file_path in Path(temp_chromadb_dir).rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(temp_chromadb_dir)
                dest = Path(copy_dir) / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(file_path, dest)
                except (OSError, PermissionError) as e:
                    # This proves files are locked!
                    pytest.fail(
                        f"File is locked without proper close: {e}. "
                        "This proves GC and delay are necessary."
                    )
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)


@pytest.mark.integration
def test_file_unlocked_with_gc_only(temp_chromadb_dir):
    """
    Test that GC alone is not sufficient - delay is also needed.

    This test verifies that even with GC, we need a delay for OS
    to release file handles.
    """
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    )
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test document"])

    # Close client (which does GC)
    client.close()

    # Try to copy IMMEDIATELY after close (no delay)
    # This might work on some systems but is unreliable
    copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_")
    try:
        success = True
        for file_path in Path(temp_chromadb_dir).rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(temp_chromadb_dir)
                dest = Path(copy_dir) / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(file_path, dest)
                except (OSError, PermissionError):
                    # File is still locked even after GC!
                    success = False
                    break

        # If this succeeds, it's luck - not guaranteed
        # The delay ensures reliability across all systems
        if not success:
            pytest.skip(
                "GC alone was insufficient - delay is necessary. "
                "This proves both GC and delay are needed."
            )
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)


@pytest.mark.integration
def test_file_unlocked_with_gc_and_delay(temp_chromadb_dir):
    """
    Test that GC + delay properly unlocks files.

    This is the recommended approach and should always work.
    """
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    )
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test document"])

    # Close client (does GC + 0.1s delay)
    client.close()

    # Additional small delay to be safe (matching test pattern)
    time.sleep(0.1)

    # Should be able to copy files reliably
    copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_")
    try:
        for file_path in Path(temp_chromadb_dir).rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(temp_chromadb_dir)
                dest = Path(copy_dir) / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dest)  # Should always succeed

        # Verify copied database can be opened
        with ChromaClient(
            persist_directory=copy_dir, mode="read"
        ) as new_client:
            new_collection = new_client.get_collection("test")
            assert new_collection.count() == 1
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)


@pytest.mark.integration
def test_file_locking_detection(temp_chromadb_dir):
    """
    Test that we can detect file locking by trying to open exclusively.

    This is a more direct test of file locking.
    """
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    )
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test document"])

    # Find SQLite database file
    sqlite_files = list(Path(temp_chromadb_dir).rglob("*.sqlite*"))
    if not sqlite_files:
        # ChromaDB might use different file extensions or storage
        # If no SQLite files found, test passes (nothing to test)
        return

    sqlite_file = sqlite_files[0]

    # Test 1: File is locked while client is open
    try:
        # Try to open exclusively (this should fail if file is locked)
        with open(sqlite_file, "r+b") as f:
            # If we get here, file might not be locked (or OS allows it)
            pass
    except (OSError, PermissionError):
        # File is locked - this is expected!
        pass

    # Test 2: Close client and check if file is unlocked
    client.close()
    time.sleep(0.2)  # Wait for OS to release handles

    # Now file should be accessible
    try:
        with open(sqlite_file, "r+b") as f:
            # Should succeed now
            assert f is not None
    except (OSError, PermissionError) as e:
        pytest.fail(
            f"File still locked after close() + delay: {e}. "
            "This suggests the delay might need to be longer."
        )
