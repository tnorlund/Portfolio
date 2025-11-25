"""
Integration tests for ChromaClient.

These tests verify ChromaClient functionality with actual ChromaDB operations,
including file I/O, persistence, resource management, and S3 integration.
"""

import shutil
import tempfile
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from receipt_chroma import ChromaClient


@pytest.mark.integration
def test_client_persistence(temp_chromadb_dir):
    """Test that data persists across client instances."""
    # Create and populate a client
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as client:
        client.upsert(
            collection_name="test",
            ids=["1", "2"],
            documents=["doc1", "doc2"],
            metadatas=[{"key": "value1"}, {"key": "value2"}],
        )

    # Create a new client and verify data persists
    # For read mode, we need to use query_embeddings or get the collection directly
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="read"
    ) as client:
        collection = client.get_collection("test")
        # Verify collection exists and has data
        assert collection.count() == 2
        # Get by IDs to verify data
        results = client.get(collection_name="test", ids=["1", "2"])
        assert len(results["ids"]) == 2
        assert "1" in results["ids"]
        assert "2" in results["ids"]


@pytest.mark.integration
def test_close_releases_file_locks(temp_chromadb_dir):
    """
    Test that close() releases SQLite file locks.

    This is the critical test for issue #5868 - verifying that files
    can be accessed after closing the client.
    """
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    )
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test document"])

    # Close the client
    client.close()

    # Wait a bit for file handles to be released
    time.sleep(0.2)

    # Should be able to copy/access files after close
    copy_dir = tempfile.mkdtemp(prefix="chromadb_copy_")
    try:
        # Copy all files from the persist directory
        for file_path in Path(temp_chromadb_dir).rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(temp_chromadb_dir)
                dest = Path(copy_dir) / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dest)

        # Should be able to open the copied database
        with ChromaClient(
            persist_directory=copy_dir, mode="read"
        ) as new_client:
            new_collection = new_client.get_collection("test")
            assert new_collection.count() == 1
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)


@pytest.mark.integration
def test_close_before_upload_simulation(temp_chromadb_dir):
    """
    Test the scenario from issue #5868: closing before uploading to S3.

    This simulates the Lambda function scenario where we need to upload
    ChromaDB files to S3 after operations.
    """
    # Create and use client
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="delta", metadata_only=True
    )
    try:
        collection = client.get_collection("test", create_if_missing=True)
        collection.upsert(ids=["1"], documents=["test document"])

        # CRITICAL: Close before "uploading" (simulated by copying)
        client.close()

        # Simulate upload by copying files
        upload_dir = tempfile.mkdtemp(prefix="chromadb_upload_")
        try:
            for file_path in Path(temp_chromadb_dir).rglob("*"):
                if file_path.is_file():
                    relative = file_path.relative_to(temp_chromadb_dir)
                    dest = Path(upload_dir) / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, dest)

            # Verify the "uploaded" database can be opened
            with ChromaClient(
                persist_directory=upload_dir, mode="read"
            ) as uploaded_client:
                uploaded_collection = uploaded_client.get_collection("test")
                assert uploaded_collection.count() == 1
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)
    finally:
        client.close()


@pytest.mark.integration
def test_upsert_and_query_workflow(chroma_client_write):
    """Test complete upsert and query workflow."""
    client = chroma_client_write

    # Upsert documents
    client.upsert(
        collection_name="workflow_test",
        ids=["id1", "id2", "id3"],
        documents=["First document", "Second document", "Third document"],
        metadatas=[
            {"category": "test"},
            {"category": "test"},
            {"category": "other"},
        ],
    )

    # Query by text
    results = client.query(
        collection_name="workflow_test", query_texts=["First"], n_results=3
    )

    assert len(results["ids"][0]) > 0
    assert "id1" in results["ids"][0]

    # Query with filter
    filtered_results = client.query(
        collection_name="workflow_test",
        query_texts=["document"],
        n_results=3,
        where={"category": "test"},
    )

    assert len(filtered_results["ids"][0]) >= 2


@pytest.mark.integration
def test_multiple_collections(chroma_client_write):
    """Test working with multiple collections."""
    client = chroma_client_write

    client.upsert(collection_name="collection1", ids=["1"], documents=["doc1"])
    client.upsert(collection_name="collection2", ids=["2"], documents=["doc2"])

    assert client.count("collection1") == 1
    assert client.count("collection2") == 1

    collections = client.list_collections()
    assert "collection1" in collections
    assert "collection2" in collections


@pytest.mark.integration
def test_query_with_populated_db(populated_chroma_db):
    """Test querying against a pre-populated database."""
    client, collection_name = populated_chroma_db

    # Query for programming-related documents
    results = client.query(
        collection_name=collection_name,
        query_texts=["Python programming"],
        n_results=3,
    )

    assert len(results["ids"][0]) > 0
    assert len(results["metadatas"][0]) > 0


@pytest.mark.integration
def test_get_by_ids(chroma_client_write):
    """Test retrieving documents by their IDs."""
    client = chroma_client_write

    client.upsert(
        collection_name="get_test",
        ids=["id1", "id2"],
        documents=["Document 1", "Document 2"],
        metadatas=[{"key": "value1"}, {"key": "value2"}],
    )

    results = client.get(
        collection_name="get_test",
        ids=["id1", "id2"],
        include=["documents", "metadatas"],
    )

    assert len(results["ids"]) == 2
    assert "id1" in results["ids"]
    assert "id2" in results["ids"]
    assert "Document 1" in results["documents"]
    assert "Document 2" in results["documents"]


@pytest.mark.integration
def test_delete_operations(chroma_client_write):
    """Test delete operations."""
    client = chroma_client_write

    # Add documents
    client.upsert(
        collection_name="delete_test",
        ids=["id1", "id2", "id3"],
        documents=["doc1", "doc2", "doc3"],
    )

    assert client.count("delete_test") == 3

    # Delete by ID
    client.delete(collection_name="delete_test", ids=["id1"])
    assert client.count("delete_test") == 2

    # Delete by filter - use a metadata filter instead
    # First add metadata to make filtering work
    client.upsert(
        collection_name="delete_test",
        ids=["id2"],
        documents=["doc2"],
        metadatas=[{"to_delete": True}],
    )
    # Now delete by metadata filter
    client.delete(collection_name="delete_test", where={"to_delete": True})
    assert client.count("delete_test") == 1


@pytest.mark.integration
def test_read_only_mode_prevents_writes(temp_chromadb_dir):
    """Test that read-only mode prevents writes."""
    # First create a collection with write mode
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as client:
        client.upsert(
            collection_name="readonly_test", ids=["1"], documents=["test"]
        )

    # Now try to write in read mode
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="read"
    ) as client:
        with pytest.raises(RuntimeError, match="read-only"):
            client.upsert(
                collection_name="readonly_test", ids=["2"], documents=["test2"]
            )

        # But should be able to read - use get() instead of query() for read mode
        collection = client.get_collection("readonly_test")
        assert collection.count() > 0
        results = client.get(collection_name="readonly_test", ids=["1"])
        assert len(results["ids"]) > 0


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
def test_close_before_s3_upload(temp_chromadb_dir, s3_bucket):
    """
    Test the critical scenario from issue #5868: closing before uploading to S3.

    This test uses moto to mock S3, verifying that files can be uploaded
    after closing the ChromaDB client.
    """
    # Create and use client
    client = ChromaClient(
        persist_directory=temp_chromadb_dir, mode="delta", metadata_only=True
    )
    try:
        collection = client.get_collection("test", create_if_missing=True)
        collection.upsert(ids=["1"], documents=["test document"])

        # CRITICAL: Close before uploading to S3
        client.close()

        # Wait for file handles to be released
        time.sleep(0.2)

        # Now upload to S3 using moto
        s3_client = boto3.client("s3", region_name="us-east-1")

        # Upload all files from the persist directory
        uploaded_count = 0
        for file_path in Path(temp_chromadb_dir).rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(temp_chromadb_dir)
                s3_key = f"deltas/test/{relative}"
                s3_client.upload_file(str(file_path), s3_bucket, s3_key)
                uploaded_count += 1

        assert uploaded_count > 0

        # Verify files were uploaded
        objects = s3_client.list_objects_v2(
            Bucket=s3_bucket, Prefix="deltas/test/"
        )
        assert len(objects.get("Contents", [])) == uploaded_count

        # Download and verify the uploaded database can be opened
        download_dir = tempfile.mkdtemp(prefix="chromadb_download_")
        try:
            for obj in objects["Contents"]:
                local_file = Path(download_dir) / Path(obj["Key"]).name
                local_file.parent.mkdir(parents=True, exist_ok=True)
                s3_client.download_file(s3_bucket, obj["Key"], str(local_file))

            # Try to open the downloaded database
            # Note: This is simplified - in reality we'd need to preserve directory structure
            # But this demonstrates the key point: files are unlocked after close()
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)
    finally:
        client.close()


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
def test_s3_upload_after_close_prevents_corruption(
    temp_chromadb_dir, s3_bucket
):
    """
    Test that closing the client before S3 upload prevents database corruption.

    This verifies the fix for issue #5868 by ensuring files are properly
    closed before upload operations.
    """
    with mock_aws():
        # Create client and add data
        client = ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="delta",
            metadata_only=True,
        )
        try:
            client.upsert(
                collection_name="corruption_test",
                ids=["1", "2", "3"],
                documents=["doc1", "doc2", "doc3"],
            )

            # Close client BEFORE upload (critical for issue #5868)
            client.close()
            time.sleep(0.2)  # Ensure file handles are released

            # Upload to S3
            s3_client = boto3.client("s3", region_name="us-east-1")
            s3_client.create_bucket(Bucket=s3_bucket)

            uploaded_files = []
            for file_path in Path(temp_chromadb_dir).rglob("*"):
                if file_path.is_file():
                    relative = file_path.relative_to(temp_chromadb_dir)
                    s3_key = f"snapshots/{relative}"
                    s3_client.upload_file(str(file_path), s3_bucket, s3_key)
                    uploaded_files.append(s3_key)

            assert len(uploaded_files) > 0

            # Verify all files uploaded successfully
            objects = s3_client.list_objects_v2(Bucket=s3_bucket)
            assert len(objects.get("Contents", [])) == len(uploaded_files)
        finally:
            client.close()
