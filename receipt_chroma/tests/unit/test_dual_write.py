"""Unit tests for dual-write sync_collection_to_cloud functionality.

Tests the BulkSyncResult dataclass and sync_collection_to_cloud function
using mocked cloud clients.
"""

import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from receipt_chroma import ChromaClient
from receipt_chroma.compaction.dual_write import (
    BulkSyncResult,
    CloudConfig,
    _upload_batch_with_retry,
    sync_collection_to_cloud,
)

# =============================================================================
# BulkSyncResult Tests
# =============================================================================


class TestBulkSyncResult:
    """Tests for BulkSyncResult dataclass."""

    def test_success_property_true_when_no_errors(self):
        """success property returns True when no errors and no failed batches."""
        result = BulkSyncResult(
            total_items=100,
            uploaded=100,
            failed_batches=0,
            cloud_count=100,
            error=None,
            duration_seconds=1.5,
        )
        assert result.success is True

    def test_success_property_false_when_error(self):
        """success property returns False when error is set."""
        result = BulkSyncResult(
            total_items=100,
            uploaded=0,
            failed_batches=0,
            cloud_count=0,
            error="ConnectionError: Failed to connect",
            duration_seconds=0.5,
        )
        assert result.success is False

    def test_success_property_false_when_failed_batches(self):
        """success property returns False when there are failed batches."""
        result = BulkSyncResult(
            total_items=100,
            uploaded=50,
            failed_batches=2,
            cloud_count=50,
            error=None,
            duration_seconds=2.0,
        )
        assert result.success is False

    def test_default_values(self):
        """Test default values for optional fields."""
        result = BulkSyncResult(
            total_items=10,
            uploaded=10,
            failed_batches=0,
            cloud_count=10,
        )
        assert result.error is None
        assert result.duration_seconds == 0.0
        assert result.success is True


# =============================================================================
# _upload_batch_with_retry Tests
# =============================================================================


class TestUploadBatchWithRetry:
    """Tests for _upload_batch_with_retry helper function."""

    def test_successful_upload_first_attempt(self):
        """Batch uploads successfully on first attempt."""
        mock_coll = MagicMock()
        batch = {
            "ids": ["id1", "id2"],
            "embeddings": [[0.1] * 384, [0.2] * 384],
            "metadatas": [{"text": "a"}, {"text": "b"}],
            "documents": ["doc1", "doc2"],
        }

        count = _upload_batch_with_retry(mock_coll, batch, max_retries=3)

        assert count == 2
        mock_coll.upsert.assert_called_once_with(
            ids=batch["ids"],
            embeddings=batch["embeddings"],
            metadatas=batch["metadatas"],
            documents=batch["documents"],
        )

    def test_retry_on_transient_error(self):
        """Batch retries on transient error and succeeds."""
        mock_coll = MagicMock()
        # Fail twice, then succeed
        mock_coll.upsert.side_effect = [
            Exception("Network error"),
            Exception("Timeout"),
            None,  # Success
        ]
        batch = {"ids": ["id1"], "embeddings": [[0.1] * 384]}

        with patch("receipt_chroma.compaction.dual_write.time.sleep"):
            count = _upload_batch_with_retry(mock_coll, batch, max_retries=3)

        assert count == 1
        assert mock_coll.upsert.call_count == 3

    def test_raises_after_max_retries(self):
        """Raises exception after exhausting all retries."""
        mock_coll = MagicMock()
        mock_coll.upsert.side_effect = Exception("Persistent error")
        batch = {"ids": ["id1"], "embeddings": [[0.1] * 384]}

        with patch("receipt_chroma.compaction.dual_write.time.sleep"):
            with pytest.raises(Exception, match="Persistent error"):
                _upload_batch_with_retry(mock_coll, batch, max_retries=3)

        assert mock_coll.upsert.call_count == 3


# =============================================================================
# sync_collection_to_cloud Tests
# =============================================================================


class TestSyncCollectionToCloud:
    """Tests for sync_collection_to_cloud function."""

    @pytest.fixture
    def temp_chromadb_dir(self):
        """Create a temporary directory for ChromaDB persistence."""
        with tempfile.TemporaryDirectory(prefix="chromadb_test_") as tmpdir:
            yield tmpdir

    @pytest.fixture
    def cloud_config(self):
        """Create a test CloudConfig."""
        return CloudConfig(
            api_key="test-api-key",
            tenant="test-tenant",
            database="test-database",
            enabled=True,
        )

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        logger = MagicMock()
        logger.info = MagicMock()
        logger.error = MagicMock()
        logger.debug = MagicMock()
        return logger

    def test_sync_empty_collection(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Empty collection returns success with zero counts."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            # Create empty collection
            local_client.get_collection(
                "test_collection", create_if_missing=True
            )

            result = sync_collection_to_cloud(
                local_client=local_client,
                collection_name="test_collection",
                cloud_config=cloud_config,
                logger=mock_logger,
            )

        assert result.success is True
        assert result.total_items == 0
        assert result.uploaded == 0
        assert result.failed_batches == 0
        assert result.cloud_count == 0
        assert result.error is None

    def test_sync_single_batch(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Single batch uploads correctly."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            # Add test data
            local_client.upsert(
                collection_name="test_collection",
                ids=["id1", "id2", "id3"],
                embeddings=[[0.1] * 384, [0.2] * 384, [0.3] * 384],
                metadatas=[{"text": "a"}, {"text": "b"}, {"text": "c"}],
            )

            # Mock the cloud client creation
            with patch(
                "receipt_chroma.compaction.dual_write._create_cloud_client_for_sync"
            ) as mock_create:
                mock_cloud_client = MagicMock()
                mock_cloud_client.__enter__ = MagicMock(
                    return_value=mock_cloud_client
                )
                mock_cloud_client.__exit__ = MagicMock(return_value=False)

                mock_cloud_coll = MagicMock()
                mock_cloud_coll.count.return_value = 3
                mock_cloud_client.get_collection.return_value = mock_cloud_coll

                mock_create.return_value = mock_cloud_client

                result = sync_collection_to_cloud(
                    local_client=local_client,
                    collection_name="test_collection",
                    cloud_config=cloud_config,
                    batch_size=5000,
                    logger=mock_logger,
                )

        assert result.success is True
        assert result.total_items == 3
        assert result.uploaded == 3
        assert result.failed_batches == 0
        assert result.cloud_count == 3

    def test_sync_multiple_batches_parallel(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Multiple batches upload in parallel."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            # Add enough data for multiple batches (batch_size=2 for testing)
            ids = [f"id{i}" for i in range(6)]
            embeddings = [[0.1 * i] * 384 for i in range(6)]
            metadatas = [{"idx": i} for i in range(6)]

            local_client.upsert(
                collection_name="test_collection",
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
            )

            with patch(
                "receipt_chroma.compaction.dual_write._create_cloud_client_for_sync"
            ) as mock_create:
                mock_cloud_client = MagicMock()
                mock_cloud_client.__enter__ = MagicMock(
                    return_value=mock_cloud_client
                )
                mock_cloud_client.__exit__ = MagicMock(return_value=False)

                mock_cloud_coll = MagicMock()
                mock_cloud_coll.count.return_value = 6
                mock_cloud_client.get_collection.return_value = mock_cloud_coll

                mock_create.return_value = mock_cloud_client

                result = sync_collection_to_cloud(
                    local_client=local_client,
                    collection_name="test_collection",
                    cloud_config=cloud_config,
                    batch_size=2,  # Small batch size to create multiple batches
                    max_workers=2,
                    logger=mock_logger,
                )

        assert result.success is True
        assert result.total_items == 6
        assert result.uploaded == 6
        assert result.failed_batches == 0
        # Should have made 3 upsert calls (6 items / 2 per batch)
        assert mock_cloud_coll.upsert.call_count == 3

    def test_sync_partial_failure_non_blocking(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Some batch failures don't block others."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            # Add data for 2 batches
            ids = [f"id{i}" for i in range(4)]
            embeddings = [[0.1 * i] * 384 for i in range(4)]
            metadatas = [{"idx": i} for i in range(4)]

            local_client.upsert(
                collection_name="test_collection",
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
            )

            with patch(
                "receipt_chroma.compaction.dual_write._create_cloud_client_for_sync"
            ) as mock_create:
                mock_cloud_client = MagicMock()
                mock_cloud_client.__enter__ = MagicMock(
                    return_value=mock_cloud_client
                )
                mock_cloud_client.__exit__ = MagicMock(return_value=False)

                mock_cloud_coll = MagicMock()
                # First batch succeeds, second fails
                mock_cloud_coll.upsert.side_effect = [
                    None,  # Batch 1 succeeds
                    Exception("Network error"),  # Batch 2 fails
                    Exception("Network error"),  # Retry 1
                    Exception("Network error"),  # Retry 2
                ]
                mock_cloud_coll.count.return_value = 2
                mock_cloud_client.get_collection.return_value = mock_cloud_coll

                mock_create.return_value = mock_cloud_client

                with patch("receipt_chroma.compaction.dual_write.time.sleep"):
                    result = sync_collection_to_cloud(
                        local_client=local_client,
                        collection_name="test_collection",
                        cloud_config=cloud_config,
                        batch_size=2,
                        max_workers=1,  # Sequential for predictable ordering
                        max_retries=3,
                        logger=mock_logger,
                    )

        # Partial failure - not full success
        assert result.success is False
        assert result.total_items == 4
        assert result.uploaded == 2  # Only first batch succeeded
        assert result.failed_batches == 1
        assert result.error is None  # No top-level error

    def test_sync_cloud_connection_error(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Cloud connection error returns result with error."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            local_client.upsert(
                collection_name="test_collection",
                ids=["id1"],
                embeddings=[[0.1] * 384],
                metadatas=[{"text": "a"}],
            )

            with patch(
                "receipt_chroma.compaction.dual_write._create_cloud_client_for_sync"
            ) as mock_create:
                mock_create.side_effect = Exception("Connection refused")

                result = sync_collection_to_cloud(
                    local_client=local_client,
                    collection_name="test_collection",
                    cloud_config=cloud_config,
                    logger=mock_logger,
                )

        assert result.success is False
        assert result.error is not None
        assert "Connection refused" in result.error
        assert result.total_items == 0
        assert result.uploaded == 0

    def test_sync_collection_not_found(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Collection not found returns result with error."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            # Don't create collection - try to sync non-existent collection
            result = sync_collection_to_cloud(
                local_client=local_client,
                collection_name="nonexistent_collection",
                cloud_config=cloud_config,
                logger=mock_logger,
            )

        assert result.success is False
        assert result.error is not None
        assert result.total_items == 0

    def test_sync_logs_progress(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Sync logs progress information."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            local_client.upsert(
                collection_name="test_collection",
                ids=["id1", "id2"],
                embeddings=[[0.1] * 384, [0.2] * 384],
                metadatas=[{"text": "a"}, {"text": "b"}],
            )

            with patch(
                "receipt_chroma.compaction.dual_write._create_cloud_client_for_sync"
            ) as mock_create:
                mock_cloud_client = MagicMock()
                mock_cloud_client.__enter__ = MagicMock(
                    return_value=mock_cloud_client
                )
                mock_cloud_client.__exit__ = MagicMock(return_value=False)

                mock_cloud_coll = MagicMock()
                mock_cloud_coll.count.return_value = 2
                mock_cloud_client.get_collection.return_value = mock_cloud_coll

                mock_create.return_value = mock_cloud_client

                result = sync_collection_to_cloud(
                    local_client=local_client,
                    collection_name="test_collection",
                    cloud_config=cloud_config,
                    logger=mock_logger,
                )

        # Verify logging was called
        mock_logger.info.assert_called()
        # Find the "Starting parallel cloud sync" log call
        info_calls = [
            call
            for call in mock_logger.info.call_args_list
            if "Starting parallel cloud sync" in str(call)
        ]
        assert len(info_calls) == 1

    def test_sync_duration_tracked(
        self, temp_chromadb_dir, cloud_config, mock_logger
    ):
        """Sync duration is tracked in result."""
        with ChromaClient(
            persist_directory=temp_chromadb_dir,
            mode="write",
            metadata_only=True,
        ) as local_client:
            local_client.get_collection(
                "test_collection", create_if_missing=True
            )

            result = sync_collection_to_cloud(
                local_client=local_client,
                collection_name="test_collection",
                cloud_config=cloud_config,
                logger=mock_logger,
            )

        # Duration should be tracked (even for empty collection)
        assert result.duration_seconds >= 0
