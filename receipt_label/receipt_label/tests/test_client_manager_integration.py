"""
Integration tests for ClientManager and AI usage tracking.

Tests the complete flow of ClientManager initialization, service client creation,
AI usage tracking, cost calculation, and metric storage.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.client_manager import ClientConfig, ClientManager
from receipt_label.utils.cost_calculator import AICostCalculator

sys.path.append(os.path.join(os.path.dirname(__file__), "../../.."))

from tests.utils.ai_usage_helpers import (
    assert_usage_metric_equal,
    create_mock_anthropic_response,
    create_mock_openai_response,
    create_test_tracking_context,
)


def get_dynamo_call_count(mock_dynamo):
    """Helper to get the actual call count from mock DynamoDB client."""
    # The tracker will use put_ai_usage_metric if available, else put_item
    if (
        hasattr(mock_dynamo, "put_ai_usage_metric")
        and mock_dynamo.put_ai_usage_metric.called
    ):
        return mock_dynamo.put_ai_usage_metric.call_count
    else:
        return mock_dynamo.put_item.call_count


def get_dynamo_call_args(mock_dynamo):
    """Helper to get the actual call args from mock DynamoDB client."""
    # The tracker will use put_ai_usage_metric if available, else put_item
    if (
        hasattr(mock_dynamo, "put_ai_usage_metric")
        and mock_dynamo.put_ai_usage_metric.called
    ):
        # For put_ai_usage_metric, the metric object is passed directly
        # We need to convert it to the same format as put_item calls
        calls = []
        for call in mock_dynamo.put_ai_usage_metric.call_args_list:
            metric = call.args[0]  # First argument is the metric
            item = metric.to_dynamodb_item()
            # Simulate the put_item call format
            calls.append(type("MockCall", (), {"kwargs": {"Item": item}})())
        return calls
    else:
        return mock_dynamo.put_item.call_args_list


@pytest.fixture
def mock_env():
    """Mock environment variables for testing."""
    env_vars = {
        "DYNAMODB_TABLE_NAME": "test-table",
        "OPENAI_API_KEY": "test-openai-key",
        "PINECONE_API_KEY": "test-pinecone-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
        "TRACK_AI_USAGE": "true",
        "USER_ID": "test-user",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def mock_dynamo_client():
    """Mock DynamoDB client for testing."""
    client = MagicMock(spec=DynamoClient)
    client.table_name = "test-table"
    client.put_item = MagicMock(
        return_value={"ResponseMetadata": {"HTTPStatusCode": 200}}
    )

    def store_metric(metric):
        """Store AIUsageMetric for new resilient client interface."""
        item = metric.to_dynamodb_item()
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    client.put_ai_usage_metric = MagicMock(side_effect=store_metric)
    client.query = MagicMock(return_value={"Items": []})
    return client


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for testing."""
    client = MagicMock(spec=OpenAI)

    # Mock chat completions
    client.chat = MagicMock()
    client.chat.completions = MagicMock()

    # Mock embeddings
    client.embeddings = MagicMock()

    return client


@pytest.fixture
def mock_pinecone_index():
    """Mock Pinecone index for testing."""
    index = MagicMock()
    index.upsert = MagicMock()
    index.query = MagicMock(return_value={"matches": []})
    return index


@pytest.mark.integration
class TestClientManagerIntegration:
    """Test ClientManager integration with AI usage tracking."""

    def test_client_manager_initialization_with_tracking(
        self, mock_env, mock_dynamo_client
    ):
        """Test ClientManager initialization with usage tracking enabled."""
        config = ClientConfig.from_env()

        assert config.track_usage is True
        assert config.user_id == "test-user"
        assert config.dynamo_table == "test-table"

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            manager = ClientManager(config)

            # Verify lazy initialization
            assert manager._dynamo_client is None
            assert manager._openai_client is None
            assert manager._usage_tracker is None

            # Access dynamo client
            dynamo = manager.dynamo
            assert dynamo == mock_dynamo_client

            # Access usage tracker
            tracker = manager.usage_tracker
            assert tracker is not None
            assert isinstance(tracker, AIUsageTracker)
            assert tracker.user_id == "test-user"
            assert tracker.track_to_dynamo is True

    def test_client_manager_initialization_without_tracking(
        self, mock_dynamo_client
    ):
        """Test ClientManager initialization with usage tracking disabled."""
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test.pinecone.io",
            track_usage=False,
        )

        manager = ClientManager(config)

        # Verify tracking is disabled
        assert manager.usage_tracker is None

    @pytest.mark.integration
    def test_openai_client_wrapping_with_tracking(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test OpenAI client wrapping with usage tracking."""
        config = ClientConfig.from_env()

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                # Get wrapped OpenAI client
                openai_client = manager.openai

                # Verify client is wrapped (it's a custom wrapped class)
                assert (
                    openai_client is not mock_openai_client
                )  # Should be wrapped
                assert hasattr(
                    openai_client, "chat"
                )  # Should have expected methods

    @pytest.mark.integration
    def test_complete_openai_completion_tracking_flow(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test complete flow: OpenAI API call → track → calculate cost → store metric."""
        config = ClientConfig.from_env()

        # Create mock response
        mock_response = create_mock_openai_response(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-3.5-turbo",
            content="Test response",
        )

        mock_openai_client.chat.completions.create.return_value = mock_response

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                # Set tracking context
                manager.set_tracking_context(
                    job_id="test-job-123",
                    batch_id="test-batch-456",
                )

                # Make API call
                openai = manager.openai
                response = openai.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": "Hello"}],
                    temperature=0.7,
                )

                # Verify response
                assert response.id == "chatcmpl-test123"
                assert response.choices[0].message.content == "Test response"

                # Verify metric was stored (check both interfaces)
                assert mock_dynamo_client.put_item.called or (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                )

                # Check stored metric (handle both interfaces)
                if (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                ):
                    # New resilient client interface - get metric from call args
                    call_args = (
                        mock_dynamo_client.put_ai_usage_metric.call_args
                    )
                    metric = call_args.args[
                        0
                    ]  # First argument is the AIUsageMetric
                    item = metric.to_dynamodb_item()
                else:
                    # Legacy interface
                    call_args = mock_dynamo_client.put_item.call_args
                    assert call_args.kwargs["TableName"] == "test-table"
                    item = call_args.kwargs["Item"]
                assert item["service"]["S"] == "openai"
                assert item["model"]["S"] == "gpt-3.5-turbo"
                assert item["operation"]["S"] == "completion"
                assert int(item["inputTokens"]["N"]) == 100
                assert int(item["outputTokens"]["N"]) == 50
                assert int(item["totalTokens"]["N"]) == 150
                assert item["jobId"]["S"] == "test-job-123"
                assert item["batchId"]["S"] == "test-batch-456"
                assert item["userId"]["S"] == "test-user"

                # Verify cost calculation
                cost = float(item["costUSD"]["N"])
                expected_cost = AICostCalculator.calculate_openai_cost(
                    model="gpt-3.5-turbo",
                    input_tokens=100,
                    output_tokens=50,
                    is_batch=False,
                )
                assert abs(cost - expected_cost) < 0.0001

    @pytest.mark.integration
    def test_openai_embedding_tracking_flow(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test OpenAI embedding API tracking flow."""
        config = ClientConfig.from_env()

        # Create mock embedding response
        mock_response = Mock()
        mock_response.data = [Mock(embedding=[0.1] * 1536, index=0)]
        mock_response.model = "text-embedding-ada-002"
        mock_response.usage = Mock(prompt_tokens=10, total_tokens=10)

        mock_openai_client.embeddings.create.return_value = mock_response

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                # Make embedding call
                openai = manager.openai
                response = openai.embeddings.create(
                    model="text-embedding-ada-002",
                    input="Test text",
                )

                # Verify response
                assert len(response.data) == 1
                assert response.model == "text-embedding-ada-002"

                # Verify metric was stored (check both interfaces)
                assert mock_dynamo_client.put_item.called or (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                )

                # Check stored metric (handle both interfaces)
                if (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                ):
                    # New resilient client interface - get metric from call args
                    call_args = (
                        mock_dynamo_client.put_ai_usage_metric.call_args
                    )
                    metric = call_args.args[
                        0
                    ]  # First argument is the AIUsageMetric
                    item = metric.to_dynamodb_item()
                else:
                    # Legacy interface
                    call_args = mock_dynamo_client.put_item.call_args
                    item = call_args.kwargs["Item"]
                assert item["service"]["S"] == "openai"
                assert item["model"]["S"] == "text-embedding-ada-002"
                assert item["operation"]["S"] == "embedding"
                # Note: embedding tracking may not capture input tokens the same way
                assert int(item["totalTokens"]["N"]) == 10

    @pytest.mark.integration
    def test_concurrent_api_calls_tracking(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test concurrent API calls are tracked independently."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        config = ClientConfig.from_env()

        # Track call count
        call_count = {"count": 0}
        original_create = mock_openai_client.chat.completions.create

        def mock_create(*args, **kwargs):
            call_count["count"] += 1
            return create_mock_openai_response(
                prompt_tokens=100 + call_count["count"],
                completion_tokens=50 + call_count["count"],
                model="gpt-3.5-turbo",
                content=f"Response {call_count['count']}",
            )

        mock_openai_client.chat.completions.create = mock_create

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                def make_api_call(job_id: str):
                    # Set unique context per thread
                    manager.set_tracking_context(job_id=job_id)

                    openai = manager.openai
                    response = openai.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[
                            {"role": "user", "content": f"Hello from {job_id}"}
                        ],
                    )
                    return response

                # Make concurrent calls
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = []
                    for i in range(5):
                        future = executor.submit(make_api_call, f"job-{i}")
                        futures.append(future)

                    # Wait for all calls to complete
                    responses = [f.result() for f in futures]

                # Verify all calls were made
                assert len(responses) == 5

                # Verify all metrics were stored
                assert get_dynamo_call_count(mock_dynamo_client) == 5

                # Verify each metric has unique data
                stored_metrics = []
                for call in get_dynamo_call_args(mock_dynamo_client):
                    item = call.kwargs["Item"]
                    stored_metrics.append(
                        {
                            "request_id": item["requestId"]["S"],
                            "input_tokens": int(item["inputTokens"]["N"]),
                            "job_id": item.get("jobId", {}).get("S"),
                        }
                    )

                # Check uniqueness
                request_ids = [m["request_id"] for m in stored_metrics]
                assert len(set(request_ids)) == 5  # All unique

                # Check token values are different
                token_values = [m["input_tokens"] for m in stored_metrics]
                assert len(set(token_values)) == 5  # All different

    @pytest.mark.integration
    def test_error_handling_and_metric_storage(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test error handling during API calls still records metrics."""
        config = ClientConfig.from_env()

        # Make OpenAI client raise an error
        mock_openai_client.chat.completions.create.side_effect = Exception(
            "API rate limit exceeded"
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                # Make API call that will fail
                openai = manager.openai
                with pytest.raises(Exception, match="API rate limit exceeded"):
                    openai.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "Hello"}],
                    )

                # Verify error metric was still stored (check both interfaces)
                assert mock_dynamo_client.put_item.called or (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                )

                # Check stored metric contains error (handle both interfaces)
                if (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                ):
                    # New resilient client interface - get metric from call args
                    call_args = (
                        mock_dynamo_client.put_ai_usage_metric.call_args
                    )
                    metric = call_args.args[
                        0
                    ]  # First argument is the AIUsageMetric
                    item = metric.to_dynamodb_item()
                else:
                    # Legacy interface
                    call_args = mock_dynamo_client.put_item.call_args
                    item = call_args.kwargs["Item"]
                assert item["service"]["S"] == "openai"
                # Model may still be captured from kwargs even on error
                assert item["error"]["S"] == "API rate limit exceeded"

    @pytest.mark.integration
    def test_batch_operation_tracking(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test batch operations are tracked with correct pricing."""
        config = ClientConfig.from_env()

        # Create batch response
        mock_response = create_mock_openai_response(
            prompt_tokens=1000,
            completion_tokens=500,
            model="gpt-3.5-turbo",
            content="Batch response",
        )

        mock_openai_client.chat.completions.create.return_value = mock_response

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                # Set batch context
                manager.set_tracking_context(batch_id="batch-123")

                # Make batch API call
                openai = manager.openai
                response = openai.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": "Batch request"}],
                    is_batch=True,  # Indicate batch pricing
                )

                # Verify metric was stored (check both interfaces)
                assert mock_dynamo_client.put_item.called or (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                )

                # Check batch pricing was applied (handle both interfaces)
                if (
                    hasattr(mock_dynamo_client, "put_ai_usage_metric")
                    and mock_dynamo_client.put_ai_usage_metric.called
                ):
                    # New resilient client interface - get metric from call args
                    call_args = (
                        mock_dynamo_client.put_ai_usage_metric.call_args
                    )
                    metric = call_args.args[
                        0
                    ]  # First argument is the AIUsageMetric
                    item = metric.to_dynamodb_item()
                else:
                    # Legacy interface
                    call_args = mock_dynamo_client.put_item.call_args
                    item = call_args.kwargs["Item"]

                # Calculate expected batch cost (50% discount)
                regular_cost = AICostCalculator.calculate_openai_cost(
                    model="gpt-3.5-turbo",
                    input_tokens=1000,
                    output_tokens=500,
                    is_batch=False,
                )
                batch_cost = AICostCalculator.calculate_openai_cost(
                    model="gpt-3.5-turbo",
                    input_tokens=1000,
                    output_tokens=500,
                    is_batch=True,
                )

                assert batch_cost == regular_cost * 0.5
                assert float(item["costUSD"]["N"]) == batch_cost

    @pytest.mark.integration
    def test_context_propagation_across_calls(
        self, mock_env, mock_dynamo_client, mock_openai_client
    ):
        """Test context (job_id, batch_id) propagates across multiple calls."""
        config = ClientConfig.from_env()

        # Create responses
        responses = []
        for i in range(3):
            responses.append(
                create_mock_openai_response(
                    prompt_tokens=100,
                    completion_tokens=50,
                    model="gpt-3.5-turbo",
                    content=f"Response {i}",
                )
            )

        mock_openai_client.chat.completions.create.side_effect = responses

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                manager = ClientManager(config)

                # Set context once
                manager.set_tracking_context(
                    job_id="persistent-job",
                    batch_id="persistent-batch",
                )

                # Make multiple calls
                openai = manager.openai
                for i in range(3):
                    openai.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": f"Message {i}"}],
                    )

                # Verify all calls have same context
                assert get_dynamo_call_count(mock_dynamo_client) == 3

                for call in get_dynamo_call_args(mock_dynamo_client):
                    item = call.kwargs["Item"]
                    assert item["jobId"]["S"] == "persistent-job"
                    assert item["batchId"]["S"] == "persistent-batch"

    @pytest.mark.integration
    def test_usage_tracker_file_logging(self, mock_dynamo_client, tmp_path):
        """Test usage tracker can log to file when enabled."""
        log_file = tmp_path / "ai_usage.jsonl"

        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test.pinecone.io",
            track_usage=True,
        )

        # Mock response
        mock_openai_client = MagicMock(spec=OpenAI)
        mock_openai_client.chat = MagicMock()
        mock_openai_client.chat.completions = MagicMock()
        mock_response = create_mock_openai_response(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-3.5-turbo",
            content="Test response",
        )
        mock_openai_client.chat.completions.create.return_value = mock_response

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai_client,
            ):
                with patch.dict(os.environ, {"TRACK_TO_FILE": "true"}):
                    manager = ClientManager(config)

                    # Override log file path
                    manager.usage_tracker.log_file = str(log_file)

                    # Make API call
                    openai = manager.openai
                    openai.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "Hello"}],
                    )

                    # Verify log file was created
                    assert log_file.exists()

                    # Read and verify log content
                    with open(log_file) as f:
                        log_entry = json.loads(f.readline())

                    assert log_entry["service"] == "openai"
                    assert log_entry["model"] == "gpt-3.5-turbo"
                    assert log_entry["operation"] == "completion"
                    assert log_entry["input_tokens"] == 100
                    assert log_entry["output_tokens"] == 50
                    assert log_entry["total_tokens"] == 150

    @pytest.mark.integration
    def test_metric_query_by_service_and_date(
        self, mock_env, mock_dynamo_client
    ):
        """Test querying metrics by service and date range."""
        config = ClientConfig.from_env()

        # Mock query response
        mock_dynamo_client.query.return_value = {
            "Items": [
                {
                    "service": {"S": "openai"},
                    "model": {"S": "gpt-3.5-turbo"},
                    "operation": {"S": "completion"},
                    "timestamp": {"S": "2024-01-15T10:00:00Z"},
                    "requestId": {"S": "test-request-1"},
                    "inputTokens": {"N": "100"},
                    "outputTokens": {"N": "50"},
                    "totalTokens": {"N": "150"},
                    "costUSD": {"N": "0.000225"},
                    "apiCalls": {"N": "1"},
                    "date": {"S": "2024-01-15"},
                },
                {
                    "service": {"S": "openai"},
                    "model": {"S": "gpt-4"},
                    "operation": {"S": "completion"},
                    "timestamp": {"S": "2024-01-15T11:00:00Z"},
                    "requestId": {"S": "test-request-2"},
                    "inputTokens": {"N": "200"},
                    "outputTokens": {"N": "100"},
                    "totalTokens": {"N": "300"},
                    "costUSD": {"N": "0.009"},
                    "apiCalls": {"N": "1"},
                    "date": {"S": "2024-01-15"},
                },
            ]
        }

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            manager = ClientManager(config)

            # Query metrics
            metrics = AIUsageMetric.query_by_service_date(
                manager.dynamo,
                service="openai",
                start_date="2024-01-15",
                end_date="2024-01-15",
            )

            assert len(metrics) == 2
            assert all(m.service == "openai" for m in metrics)
            assert metrics[0].model == "gpt-3.5-turbo"
            assert metrics[1].model == "gpt-4"

            # Verify query parameters
            call_args = mock_dynamo_client.query.call_args
            assert call_args.kwargs["IndexName"] == "GSI1"
            # Check that the expression values contain the service
            expression_values = call_args.kwargs["ExpressionAttributeValues"]
            assert expression_values[":pk"]["S"] == "AI_USAGE#openai"
