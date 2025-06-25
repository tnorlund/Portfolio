"""Unit tests for ClientManager integration with AIUsageTracker."""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest
from openai import OpenAI
from pinecone import Index, Pinecone
from receipt_dynamo import DynamoClient

from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.client_manager import ClientConfig, ClientManager

from receipt_label.tests.utils.ai_usage_helpers import create_mock_openai_response


@pytest.mark.unit
class TestClientConfigIntegration:
    """Test ClientConfig functionality."""

    def test_client_config_initialization(self):
        """Test ClientConfig initialization with all parameters."""
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-openai-key",
            pinecone_api_key="test-pinecone-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host.pinecone.io",
            track_usage=True,
            user_id="test-user",
        )

        assert config.dynamo_table == "test-table"
        assert config.openai_api_key == "test-openai-key"
        assert config.pinecone_api_key == "test-pinecone-key"
        assert config.pinecone_index_name == "test-index"
        assert config.pinecone_host == "test-host.pinecone.io"
        assert config.track_usage is True
        assert config.user_id == "test-user"

    def test_client_config_from_env(self):
        """Test ClientConfig loading from environment variables."""
        env_vars = {
            "DYNAMO_TABLE_NAME": "env-table",
            "OPENAI_API_KEY": "env-openai-key",
            "PINECONE_API_KEY": "env-pinecone-key",
            "PINECONE_INDEX_NAME": "env-index",
            "PINECONE_HOST": "env-host.pinecone.io",
            "TRACK_AI_USAGE": "true",
            "USER_ID": "env-user",
        }

        with patch.dict(os.environ, env_vars):
            config = ClientConfig.from_env()

        assert config.dynamo_table == "env-table"
        assert config.openai_api_key == "env-openai-key"
        assert config.pinecone_api_key == "env-pinecone-key"
        assert config.pinecone_index_name == "env-index"
        assert config.pinecone_host == "env-host.pinecone.io"
        assert config.track_usage is True
        assert config.user_id == "env-user"

    def test_client_config_track_usage_false(self):
        """Test ClientConfig with track_usage disabled."""
        env_vars = {
            "DYNAMO_TABLE_NAME": "table",
            "OPENAI_API_KEY": "key",
            "PINECONE_API_KEY": "key",
            "PINECONE_INDEX_NAME": "index",
            "PINECONE_HOST": "host",
            "TRACK_AI_USAGE": "false",
        }

        with patch.dict(os.environ, env_vars):
            config = ClientConfig.from_env()

        assert config.track_usage is False

    def test_client_config_missing_user_id(self):
        """Test ClientConfig with missing USER_ID."""
        env_vars = {
            "DYNAMO_TABLE_NAME": "table",
            "OPENAI_API_KEY": "key",
            "PINECONE_API_KEY": "key",
            "PINECONE_INDEX_NAME": "index",
            "PINECONE_HOST": "host",
        }

        with patch.dict(os.environ, env_vars):
            config = ClientConfig.from_env()

        assert config.user_id is None


@pytest.mark.unit
class TestClientManagerBasics:
    """Test basic ClientManager functionality."""

    def test_client_manager_initialization(self):
        """Test ClientManager initialization."""
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
        )

        manager = ClientManager(config)

        assert manager.config == config
        assert manager._dynamo_client is None
        assert manager._openai_client is None
        assert manager._pinecone_index is None
        assert manager._usage_tracker is None

    @patch("receipt_label.utils.client_manager.DynamoClient")
    def test_dynamo_client_lazy_initialization(self, mock_dynamo_class):
        """Test lazy initialization of DynamoDB client."""
        mock_dynamo_instance = Mock()
        mock_dynamo_class.return_value = mock_dynamo_instance

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
        )

        manager = ClientManager(config)

        # First access should create client
        client1 = manager.dynamo
        assert client1 == mock_dynamo_instance
        mock_dynamo_class.assert_called_once_with("test-table")

        # Second access should return same instance
        client2 = manager.dynamo
        assert client2 == client1
        assert mock_dynamo_class.call_count == 1  # Not called again

    @patch("receipt_label.utils.client_manager.Pinecone")
    def test_pinecone_client_lazy_initialization(self, mock_pinecone_class):
        """Test lazy initialization of Pinecone client."""
        mock_pc_instance = Mock()
        mock_index = Mock()
        mock_pc_instance.Index.return_value = mock_index
        mock_pinecone_class.return_value = mock_pc_instance

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-pinecone-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host.pinecone.io",
        )

        manager = ClientManager(config)

        # First access should create index
        index1 = manager.pinecone
        assert index1 == mock_index
        mock_pinecone_class.assert_called_once_with(api_key="test-pinecone-key")
        mock_pc_instance.Index.assert_called_once_with(
            "test-index", host="test-host.pinecone.io"
        )

        # Second access should return same instance
        index2 = manager.pinecone
        assert index2 == index1
        assert mock_pinecone_class.call_count == 1


@pytest.mark.unit
class TestUsageTrackerIntegration:
    """Test AIUsageTracker integration with ClientManager."""

    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.AIUsageTracker")
    def test_usage_tracker_creation(self, mock_tracker_class, mock_dynamo_class):
        """Test usage tracker creation with tracking enabled."""
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_tracker = Mock()
        mock_tracker_class.return_value = mock_tracker

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=True,
            user_id="test-user",
        )

        manager = ClientManager(config)

        # Access tracker
        tracker = manager.usage_tracker
        assert tracker == mock_tracker

        # Verify tracker was created with correct parameters
        mock_tracker_class.assert_called_once_with(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            user_id="test-user",
            track_to_dynamo=True,
            track_to_file=False,
        )

    def test_usage_tracker_disabled(self):
        """Test that usage tracker is None when tracking is disabled."""
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=False,
        )

        manager = ClientManager(config)

        assert manager.usage_tracker is None

    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.AIUsageTracker")
    def test_usage_tracker_with_file_logging(
        self, mock_tracker_class, mock_dynamo_class
    ):
        """Test usage tracker with file logging enabled."""
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_tracker = Mock()
        mock_tracker_class.return_value = mock_tracker

        with patch.dict(os.environ, {"TRACK_TO_FILE": "true"}):
            config = ClientConfig(
                dynamo_table="test-table",
                openai_api_key="test-key",
                pinecone_api_key="test-key",
                pinecone_index_name="test-index",
                pinecone_host="test-host",
                track_usage=True,
            )

            manager = ClientManager(config)
            tracker = manager.usage_tracker

        # Verify file tracking was enabled
        mock_tracker_class.assert_called_once()
        call_kwargs = mock_tracker_class.call_args.kwargs
        assert call_kwargs["track_to_file"] is True


@pytest.mark.unit
class TestOpenAIClientIntegration:
    """Test OpenAI client integration with usage tracking."""

    @patch("receipt_label.utils.client_manager.OpenAI")
    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.AIUsageTracker")
    def test_openai_client_with_tracking(
        self, mock_tracker_class, mock_dynamo_class, mock_openai_class
    ):
        """Test OpenAI client creation with usage tracking."""
        # Setup mocks
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_tracker = Mock()
        mock_tracker_class.return_value = mock_tracker

        mock_openai = Mock(spec=OpenAI)
        mock_openai_class.return_value = mock_openai

        mock_wrapped_client = Mock()
        mock_tracker_class.create_wrapped_openai_client.return_value = (
            mock_wrapped_client
        )

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-openai-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=True,
            user_id="test-user",
        )

        manager = ClientManager(config)

        # Access OpenAI client
        client = manager.openai

        # Verify OpenAI client was created
        mock_openai_class.assert_called_once_with(api_key="test-openai-key")

        # Verify it was wrapped for tracking
        mock_tracker_class.create_wrapped_openai_client.assert_called_once_with(
            mock_openai, mock_tracker
        )

        # Should return wrapped client
        assert client == mock_wrapped_client

    @patch("receipt_label.utils.client_manager.OpenAI")
    def test_openai_client_without_tracking(self, mock_openai_class):
        """Test OpenAI client creation without usage tracking."""
        mock_openai = Mock(spec=OpenAI)
        mock_openai_class.return_value = mock_openai

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-openai-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=False,  # Tracking disabled
        )

        manager = ClientManager(config)
        client = manager.openai

        # Should return unwrapped client
        assert client == mock_openai
        mock_openai_class.assert_called_once_with(api_key="test-openai-key")

    @patch("receipt_label.utils.client_manager.OpenAI")
    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.AIUsageTracker")
    def test_openai_client_reuse(
        self, mock_tracker_class, mock_dynamo_class, mock_openai_class
    ):
        """Test that OpenAI client is reused on multiple accesses."""
        mock_openai = Mock(spec=OpenAI)
        mock_openai_class.return_value = mock_openai

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=False,
        )

        manager = ClientManager(config)

        # Multiple accesses
        client1 = manager.openai
        client2 = manager.openai
        client3 = manager.openai

        # Should all be same instance
        assert client1 == client2 == client3
        assert mock_openai_class.call_count == 1


@pytest.mark.unit
class TestClientManagerMethods:
    """Test ClientManager methods."""

    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.OpenAI")
    @patch("receipt_label.utils.client_manager.Pinecone")
    def test_get_all_clients(
        self, mock_pinecone_class, mock_openai_class, mock_dynamo_class
    ):
        """Test get_all_clients method."""
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_openai = Mock()
        mock_openai_class.return_value = mock_openai

        mock_pc = Mock()
        mock_index = Mock()
        mock_pc.Index.return_value = mock_index
        mock_pinecone_class.return_value = mock_pc

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=False,
        )

        manager = ClientManager(config)

        # Get all clients
        dynamo, openai, pinecone = manager.get_all_clients()

        assert dynamo == mock_dynamo
        assert openai == mock_openai
        assert pinecone == mock_index

    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.AIUsageTracker")
    def test_set_tracking_context(self, mock_tracker_class, mock_dynamo_class):
        """Test set_tracking_context method."""
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_tracker = Mock()
        mock_tracker_class.return_value = mock_tracker

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=True,
        )

        manager = ClientManager(config)

        # Set context
        manager.set_tracking_context(
            job_id="job-123", batch_id="batch-456", user_id="new-user"
        )

        # Verify tracker's set_context was called
        mock_tracker.set_context.assert_called_once_with(
            job_id="job-123", batch_id="batch-456", user_id="new-user"
        )

    def test_set_tracking_context_no_tracker(self):
        """Test set_tracking_context when tracking is disabled."""
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=False,
        )

        manager = ClientManager(config)

        # Should not raise error
        manager.set_tracking_context(job_id="job-123")

        # Tracker should remain None
        assert manager._usage_tracker is None


@pytest.mark.unit
class TestEndToEndTracking:
    """Test end-to-end tracking scenarios."""

    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.OpenAI")
    def test_full_tracking_flow(self, mock_openai_class, mock_dynamo_class):
        """Test complete flow of client creation and usage tracking."""
        # Setup mock DynamoDB
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        # Setup mock OpenAI client
        mock_openai = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_openai.chat = mock_chat
        mock_chat.completions = mock_completions

        mock_response = create_mock_openai_response(
            prompt_tokens=100, completion_tokens=50, model="gpt-3.5-turbo"
        )
        mock_completions.create.return_value = mock_response

        mock_openai_class.return_value = mock_openai

        # Create config with tracking enabled
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=True,
            user_id="test-user",
        )

        # Create manager and set context
        manager = ClientManager(config)
        manager.set_tracking_context(job_id="job-123", batch_id="batch-456")

        # Get wrapped OpenAI client
        client = manager.openai

        # Make API call through wrapped client
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )

        # Verify response
        assert response == mock_response

        # Verify tracking occurred with correct context
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "openai"
        assert item["model"]["S"] == "gpt-3.5-turbo"
        assert item["jobId"]["S"] == "job-123"
        assert item["batchId"]["S"] == "batch-456"
        assert item["userId"]["S"] == "test-user"
        assert item["inputTokens"]["N"] == "100"
        assert item["outputTokens"]["N"] == "50"

    @patch.dict(
        os.environ,
        {
            "DYNAMO_TABLE_NAME": "env-table",
            "OPENAI_API_KEY": "env-key",
            "PINECONE_API_KEY": "env-key",
            "PINECONE_INDEX_NAME": "env-index",
            "PINECONE_HOST": "env-host",
            "TRACK_AI_USAGE": "true",
            "USER_ID": "env-user",
            "TRACK_TO_FILE": "true",
        },
    )
    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.OpenAI")
    def test_env_based_configuration(self, mock_openai_class, mock_dynamo_class):
        """Test complete flow with environment-based configuration."""
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_openai = Mock(spec=OpenAI)
        mock_openai_class.return_value = mock_openai

        # Create manager from environment
        config = ClientConfig.from_env()
        manager = ClientManager(config)

        # Verify configuration
        assert manager.config.dynamo_table == "env-table"
        assert manager.config.user_id == "env-user"
        assert manager.config.track_usage is True

        # Access tracker to trigger creation
        tracker = manager.usage_tracker
        assert tracker is not None

        # Verify DynamoDB client was created with env table
        mock_dynamo_class.assert_called_with("env-table")


@pytest.mark.unit
class TestErrorHandling:
    """Test error handling in ClientManager."""

    def test_missing_required_config(self):
        """Test handling of missing required configuration."""
        with pytest.raises(KeyError):
            with patch.dict(os.environ, {}, clear=True):
                ClientConfig.from_env()

    @patch("receipt_label.utils.client_manager.DynamoClient")
    def test_dynamo_client_creation_failure(self, mock_dynamo_class):
        """Test handling of DynamoDB client creation failure."""
        mock_dynamo_class.side_effect = Exception("DynamoDB connection failed")

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
        )

        manager = ClientManager(config)

        # Should raise exception when accessing client
        with pytest.raises(Exception, match="DynamoDB connection failed"):
            manager.dynamo

    @patch("receipt_label.utils.client_manager.OpenAI")
    def test_openai_client_creation_failure(self, mock_openai_class):
        """Test handling of OpenAI client creation failure."""
        mock_openai_class.side_effect = Exception("Invalid API key")

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="invalid-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
        )

        manager = ClientManager(config)

        # Should raise exception when accessing client
        with pytest.raises(Exception, match="Invalid API key"):
            manager.openai

    @patch("receipt_label.utils.client_manager.DynamoClient")
    @patch("receipt_label.utils.client_manager.AIUsageTracker")
    def test_tracker_creation_failure(self, mock_tracker_class, mock_dynamo_class):
        """Test handling of tracker creation failure."""
        mock_dynamo = Mock()
        mock_dynamo_class.return_value = mock_dynamo

        mock_tracker_class.side_effect = Exception("Tracker initialization failed")

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test-host",
            track_usage=True,
        )

        manager = ClientManager(config)

        # Should raise exception when accessing tracker
        with pytest.raises(Exception, match="Tracker initialization failed"):
            manager.usage_tracker