"""Unit tests for ChromaDB S3 compaction pipeline."""

import pytest
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, call
from pathlib import Path
import json
import uuid

from receipt_label.utils.chroma_compactor import ChromaCompactor
from receipt_label.utils.chroma_s3_helpers import (
    produce_embedding_delta, 
    consume_latest_snapshot,
    upload_delta_to_s3,
    download_snapshot_from_s3
)
from receipt_label.tests.markers import unit, fast, chroma, aws
from receipt_dynamo.entities import CompactionLock


@unit
@fast
@chroma 
@aws
class TestChromaS3Compaction:
    """Test ChromaDB S3 compaction pipeline components."""

    @pytest.fixture
    def mock_s3_client(self):
        """Mock S3 client for testing."""
        client = Mock()
        
        # Mock successful uploads
        client.upload_fileobj.return_value = None
        client.upload_file.return_value = None
        
        # Mock successful downloads
        client.download_fileobj.return_value = None
        client.download_file.return_value = None
        
        # Mock list operations
        client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "delta/uuid-1/chroma.sqlite3", "LastModified": datetime.now(timezone.utc)},
                {"Key": "delta/uuid-2/chroma.sqlite3", "LastModified": datetime.now(timezone.utc)},
                {"Key": "snapshot/2023-01-01T00:00:00Z/chroma.sqlite3", "LastModified": datetime.now(timezone.utc)},
                {"Key": "snapshot/latest/chroma.sqlite3", "LastModified": datetime.now(timezone.utc)},
            ]
        }
        
        # Mock head operations
        client.head_object.return_value = {
            "LastModified": datetime.now(timezone.utc),
            "ContentLength": 1024000  # 1MB
        }
        
        return client

    @pytest.fixture
    def mock_dynamo_client(self):
        """Mock DynamoDB client for compaction locks."""
        client = Mock()
        
        # Mock successful lock operations
        client.put_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        client.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        client.update_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        
        # Mock get_item for lock status
        client.get_item.return_value = {
            "Item": {
                "lock_id": {"S": "test-lock"},
                "owner": {"S": "test-owner"},
                "expires": {"S": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}
            }
        }
        
        return client

    @pytest.fixture
    def temp_dir(self):
        """Temporary directory for testing."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def sample_embedding_data(self):
        """Sample embedding data for testing."""
        return {
            "ids": ["emb_1", "emb_2", "emb_3"],
            "embeddings": [
                [0.1, 0.2, 0.3] * 512,
                [0.4, 0.5, 0.6] * 512,
                [0.7, 0.8, 0.9] * 512
            ],
            "documents": ["document 1", "document 2", "document 3"],
            "metadatas": [
                {"label": "MERCHANT_NAME", "confidence": 0.9},
                {"label": "CURRENCY", "confidence": 0.8},
                {"label": "DATE", "confidence": 0.7}
            ]
        }

    def test_compactor_initialization(self, mock_dynamo_client):
        """Test ChromaCompactor initializes correctly."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket",
            table_name="test-locks"
        )
        
        assert compactor._dynamo_client == mock_dynamo_client
        assert compactor._bucket == "test-bucket"
        assert compactor._table_name == "test-locks"
        assert compactor._lock_timeout_minutes == 15  # Default

    def test_compaction_lock_acquisition(self, mock_dynamo_client):
        """Test distributed lock acquisition for compaction."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        lock_id = "test-compaction-lock"
        owner = str(uuid.uuid4())
        
        # Test successful lock acquisition
        success = compactor._acquire_lock(lock_id, owner)
        assert success is True
        
        # Should have called put_item with condition
        mock_dynamo_client.put_item.assert_called_once()
        call_args = mock_dynamo_client.put_item.call_args
        assert call_args.kwargs["Item"]["lock_id"]["S"] == lock_id
        assert call_args.kwargs["Item"]["owner"]["S"] == owner
        assert "ConditionExpression" in call_args.kwargs

    def test_compaction_lock_release(self, mock_dynamo_client):
        """Test distributed lock release."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        lock_id = "test-compaction-lock"
        owner = str(uuid.uuid4())
        
        # Test lock release
        compactor._release_lock(lock_id, owner)
        
        # Should have called delete_item
        mock_dynamo_client.delete_item.assert_called_once()
        call_args = mock_dynamo_client.delete_item.call_args
        assert call_args.kwargs["Key"]["lock_id"]["S"] == lock_id

    def test_compaction_lock_busy_scenario(self, mock_dynamo_client):
        """Test behavior when compaction lock is busy."""
        # Mock lock acquisition failure
        from botocore.exceptions import ClientError
        mock_dynamo_client.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Lock exists"}},
            "PutItem"
        )
        
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        lock_id = "busy-lock"
        owner = str(uuid.uuid4())
        
        # Should return False when lock is busy
        success = compactor._acquire_lock(lock_id, owner)
        assert success is False

    def test_produce_embedding_delta(self, mock_s3_client, temp_dir, sample_embedding_data):
        """Test producing embedding deltas to S3."""
        bucket = "test-embeddings"
        delta_prefix = "delta/"
        
        with patch('boto3.client', return_value=mock_s3_client):
            result = produce_embedding_delta(
                ids=sample_embedding_data["ids"],
                embeddings=sample_embedding_data["embeddings"],
                documents=sample_embedding_data["documents"],
                metadatas=sample_embedding_data["metadatas"],
                bucket=bucket,
                delta_prefix=delta_prefix,
                local_temp_dir=temp_dir
            )
        
        # Should return delta information
        assert "delta_key" in result
        assert result["delta_key"].startswith(delta_prefix)
        assert "item_count" in result
        assert result["item_count"] == 3
        assert "delta_size_bytes" in result
        
        # Should have uploaded to S3
        mock_s3_client.upload_fileobj.assert_called_once()

    def test_consume_latest_snapshot(self, mock_s3_client, temp_dir):
        """Test consuming latest snapshot from S3."""
        bucket = "test-embeddings"
        snapshot_prefix = "snapshot/"
        local_mount_dir = temp_dir
        
        with patch('boto3.client', return_value=mock_s3_client):
            result = consume_latest_snapshot(
                bucket=bucket,
                snapshot_prefix=snapshot_prefix,
                local_mount_dir=local_mount_dir
            )
        
        # Should return snapshot information
        assert "snapshot_path" in result
        assert result["snapshot_path"] == local_mount_dir
        assert "snapshot_key" in result
        
        # Should have downloaded from S3
        mock_s3_client.list_objects_v2.assert_called()
        mock_s3_client.download_fileobj.assert_called()

    def test_delta_compaction_workflow(self, mock_s3_client, mock_dynamo_client, temp_dir):
        """Test complete delta compaction workflow."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        delta_keys = [
            "delta/uuid-1/",
            "delta/uuid-2/",
            "delta/uuid-3/"
        ]
        
        # Mock the compaction steps
        with patch('boto3.client', return_value=mock_s3_client):
            with patch.object(compactor, '_download_current_snapshot', return_value=temp_dir) as mock_download:
                with patch.object(compactor, '_merge_deltas', return_value={"merged_count": 150}) as mock_merge:
                    with patch.object(compactor, '_upload_new_snapshot', return_value={"snapshot_key": "snapshot/2023-01-01T12:00:00Z/"}) as mock_upload:
                        
                        result = compactor.compact_deltas(delta_keys)
        
        # Should complete successfully
        assert result["status"] == "completed"
        assert result["deltas_processed"] == 3
        assert "snapshot_key" in result
        assert result["merged_count"] == 150
        
        # Should have acquired and released lock
        mock_dynamo_client.put_item.assert_called()  # Lock acquire
        mock_dynamo_client.delete_item.assert_called()  # Lock release
        
        # Should have called compaction steps
        mock_download.assert_called_once()
        mock_merge.assert_called_once_with(delta_keys, temp_dir)
        mock_upload.assert_called_once()

    def test_compaction_failure_recovery(self, mock_s3_client, mock_dynamo_client, temp_dir):
        """Test compaction failure recovery and cleanup."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        delta_keys = ["delta/uuid-fail/"]
        
        # Mock failure in merge step
        with patch('boto3.client', return_value=mock_s3_client):
            with patch.object(compactor, '_download_current_snapshot', return_value=temp_dir):
                with patch.object(compactor, '_merge_deltas', side_effect=Exception("Merge failed")):
                    
                    result = compactor.compact_deltas(delta_keys)
        
        # Should handle failure gracefully
        assert result["status"] == "failed"
        assert "error" in result
        assert "Merge failed" in result["error"]
        
        # Should still release lock even on failure
        mock_dynamo_client.delete_item.assert_called()

    def test_concurrent_compaction_prevention(self, mock_dynamo_client):
        """Test prevention of concurrent compactions."""
        # Mock lock already exists
        from botocore.exceptions import ClientError
        mock_dynamo_client.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem"
        )
        
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        result = compactor.compact_deltas(["delta/test/"])
        
        # Should skip when lock is busy
        assert result["status"] == "skipped"
        assert "lock_busy" in result["reason"]

    def test_delta_upload_with_metadata(self, mock_s3_client, temp_dir):
        """Test delta upload with comprehensive metadata."""
        bucket = "test-bucket"
        delta_key = "delta/test-uuid/"
        
        # Create test delta files
        delta_dir = Path(temp_dir) / "test-delta"
        delta_dir.mkdir()
        
        # Create mock ChromaDB files
        (delta_dir / "chroma.sqlite3").write_bytes(b"mock chroma db")
        (delta_dir / "index").mkdir()
        (delta_dir / "index" / "uuid.bin").write_bytes(b"mock index")
        
        with patch('boto3.client', return_value=mock_s3_client):
            result = upload_delta_to_s3(
                local_delta_path=str(delta_dir),
                bucket=bucket,
                delta_key=delta_key,
                metadata={
                    "item_count": 42,
                    "producer_id": "test-lambda",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )
        
        # Should return upload result
        assert result["status"] == "uploaded"
        assert result["delta_key"] == delta_key
        assert result["file_count"] >= 2  # At least db and index files
        
        # Should have uploaded multiple files
        assert mock_s3_client.upload_file.call_count >= 2

    def test_snapshot_download_with_verification(self, mock_s3_client, temp_dir):
        """Test snapshot download with integrity verification."""
        bucket = "test-bucket"
        snapshot_key = "snapshot/2023-01-01T12:00:00Z/"
        
        # Mock S3 responses for snapshot files
        mock_s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": f"{snapshot_key}chroma.sqlite3", "Size": 1024000},
                {"Key": f"{snapshot_key}index/uuid.bin", "Size": 512000},
            ]
        }
        
        with patch('boto3.client', return_value=mock_s3_client):
            result = download_snapshot_from_s3(
                bucket=bucket,
                snapshot_key=snapshot_key,
                local_snapshot_path=temp_dir,
                verify_integrity=True
            )
        
        # Should return download result
        assert result["status"] == "downloaded"
        assert result["snapshot_key"] == snapshot_key
        assert result["local_path"] == temp_dir
        assert result["file_count"] == 2
        
        # Should have downloaded all files
        assert mock_s3_client.download_file.call_count == 2

    def test_compaction_metrics_tracking(self, mock_s3_client, mock_dynamo_client, temp_dir):
        """Test compaction metrics and performance tracking."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        delta_keys = ["delta/perf-test-1/", "delta/perf-test-2/"]
        
        with patch('boto3.client', return_value=mock_s3_client):
            with patch.object(compactor, '_download_current_snapshot', return_value=temp_dir):
                with patch.object(compactor, '_merge_deltas') as mock_merge:
                    with patch.object(compactor, '_upload_new_snapshot') as mock_upload:
                        
                        # Mock performance data
                        mock_merge.return_value = {
                            "merged_count": 500,
                            "merge_time_seconds": 12.5,
                            "duplicates_resolved": 25
                        }
                        mock_upload.return_value = {
                            "snapshot_key": "snapshot/2023-01-01T12:00:00Z/",
                            "upload_time_seconds": 8.3,
                            "compressed_size_bytes": 2048000
                        }
                        
                        result = compactor.compact_deltas(delta_keys)
        
        # Should include performance metrics
        assert "performance_metrics" in result
        metrics = result["performance_metrics"]
        assert "total_time_seconds" in metrics
        assert "merge_time_seconds" in metrics
        assert "upload_time_seconds" in metrics
        assert metrics["merged_count"] == 500
        assert metrics["duplicates_resolved"] == 25

    def test_s3_error_handling(self, mock_s3_client, temp_dir, sample_embedding_data):
        """Test handling of S3 errors during delta operations."""
        # Mock S3 upload failure
        from botocore.exceptions import ClientError
        mock_s3_client.upload_fileobj.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "Bucket does not exist"}},
            "UploadFileObj"
        )
        
        bucket = "nonexistent-bucket"
        
        with patch('boto3.client', return_value=mock_s3_client):
            try:
                result = produce_embedding_delta(
                    ids=sample_embedding_data["ids"],
                    embeddings=sample_embedding_data["embeddings"],
                    documents=sample_embedding_data["documents"],
                    metadatas=sample_embedding_data["metadatas"],
                    bucket=bucket,
                    delta_prefix="delta/",
                    local_temp_dir=temp_dir
                )
                
                # Should handle error gracefully
                assert result["status"] == "failed"
                assert "error" in result
                assert "NoSuchBucket" in result["error"]
                
            except Exception as e:
                pytest.fail(f"Should handle S3 errors gracefully, got: {e}")

    def test_lock_timeout_handling(self, mock_dynamo_client):
        """Test handling of lock timeouts during long compactions."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket",
            lock_timeout_minutes=1  # Short timeout for testing
        )
        
        # Mock long-running compaction
        def slow_merge(delta_keys, snapshot_path):
            import time
            time.sleep(2)  # Longer than lock timeout
            return {"merged_count": 100}
        
        with patch.object(compactor, '_download_current_snapshot', return_value="/tmp"):
            with patch.object(compactor, '_merge_deltas', side_effect=slow_merge):
                with patch.object(compactor, '_upload_new_snapshot', return_value={"snapshot_key": "test"}):
                    
                    result = compactor.compact_deltas(["delta/slow/"])
        
        # Should handle timeout scenario
        # Implementation might extend lock or handle gracefully
        assert "status" in result

    def test_delta_size_optimization(self, mock_s3_client, temp_dir, sample_embedding_data):
        """Test delta size optimization and compression."""
        bucket = "test-bucket"
        
        # Test with large dataset to trigger compression
        large_data = {
            "ids": [f"large_id_{i}" for i in range(1000)],
            "embeddings": [[i * 0.001] * 1536 for i in range(1000)],
            "documents": [f"large document {i}" for i in range(1000)],
            "metadatas": [{"index": i, "large": True} for i in range(1000)]
        }
        
        with patch('boto3.client', return_value=mock_s3_client):
            result = produce_embedding_delta(
                ids=large_data["ids"],
                embeddings=large_data["embeddings"],
                documents=large_data["documents"],
                metadatas=large_data["metadatas"],
                bucket=bucket,
                delta_prefix="delta/",
                local_temp_dir=temp_dir,
                compress=True  # Enable compression if supported
            )
        
        # Should handle large datasets efficiently
        assert result["item_count"] == 1000
        assert "delta_size_bytes" in result
        
        # If compression is enabled, should report compression ratio
        if "compression_ratio" in result:
            assert 0 < result["compression_ratio"] <= 1.0

    def test_snapshot_versioning_and_cleanup(self, mock_s3_client, mock_dynamo_client):
        """Test snapshot versioning and old snapshot cleanup."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket",
            keep_snapshots=3  # Keep only 3 recent snapshots
        )
        
        # Mock multiple existing snapshots
        mock_s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "snapshot/2023-01-01T00:00:00Z/chroma.sqlite3", "LastModified": datetime(2023, 1, 1)},
                {"Key": "snapshot/2023-01-02T00:00:00Z/chroma.sqlite3", "LastModified": datetime(2023, 1, 2)},
                {"Key": "snapshot/2023-01-03T00:00:00Z/chroma.sqlite3", "LastModified": datetime(2023, 1, 3)},
                {"Key": "snapshot/2023-01-04T00:00:00Z/chroma.sqlite3", "LastModified": datetime(2023, 1, 4)},
                {"Key": "snapshot/2023-01-05T00:00:00Z/chroma.sqlite3", "LastModified": datetime(2023, 1, 5)},
            ]
        }
        
        with patch.object(compactor, '_cleanup_old_snapshots') as mock_cleanup:
            with patch.object(compactor, '_download_current_snapshot', return_value="/tmp"):
                with patch.object(compactor, '_merge_deltas', return_value={"merged_count": 100}):
                    with patch.object(compactor, '_upload_new_snapshot', return_value={"snapshot_key": "snapshot/2023-01-06T00:00:00Z/"}):
                        
                        result = compactor.compact_deltas(["delta/test/"])
        
        # Should trigger cleanup of old snapshots
        mock_cleanup.assert_called_once()
        assert result["status"] == "completed"

    def test_multi_region_s3_support(self, mock_s3_client, temp_dir, sample_embedding_data):
        """Test S3 operations across different regions."""
        regions = ["us-east-1", "eu-west-1", "ap-southeast-1"]
        
        for region in regions:
            with patch('boto3.client') as mock_boto:
                mock_boto.return_value = mock_s3_client
                
                result = produce_embedding_delta(
                    ids=sample_embedding_data["ids"][:1],  # Small batch
                    embeddings=sample_embedding_data["embeddings"][:1],
                    documents=sample_embedding_data["documents"][:1],
                    metadatas=sample_embedding_data["metadatas"][:1],
                    bucket=f"test-bucket-{region}",
                    delta_prefix="delta/",
                    local_temp_dir=temp_dir,
                    region=region
                )
                
                # Should create client for specified region
                mock_boto.assert_called_with('s3', region_name=region)
                assert result["item_count"] == 1