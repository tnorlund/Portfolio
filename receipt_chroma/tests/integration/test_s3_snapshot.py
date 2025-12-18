"""Integration tests for S3 snapshot operations using moto."""

import tempfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from receipt_chroma import ChromaClient, LockManager
from receipt_chroma.s3 import (
    download_snapshot_atomic,
    initialize_empty_snapshot,
    upload_snapshot_atomic,
)

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection


@pytest.fixture
def s3_bucket(request):
    """Create a mock S3 bucket using moto."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = request.param
        s3.create_bucket(Bucket=bucket_name)
        yield bucket_name


@pytest.fixture
def temp_chromadb_dir():
    """Create a temporary directory for ChromaDB persistence."""
    with tempfile.TemporaryDirectory(prefix="chromadb_test_") as tmpdir:
        yield tmpdir


@pytest.fixture
def dynamodb_table():
    """Create a mock DynamoDB table for LockManager."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table_name = "MyMockedTable"
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
                {"AttributeName": "GSI3PK", "AttributeType": "S"},
                {"AttributeName": "GSI3SK", "AttributeType": "S"},
                {"AttributeName": "TYPE", "AttributeType": "S"},
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI3",
                    "KeySchema": [
                        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSITYPE",
                    "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )
        dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)
        yield table_name


@pytest.mark.integration
class TestS3SnapshotOperations:
    """Test S3 snapshot atomic operations."""

    @pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
    def test_initialize_empty_snapshot(self, s3_bucket, temp_chromadb_dir):
        """Test initializing an empty snapshot."""
        result = initialize_empty_snapshot(
            bucket=s3_bucket,
            collection="test",
            local_path=temp_chromadb_dir,
        )

        assert result["status"] == "initialized"
        assert result["collection"] == "test"
        assert "version_id" in result
        assert "versioned_key" in result

        # Verify snapshot was uploaded to S3
        s3_client = boto3.client("s3", region_name="us-east-1")
        objects = s3_client.list_objects_v2(
            Bucket=s3_bucket, Prefix=result["versioned_key"]
        )
        assert "Contents" in objects
        assert len(objects["Contents"]) > 0

        # Verify pointer file exists
        pointer_key = f"test/snapshot/latest-pointer.txt"
        pointer_obj = s3_client.get_object(Bucket=s3_bucket, Key=pointer_key)
        pointer_value = pointer_obj["Body"].read().decode("utf-8").strip()
        assert pointer_value == result["version_id"]

    @pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
    def test_upload_and_download_snapshot_atomic(self, s3_bucket, temp_chromadb_dir):
        """Test atomic upload and download workflow."""
        # Create a snapshot with data
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as client:
            client.upsert(
                collection_name="test",
                ids=["1", "2"],
                documents=["doc1", "doc2"],
                metadatas=[{"key": "value1"}, {"key": "value2"}],
            )

        # Upload snapshot
        upload_result = upload_snapshot_atomic(
            local_path=temp_chromadb_dir,
            bucket=s3_bucket,
            collection="test",
        )

        assert upload_result["status"] == "uploaded"
        assert upload_result["collection"] == "test"
        assert "version_id" in upload_result

        # Download snapshot to new location
        download_dir = tempfile.mkdtemp(prefix="chromadb_download_")
        try:
            download_result = download_snapshot_atomic(
                bucket=s3_bucket,
                collection="test",
                local_path=download_dir,
            )

            assert download_result["status"] == "downloaded"
            assert download_result["version_id"] == upload_result["version_id"]

            # Verify downloaded snapshot can be opened
            with ChromaClient(persist_directory=download_dir, mode="read") as client:
                collection = client.get_collection("test")
                assert collection.count() == 2
        finally:
            import shutil

            shutil.rmtree(download_dir, ignore_errors=True)

    @pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
    def test_upload_with_lock_manager(
        self, s3_bucket, temp_chromadb_dir, dynamodb_table
    ):
        """Test upload with lock manager validation."""
        dynamo_client = DynamoClient(table_name=dynamodb_table)
        lock_manager = LockManager(
            dynamo_client=dynamo_client,
            collection=ChromaDBCollection.LINES,
        )

        # Create snapshot
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as client:
            client.upsert(
                collection_name="lines",
                ids=["1"],
                documents=["doc1"],
            )

        # Acquire lock
        assert lock_manager.acquire("test-lock")

        # Upload with lock manager
        upload_result = upload_snapshot_atomic(
            local_path=temp_chromadb_dir,
            bucket=s3_bucket,
            collection="lines",
            lock_manager=lock_manager,
        )

        assert upload_result["status"] == "uploaded"
        lock_manager.release()

    @pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
    def test_download_nonexistent_snapshot_initializes(
        self, s3_bucket, temp_chromadb_dir
    ):
        """Test that downloading nonexistent snapshot initializes empty one."""
        download_result = download_snapshot_atomic(
            bucket=s3_bucket,
            collection="new_collection",
            local_path=temp_chromadb_dir,
        )

        assert download_result["status"] == "downloaded"
        assert download_result.get("initialized") is True

        # Verify snapshot can be opened
        with ChromaClient(persist_directory=temp_chromadb_dir, mode="read") as client:
            collection = client.get_collection("new_collection")
            assert collection.count() == 0  # Empty collection

    @pytest.mark.parametrize("s3_bucket", ["test-chromadb-bucket"], indirect=True)
    def test_atomic_pointer_pattern(self, s3_bucket, temp_chromadb_dir):
        """Test that atomic pointer pattern works correctly."""
        # Upload first version
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as client:
            client.upsert(
                collection_name="test",
                ids=["1"],
                documents=["doc1"],
            )

        upload1 = upload_snapshot_atomic(
            local_path=temp_chromadb_dir,
            bucket=s3_bucket,
            collection="test",
        )
        version1 = upload1["version_id"]

        # Upload second version
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as client:
            client.upsert(
                collection_name="test",
                ids=["1", "2"],
                documents=["doc1", "doc2"],
            )

        upload2 = upload_snapshot_atomic(
            local_path=temp_chromadb_dir,
            bucket=s3_bucket,
            collection="test",
        )
        version2 = upload2["version_id"]

        # Verify pointer points to latest version
        s3_client = boto3.client("s3", region_name="us-east-1")
        pointer_key = "test/snapshot/latest-pointer.txt"
        pointer_obj = s3_client.get_object(Bucket=s3_bucket, Key=pointer_key)
        pointer_value = pointer_obj["Body"].read().decode("utf-8").strip()
        assert pointer_value == version2

        # Download should get latest version
        download_dir = tempfile.mkdtemp(prefix="chromadb_download_")
        try:
            download_result = download_snapshot_atomic(
                bucket=s3_bucket,
                collection="test",
                local_path=download_dir,
            )

            assert download_result["version_id"] == version2

            # Verify it has both documents
            with ChromaClient(persist_directory=download_dir, mode="read") as client:
                collection = client.get_collection("test")
                assert collection.count() == 2
        finally:
            import shutil

            shutil.rmtree(download_dir, ignore_errors=True)
