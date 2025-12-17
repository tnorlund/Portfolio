#!/usr/bin/env python3
"""
Standalone integration test for close_chromadb_client functionality.

This test can be run directly without pytest to avoid import issues.

Run with Python 3.12 (recommended):
    # Create venv with Python 3.12
    python3.12 -m venv .venv_test_chromadb
    source .venv_test_chromadb/bin/activate
    pip install pytest chromadb

    # Run the test
    python3 standalone_test_close_client.py

Or run directly (may have Python 3.14 compatibility issues):
    python3 standalone_test_close_client.py

NOTE: ChromaDB has compatibility issues with Python 3.14.
This test works best with Python 3.12.
"""
import sys

# Check Python version
if sys.version_info >= (3, 14):
    print(
        "⚠️  Warning: Python 3.14+ detected. ChromaDB may have compatibility issues."
    )
    print(
        "   Consider using Python 3.12: python3.12 -m venv .venv_test_chromadb"
    )
    print()

import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

# Set environment before any imports
os.environ["PYTEST_RUNNING"] = "1"

# Mock dependencies before importing compaction
from unittest.mock import MagicMock

sys.modules["utils"] = MagicMock()
sys.modules["utils.logging"] = MagicMock()
mock_logger = MagicMock()
sys.modules["utils.logging"].get_operation_logger = MagicMock(
    return_value=mock_logger
)
sys.modules["utils.logging"].get_logger = MagicMock(return_value=mock_logger)

sys.modules["receipt_dynamo"] = MagicMock()
sys.modules["receipt_dynamo.constants"] = MagicMock()
sys.modules["receipt_dynamo.data"] = MagicMock()
sys.modules["receipt_dynamo.data.dynamo_client"] = MagicMock()
sys.modules["boto3"] = MagicMock()
sys.modules["chromadb_compaction"] = MagicMock()

# Monkey-patch pydantic for chromadb compatibility with Python 3.14
# This MUST happen before chromadb is imported
chromadb_config_mock = None
try:
    import pydantic
    import pydantic_settings

    # Patch pydantic module to provide BaseSettings
    pydantic.BaseSettings = pydantic_settings.BaseSettings
    # Make sure it's in the module dict
    setattr(pydantic, "BaseSettings", pydantic_settings.BaseSettings)

    # Pre-patch chromadb.config module before it's imported if needed
    # Only inject chromadb.config temporarily, not the entire chromadb module
    import types

    # Check if chromadb.config already exists (shouldn't, but be safe)
    if "chromadb.config" not in sys.modules:
        chromadb_config_mock = types.ModuleType("chromadb.config")
        chromadb_config_mock.BaseSettings = pydantic_settings.BaseSettings
        sys.modules["chromadb.config"] = chromadb_config_mock

except ImportError:
    print("⚠️  pydantic-settings not installed. ChromaDB may fail to import.")
    print("   Install with: pip install pydantic-settings")
    # Try to continue anyway - might work if chromadb is updated

# Import chromadb (this will use the patched pydantic)
try:
    # Clean up any temporary mock entries before importing chromadb
    # so the genuine package can be loaded
    if chromadb_config_mock is not None and "chromadb.config" in sys.modules:
        # Only remove if it's our temporary mock
        if sys.modules["chromadb.config"] is chromadb_config_mock:
            del sys.modules["chromadb.config"]

    import chromadb

    # After import, check if chromadb.config lacks BaseSettings and reapply if needed
    if hasattr(chromadb, "config") and not hasattr(
        chromadb.config, "BaseSettings"
    ):
        try:
            chromadb.config.BaseSettings = pydantic_settings.BaseSettings
        except NameError:
            # pydantic_settings not available, skip
            pass
except Exception as e:
    # Clean up temporary mock in exception path
    if chromadb_config_mock is not None and "chromadb.config" in sys.modules:
        if sys.modules["chromadb.config"] is chromadb_config_mock:
            del sys.modules["chromadb.config"]
    print(f"❌ Failed to import chromadb: {e}")
    print("   This is likely due to Python 3.14 compatibility issues.")
    print("   Try running with Python 3.12 or install pydantic-settings.")
    print(f"   Error details: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

# Load compaction module directly
import importlib.util

handlers_dir = Path(__file__).parent.parent
compaction_path = handlers_dir / "compaction.py"

os.environ.setdefault("DYNAMODB_TABLE_NAME", "test-table")
os.environ.setdefault("CHROMADB_BUCKET", "test-bucket")

spec = importlib.util.spec_from_file_location("compaction", compaction_path)
compaction_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compaction_module)
close_chromadb_client = compaction_module.close_chromadb_client


def test_close_releases_file_locks():
    """Test that close_chromadb_client releases SQLite file locks."""
    print("=" * 80)
    print("Test 1: File locks are released after close_chromadb_client")
    print("=" * 80)

    # Create temporary ChromaDB
    temp_dir = tempfile.mkdtemp(prefix="chromadb_test_")
    try:
        client = chromadb.PersistentClient(path=temp_dir)
        collection = client.get_or_create_collection(
            name="test_collection", metadata={"test": "true"}
        )

        # Add test data
        collection.add(
            ids=["id1", "id2", "id3"],
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]],
            documents=["doc1", "doc2", "doc3"],
            metadatas=[{"key": "1"}, {"key": "2"}, {"key": "3"}],
        )

        assert collection.count() == 3, "Collection should have 3 items"
        print("✅ Created ChromaDB collection with 3 items")

        sqlite_file = Path(temp_dir) / "chroma.sqlite3"
        assert sqlite_file.exists(), "SQLite file should exist"
        print(f"✅ SQLite file exists: {sqlite_file}")

        # Close the client
        print("\n🔒 Closing ChromaDB client...")
        close_chromadb_client(client, collection_name="test_collection")
        client = None

        # Wait for file handles to be released
        print("⏳ Waiting for file handles to be released...")
        time.sleep(0.6)

        # Test 1: Copy SQLite file
        print("\n📋 Test 1.1: Copying SQLite file...")
        copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_")
        try:
            copied_file = Path(copy_dir) / "chroma.sqlite3"
            shutil.copy2(sqlite_file, copied_file)
            assert copied_file.exists(), "Should be able to copy SQLite file"
            assert (
                copied_file.stat().st_size > 0
            ), "Copied file should have content"
            print("✅ Successfully copied SQLite file")
        finally:
            shutil.rmtree(copy_dir, ignore_errors=True)

        # Test 2: Copy entire directory
        print("\n📋 Test 1.2: Copying entire directory...")
        copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_dir_")
        try:
            shutil.copytree(temp_dir, copy_dir, dirs_exist_ok=True)
            copy_path = Path(copy_dir)
            assert (
                copy_path.exists()
            ), "Should be able to copy entire directory"
            assert (
                copy_path / "chroma.sqlite3"
            ).exists(), "SQLite file should be copied"
            print("✅ Successfully copied entire directory")
        finally:
            shutil.rmtree(copy_dir, ignore_errors=True)

        # Test 3: Create tarball (simulates S3 upload)
        print("\n📋 Test 1.3: Creating tarball (simulates S3 upload)...")
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        tar_path = temp_file.name
        temp_file.close()
        try:
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(temp_dir, arcname="chromadb")

            assert Path(tar_path).exists(), "Should be able to create tarball"
            assert (
                Path(tar_path).stat().st_size > 0
            ), "Tarball should have content"
            print(
                f"✅ Successfully created tarball ({Path(tar_path).stat().st_size} bytes)"
            )

            # Verify we can extract it
            extract_dir = tempfile.mkdtemp(prefix="chromadb_extract_")
            try:
                with tarfile.open(tar_path, "r:gz") as tar:
                    tar.extractall(extract_dir)

                extracted_sqlite = (
                    Path(extract_dir) / "chromadb" / "chroma.sqlite3"
                )
                assert (
                    extracted_sqlite.exists()
                ), "SQLite file should be in tarball"
                print("✅ Successfully extracted tarball")
            finally:
                shutil.rmtree(extract_dir, ignore_errors=True)
        finally:
            if Path(tar_path).exists():
                os.remove(tar_path)

        # Test 4: New client can read from same directory
        print("\n📋 Test 1.4: New client reading from same directory...")
        new_client = chromadb.PersistentClient(path=temp_dir)
        new_collection = new_client.get_collection("test_collection")
        assert (
            new_collection.count() == 3
        ), "New client should be able to read data"
        print("✅ New client successfully read data")
        close_chromadb_client(new_client, collection_name="test_collection")

        print("\n✅ All tests passed!")
        return True

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_close_handles_none():
    """Test that close_chromadb_client handles None gracefully."""
    print("\n" + "=" * 80)
    print("Test 2: close_chromadb_client handles None")
    print("=" * 80)

    try:
        close_chromadb_client(None, collection_name="none_test")
        print("✅ Successfully handled None client")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Integration Test: close_chromadb_client")
    print("=" * 80 + "\n")

    results = []
    results.append(test_close_releases_file_locks())
    results.append(test_close_handles_none())

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    passed = sum(results)
    total = len(results)
    print(f"Tests passed: {passed}/{total}")

    if passed == total:
        print("✅ All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        sys.exit(1)
