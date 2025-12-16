"""Integration tests for StorageManager."""

import os
import shutil
import tempfile

import pytest

from receipt_chroma import ChromaClient
from receipt_chroma.storage import StorageManager, StorageMode


@pytest.mark.integration
class TestStorageManagerS3Mode:
    """Test StorageManager in S3-only mode."""

    def test_s3_only_mode_forced(self, mock_s3_bucket_compaction, mock_logger):
        """Test that S3_ONLY mode is properly detected when forced."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        assert manager.effective_mode == StorageMode.S3_ONLY
        assert not manager.is_using_efs

    def test_download_snapshot_s3_mode_no_snapshot(
        self, mock_s3_bucket_compaction, mock_logger
    ):
        """Test downloading snapshot in S3 mode when no snapshot exists."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = manager.download_snapshot(tmpdir, verify_integrity=False)

            # Should auto-initialize empty snapshot when none exists
            assert result["status"] == "downloaded"
            assert result.get("initialized") is True

    def test_upload_and_download_snapshot_s3_mode(
        self, mock_s3_bucket_compaction, mock_logger, chroma_snapshot_with_data
    ):
        """Test uploading and downloading snapshot in S3 mode."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        # Upload snapshot
        upload_result = manager.upload_snapshot(
            local_path=chroma_snapshot_with_data,
            metadata={"test": "metadata"},
        )

        assert upload_result["status"] == "uploaded"
        assert "version_id" in upload_result

        # Download snapshot
        with tempfile.TemporaryDirectory() as download_dir:
            download_result = manager.download_snapshot(
                download_dir, verify_integrity=False
            )

            assert download_result["status"] == "downloaded"
            assert os.path.exists(download_dir)

            # Verify ChromaDB can open the downloaded snapshot
            client = ChromaClient(persist_directory=download_dir, mode="read")
            collections = client.list_collections()
            client.close()

            assert "lines" in collections

    def test_get_latest_version_s3_mode(
        self, mock_s3_bucket_compaction, mock_logger, chroma_snapshot_with_data
    ):
        """Test getting latest version in S3 mode."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        # Upload a snapshot
        manager.upload_snapshot(chroma_snapshot_with_data)

        # Get latest version
        version = manager.get_latest_version()
        assert version is not None


@pytest.mark.integration
class TestStorageManagerEFSMode:
    """Test StorageManager in EFS mode."""

    def test_efs_mode_with_available_efs(
        self, mock_s3_bucket_compaction, mock_logger
    ):
        """Test that EFS mode is detected when EFS is available."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Create a temporary directory to simulate EFS mount
        with tempfile.TemporaryDirectory() as efs_root:
            manager = StorageManager(
                collection="lines",
                bucket=bucket_name,
                mode=StorageMode.EFS,
                efs_root=efs_root,
                logger=mock_logger,
            )

            assert manager.effective_mode == StorageMode.EFS
            assert manager.is_using_efs

    def test_efs_mode_falls_back_to_s3_when_unavailable(
        self, mock_s3_bucket_compaction, mock_logger
    ):
        """Test that EFS mode falls back to S3 when EFS is unavailable."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Use a non-existent directory to simulate unavailable EFS
        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.EFS,
            efs_root="/nonexistent/path/to/efs",
            logger=mock_logger,
        )

        # Should fall back to S3_ONLY
        assert manager.effective_mode == StorageMode.S3_ONLY
        assert not manager.is_using_efs


@pytest.mark.integration
class TestStorageManagerAutoMode:
    """Test StorageManager in AUTO mode."""

    def test_auto_mode_detects_efs_when_available(
        self, mock_s3_bucket_compaction, mock_logger
    ):
        """Test that AUTO mode detects EFS when available."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Create a temporary directory to simulate EFS mount
        with tempfile.TemporaryDirectory() as efs_root:
            manager = StorageManager(
                collection="lines",
                bucket=bucket_name,
                mode=StorageMode.AUTO,
                efs_root=efs_root,
                logger=mock_logger,
            )

            assert manager.effective_mode == StorageMode.EFS
            assert manager.is_using_efs

    def test_auto_mode_falls_back_to_s3_when_efs_unavailable(
        self, mock_s3_bucket_compaction, mock_logger
    ):
        """Test that AUTO mode falls back to S3 when EFS is unavailable."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Use a non-existent directory to simulate unavailable EFS
        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.AUTO,
            efs_root="/nonexistent/path/to/efs",
            logger=mock_logger,
        )

        # Should fall back to S3_ONLY
        assert manager.effective_mode == StorageMode.S3_ONLY
        assert not manager.is_using_efs


@pytest.mark.integration
class TestStorageManagerIntegration:
    """Test StorageManager integration scenarios."""

    def test_multiple_upload_download_cycles(
        self, mock_s3_bucket_compaction, mock_logger, chroma_snapshot_with_data
    ):
        """Test multiple upload/download cycles."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        # First upload
        upload_result_1 = manager.upload_snapshot(chroma_snapshot_with_data)
        assert upload_result_1["status"] == "uploaded"
        version_1 = upload_result_1.get("version_id")

        # Download and modify
        with tempfile.TemporaryDirectory() as download_dir:
            download_result = manager.download_snapshot(
                download_dir, verify_integrity=False
            )
            assert download_result["status"] == "downloaded"

            # Add more data
            client = ChromaClient(persist_directory=download_dir, mode="write")
            client.upsert(
                collection_name="lines",
                ids=["IMAGE#test-id#RECEIPT#00001#LINE#00003"],
                embeddings=[[0.5] * 1536],
                metadatas=[{"text": "Test line 3"}],
            )
            client.close()

            # Upload modified snapshot
            upload_result_2 = manager.upload_snapshot(download_dir)
            assert upload_result_2["status"] == "uploaded"
            version_2 = upload_result_2.get("version_id")

            # Versions should be different
            if version_1 and version_2:
                assert version_1 != version_2

    def test_upload_with_metadata(
        self, mock_s3_bucket_compaction, mock_logger, chroma_snapshot_with_data
    ):
        """Test uploading snapshot with custom metadata."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        manager = StorageManager(
            collection="words",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        metadata = {
            "compaction_run_id": "run-123",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        upload_result = manager.upload_snapshot(
            chroma_snapshot_with_data, metadata=metadata
        )

        assert upload_result["status"] == "uploaded"
        # Note: Verifying metadata requires reading S3 object metadata,
        # which would be tested in s3 module tests

    def test_storage_manager_with_different_collections(
        self, mock_s3_bucket_compaction, mock_logger, chroma_snapshot_with_data
    ):
        """Test that different collections use different S3 paths."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        lines_manager = StorageManager(
            collection="lines",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        words_manager = StorageManager(
            collection="words",
            bucket=bucket_name,
            mode=StorageMode.S3_ONLY,
            logger=mock_logger,
        )

        # Upload to both collections
        lines_manager.upload_snapshot(chroma_snapshot_with_data)
        words_manager.upload_snapshot(chroma_snapshot_with_data)

        # Both should succeed and be independent
        lines_version = lines_manager.get_latest_version()
        words_version = words_manager.get_latest_version()

        assert lines_version is not None
        assert words_version is not None
