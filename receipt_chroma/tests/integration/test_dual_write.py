"""Integration tests for dual-write functionality.

Tests the apply_collection_updates() function which handles writing
to both local ChromaDB snapshots and Chroma Cloud.
"""

from unittest.mock import MagicMock, patch

import pytest

from receipt_chroma import ChromaClient
from receipt_chroma.compaction import (
    CloudConfig,
    DualWriteResult,
    apply_collection_updates,
)
from receipt_dynamo.constants import ChromaDBCollection


class MockStreamMessage:
    """Mock StreamMessage for testing."""

    def __init__(
        self,
        entity_type: str = "RECEIPT_PLACE",
        event_name: str = "MODIFY",
        record_id: str = "test-record-id",
    ):
        self.entity_type = entity_type
        self.event_name = event_name
        self.context = MagicMock()
        self.context.record_id = record_id


class MockLogger:
    """Mock logger for testing."""

    def __init__(self):
        self.messages = []

    def info(self, msg, **kwargs):
        self.messages.append(("info", msg, kwargs))

    def error(self, msg, **kwargs):
        self.messages.append(("error", msg, kwargs))

    def warning(self, msg, **kwargs):
        self.messages.append(("warning", msg, kwargs))

    def debug(self, msg, **kwargs):
        self.messages.append(("debug", msg, kwargs))


# =============================================================================
# CloudConfig Tests
# =============================================================================


@pytest.mark.integration
def test_cloud_config_from_env_disabled():
    """Test CloudConfig.from_env returns None when disabled."""
    env = {"CHROMA_CLOUD_ENABLED": "false"}
    config = CloudConfig.from_env(env)
    assert config is None


@pytest.mark.integration
def test_cloud_config_from_env_enabled_no_api_key():
    """Test CloudConfig.from_env returns None when enabled but no API key."""
    env = {"CHROMA_CLOUD_ENABLED": "true"}
    config = CloudConfig.from_env(env)
    assert config is None


@pytest.mark.integration
def test_cloud_config_from_env_enabled_with_api_key():
    """Test CloudConfig.from_env returns config when enabled with API key."""
    env = {
        "CHROMA_CLOUD_ENABLED": "true",
        "CHROMA_CLOUD_API_KEY": "test-api-key",
        "CHROMA_CLOUD_TENANT": "test-tenant",
        "CHROMA_CLOUD_DATABASE": "test-database",
    }
    config = CloudConfig.from_env(env)

    assert config is not None
    assert config.api_key == "test-api-key"
    assert config.tenant == "test-tenant"
    assert config.database == "test-database"
    assert config.enabled is True


@pytest.mark.integration
def test_cloud_config_from_env_defaults():
    """Test CloudConfig.from_env uses defaults for tenant/database."""
    env = {
        "CHROMA_CLOUD_ENABLED": "true",
        "CHROMA_CLOUD_API_KEY": "test-api-key",
    }
    config = CloudConfig.from_env(env)

    assert config is not None
    assert config.tenant is None  # Will default to "default" in CloudClient
    assert config.database is None  # Will default to "default" in CloudClient


@pytest.mark.integration
def test_cloud_config_from_env_empty_api_key():
    """Test CloudConfig.from_env handles empty API key."""
    env = {
        "CHROMA_CLOUD_ENABLED": "true",
        "CHROMA_CLOUD_API_KEY": "   ",  # Whitespace only
    }
    config = CloudConfig.from_env(env)
    assert config is None


# =============================================================================
# apply_collection_updates Tests - Local Only
# =============================================================================


@pytest.mark.integration
def test_apply_collection_updates_local_only(temp_chromadb_dir):
    """Test apply_collection_updates with cloud disabled."""
    mock_logger = MockLogger()
    mock_metrics = MagicMock()

    # Create local client with test data
    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as local_client:
        # Pre-populate with some data
        local_client.upsert(
            collection_name="lines",
            ids=["test-id-1"],
            embeddings=[[0.1] * 384],
            metadatas=[{"text": "test"}],
        )

        # Call apply_collection_updates with no cloud config
        result = apply_collection_updates(
            stream_messages=[],  # Empty messages - just testing the flow
            collection=ChromaDBCollection.LINES,
            local_client=local_client,
            cloud_config=None,  # Cloud disabled
            op_logger=mock_logger,
            metrics=mock_metrics,
            dynamo_client=None,
        )

    # Verify result structure
    assert isinstance(result, DualWriteResult)
    assert result.local_result is not None
    assert result.cloud_result is None
    assert result.cloud_error is None
    assert result.cloud_enabled is False
    assert result.cloud_success is False


@pytest.mark.integration
def test_apply_collection_updates_metrics_tracked(temp_chromadb_dir):
    """Test that metrics are tracked correctly."""
    mock_logger = MockLogger()
    mock_metrics = MagicMock()

    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as local_client:
        local_client.upsert(
            collection_name="lines",
            ids=["test-id-1"],
            embeddings=[[0.1] * 384],
            metadatas=[{"text": "test"}],
        )

        result = apply_collection_updates(
            stream_messages=[],
            collection=ChromaDBCollection.LINES,
            local_client=local_client,
            cloud_config=None,
            op_logger=mock_logger,
            metrics=mock_metrics,
            dynamo_client=None,
        )

    # Verify result structure
    assert result.local_result is not None
    assert result.cloud_enabled is False

    # Verify dual-write status metric was tracked
    mock_metrics.count.assert_called()
    # Find the CompactionDualWriteStatus call using call.args attribute
    dual_write_calls = [
        call
        for call in mock_metrics.count.call_args_list
        if call.args and call.args[0] == "CompactionDualWriteStatus"
    ]
    assert len(dual_write_calls) == 1
    matched_call = dual_write_calls[0]
    # Dimensions are in the third positional arg
    dimensions = matched_call.args[2] if len(matched_call.args) > 2 else {}
    assert dimensions.get("cloud_enabled") == "false"


# =============================================================================
# apply_collection_updates Tests - Dual Write with Mocked Cloud
# =============================================================================


@pytest.mark.integration
def test_apply_collection_updates_dual_write_success(temp_chromadb_dir):
    """Test apply_collection_updates with successful cloud write."""
    mock_logger = MockLogger()
    mock_metrics = MagicMock()

    cloud_config = CloudConfig(
        api_key="test-api-key",
        tenant="test-tenant",
        database="test-database",
        enabled=True,
    )

    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as local_client:
        local_client.upsert(
            collection_name="lines",
            ids=["test-id-1"],
            embeddings=[[0.1] * 384],
            metadatas=[{"text": "test"}],
        )

        # Mock the cloud client creation
        with patch(
            "receipt_chroma.compaction.dual_write._create_cloud_client"
        ) as mock_create_cloud:
            mock_cloud_client = MagicMock()
            mock_cloud_client.__enter__ = MagicMock(
                return_value=mock_cloud_client
            )
            mock_cloud_client.__exit__ = MagicMock(return_value=False)
            mock_create_cloud.return_value = mock_cloud_client

            # Mock process_collection_updates for cloud to return a result
            with patch(
                "receipt_chroma.compaction.dual_write.process_collection_updates"
            ) as mock_process:
                # First call is for local, second is for cloud
                from receipt_chroma.compaction.models import (
                    CollectionUpdateResult,
                )

                mock_result = CollectionUpdateResult(
                    collection=ChromaDBCollection.LINES,
                    metadata_updates=[],
                    label_updates=[],
                    delta_merge_count=0,
                    delta_merge_results=[],
                )
                mock_process.return_value = mock_result

                result = apply_collection_updates(
                    stream_messages=[],
                    collection=ChromaDBCollection.LINES,
                    local_client=local_client,
                    cloud_config=cloud_config,
                    op_logger=mock_logger,
                    metrics=mock_metrics,
                    dynamo_client=None,
                )

    # Verify result
    assert result.cloud_enabled is True
    assert result.cloud_result is not None
    assert result.cloud_error is None
    assert result.cloud_success is True

    # Verify cloud client was created with correct config
    mock_create_cloud.assert_called_once_with(
        cloud_config, ChromaDBCollection.LINES
    )


@pytest.mark.integration
def test_apply_collection_updates_cloud_failure_non_blocking(
    temp_chromadb_dir,
):
    """Test that cloud failure doesn't block local success."""
    mock_logger = MockLogger()
    mock_metrics = MagicMock()

    cloud_config = CloudConfig(
        api_key="test-api-key",
        tenant="test-tenant",
        database="test-database",
        enabled=True,
    )

    with ChromaClient(
        persist_directory=temp_chromadb_dir, mode="write", metadata_only=True
    ) as local_client:
        local_client.upsert(
            collection_name="lines",
            ids=["test-id-1"],
            embeddings=[[0.1] * 384],
            metadatas=[{"text": "test"}],
        )

        # Mock the cloud client to raise an exception
        with patch(
            "receipt_chroma.compaction.dual_write._create_cloud_client"
        ) as mock_create_cloud:
            mock_create_cloud.side_effect = Exception(
                "Cloud connection failed"
            )

            result = apply_collection_updates(
                stream_messages=[],
                collection=ChromaDBCollection.LINES,
                local_client=local_client,
                cloud_config=cloud_config,
                op_logger=mock_logger,
                metrics=mock_metrics,
                dynamo_client=None,
            )

    # Local should succeed despite cloud failure
    assert result.local_result is not None
    assert result.cloud_enabled is True
    assert result.cloud_result is None
    # Error format is "{type(e).__name__}: {e}" per dual_write.py
    assert "Cloud connection failed" in result.cloud_error
    assert result.cloud_success is False

    # Verify error metric was tracked
    error_calls = [
        call
        for call in mock_metrics.count.call_args_list
        if call[0][0] == "CompactionCloudError"
    ]
    assert len(error_calls) == 1


# =============================================================================
# DualWriteResult Tests
# =============================================================================


@pytest.mark.integration
def test_dual_write_result_to_dict():
    """Test DualWriteResult.to_dict() serialization."""
    from receipt_chroma.compaction.models import CollectionUpdateResult

    local_result = CollectionUpdateResult(
        collection=ChromaDBCollection.LINES,
        metadata_updates=[],
        label_updates=[],
        delta_merge_count=5,
        delta_merge_results=[],
    )

    result = DualWriteResult(
        local_result=local_result,
        cloud_result=None,
        cloud_error="Test error",
        cloud_enabled=True,
    )

    result_dict = result.to_dict()

    assert "local_result" in result_dict
    assert result_dict["cloud_enabled"] is True
    assert result_dict["cloud_success"] is False
    assert result_dict["cloud_error"] == "Test error"


@pytest.mark.integration
def test_dual_write_result_has_errors_local():
    """Test DualWriteResult.has_errors when local has errors."""
    from receipt_chroma.compaction.models import (
        CollectionUpdateResult,
        MetadataUpdateResult,
    )

    # Create local result with an error
    local_result = CollectionUpdateResult(
        collection=ChromaDBCollection.LINES,
        metadata_updates=[
            MetadataUpdateResult(
                database="test",
                collection="lines",
                updated_count=0,
                image_id="test-img",
                receipt_id=1,
                error="Update failed",
            )
        ],
        label_updates=[],
        delta_merge_count=0,
        delta_merge_results=[],
    )

    result = DualWriteResult(
        local_result=local_result,
        cloud_result=None,
        cloud_error=None,
        cloud_enabled=False,
    )

    assert result.has_errors is True


@pytest.mark.integration
def test_dual_write_result_has_errors_cloud():
    """Test DualWriteResult.has_errors when cloud has errors."""
    from receipt_chroma.compaction.models import (
        CollectionUpdateResult,
        MetadataUpdateResult,
    )

    local_result = CollectionUpdateResult(
        collection=ChromaDBCollection.LINES,
        metadata_updates=[],
        label_updates=[],
        delta_merge_count=0,
        delta_merge_results=[],
    )

    # Cloud result with error
    cloud_result = CollectionUpdateResult(
        collection=ChromaDBCollection.LINES,
        metadata_updates=[
            MetadataUpdateResult(
                database="test",
                collection="lines",
                updated_count=0,
                image_id="test-img",
                receipt_id=1,
                error="Cloud update failed",
            )
        ],
        label_updates=[],
        delta_merge_count=0,
        delta_merge_results=[],
    )

    result = DualWriteResult(
        local_result=local_result,
        cloud_result=cloud_result,
        cloud_error=None,
        cloud_enabled=True,
    )

    assert result.has_errors is True
