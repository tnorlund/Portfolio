"""Integration test fixtures for receipt_chroma."""

import shutil
import tempfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from receipt_chroma import ChromaClient


@pytest.fixture
def temp_chromadb_dir():
    """
    Create a temporary directory for ChromaDB integration tests.

    The directory is automatically cleaned up after the test.
    """
    temp_dir = tempfile.mkdtemp(prefix="chromadb_integration_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def chroma_client_read(temp_chromadb_dir):
    """
    Create a read-only ChromaClient for integration tests.

    The client is automatically closed after the test.
    """
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="read"
    ) as client:
        yield client


@pytest.fixture
def chroma_client_write(temp_chromadb_dir):
    """
    Create a write-enabled ChromaClient for integration tests.

    The client is automatically closed after the test.
    Uses metadata_only=True to avoid OpenAI API key requirements.
    """
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as client:
        yield client


@pytest.fixture
def chroma_client_delta(temp_chromadb_dir):
    """
    Create a delta-mode ChromaClient for integration tests.

    The client is automatically closed after the test.
    """
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="delta"
    ) as client:
        yield client


@pytest.fixture
def populated_chroma_db(chroma_client_write):
    """
    Create a ChromaDB with sample data for testing queries.

    Returns a tuple of (client, collection_name) with pre-populated data.
    """
    collection_name = "test_collection"
    client = chroma_client_write

    # Add sample documents
    client.upsert(
        collection_name=collection_name,
        ids=["doc1", "doc2", "doc3"],
        documents=[
            "This is a test document about Python programming",
            "Another document discussing ChromaDB vector storage",
            "A third document about machine learning embeddings",
        ],
        metadatas=[
            {"category": "programming", "topic": "python"},
            {"category": "database", "topic": "vector"},
            {"category": "ai", "topic": "embeddings"},
        ],
    )

    yield client, collection_name


@pytest.fixture
def s3_bucket(request):
    """
    Spins up a mock S3 instance, creates a bucket with a name from
    `request.param`, then yields the bucket name for tests.

    After the tests, everything is torn down automatically.

    Usage:
        @pytest.mark.parametrize("s3_bucket", ["test-bucket"], indirect=True)
        def test_upload(s3_bucket):
            # s3_bucket is "test-bucket"
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")

        # Pull the bucket name to create from the test's parameter
        bucket_name = request.param
        s3.create_bucket(Bucket=bucket_name)

        yield bucket_name
