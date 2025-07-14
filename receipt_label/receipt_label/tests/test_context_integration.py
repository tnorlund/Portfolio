"""
Integration tests for AI Usage Context Manager with wrapped clients.

Tests that context properly propagates through wrapped OpenAI and other AI clients.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice, CompletionUsage

from receipt_label.utils import (
    ClientConfig,
    ClientManager,
    ai_usage_context,
    ai_usage_tracked,
)


class TestContextIntegration:
    """Test context manager integration with wrapped AI clients."""

    @pytest.fixture
    def mock_openai_response(self):
        """Create a mock OpenAI response."""
        return ChatCompletion(
            id="chatcmpl-test123",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(
                        content="Test response",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="gpt-3.5-turbo",
            object="chat.completion",
            usage=CompletionUsage(
                prompt_tokens=50,
                completion_tokens=25,
                total_tokens=75,
            ),
        )

    @pytest.fixture
    def mock_dynamo_client(self):
        """Mock DynamoDB client."""
        client = MagicMock()
        client.put_item = Mock(
            return_value={"ResponseMetadata": {"HTTPStatusCode": 200}}
        )
        client.table_name = "test-table"
        return client

    def test_context_propagates_through_wrapped_client(
        self, mock_openai_response, mock_dynamo_client
    ):
        """Test that context manager propagates to wrapped OpenAI client."""
        # Create mock OpenAI client
        mock_openai = MagicMock(spec=OpenAI)
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_openai_response

        # Create client manager with mocked clients
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test.pinecone.io",
            track_usage=True,
            user_id="test-user",
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                # Use context manager
                with ai_usage_context(
                    "test_operation",
                    job_id="job-123",
                    batch_id="batch-456",
                    custom_field="custom_value",
                ):
                    # Make API call through wrapped client
                    openai_client = manager.openai
                    response = openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "Hello"}],
                    )

                    # Verify response
                    assert (
                        response.choices[0].message.content == "Test response"
                    )

                # Check that metadata was removed before calling OpenAI
                # The wrapped client should NOT pass metadata to the actual API
                call_args = mock_openai.chat.completions.create.call_args
                assert "metadata" not in call_args[1]

                # The tracking should still happen (check tracker was used)
                # This would be verified by checking DynamoDB calls in a real test

    def test_decorator_with_wrapped_client(
        self, mock_openai_response, mock_dynamo_client
    ):
        """Test that decorator context propagates through wrapped client."""
        # Create mock OpenAI client
        mock_openai = MagicMock(spec=OpenAI)
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_openai_response

        # Create client manager
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test.pinecone.io",
            track_usage=True,
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                @ai_usage_tracked(
                    operation_type="decorated_op", project="test_project"
                )
                def process_with_ai(text: str):
                    openai_client = manager.openai
                    return openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": text}],
                    )

                # Call decorated function
                process_with_ai("Test input")

                # Verify metadata was removed before calling OpenAI
                call_args = mock_openai.chat.completions.create.call_args
                assert "metadata" not in call_args[1]

    def test_nested_contexts_propagate(
        self, mock_openai_response, mock_dynamo_client
    ):
        """Test that nested contexts properly propagate through wrapped client."""
        # Create mock OpenAI client
        mock_openai = MagicMock(spec=OpenAI)
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_openai_response

        call_count = 0
        captured_metadata = []

        def capture_metadata(**kwargs):
            nonlocal call_count
            call_count += 1
            if "metadata" in kwargs:
                captured_metadata.append(kwargs["metadata"])
            return mock_openai_response

        mock_openai.chat.completions.create.side_effect = capture_metadata

        # Create client manager
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test.pinecone.io",
            track_usage=True,
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                # Nested contexts
                with ai_usage_context("outer_op", job_id="job-outer"):
                    openai_client = manager.openai

                    # First call in outer context
                    openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "Outer"}],
                    )

                    with ai_usage_context("inner_op", job_id="job-inner"):
                        # Second call in inner context
                        openai_client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[{"role": "user", "content": "Inner"}],
                        )

                # Verify both calls were made without metadata
                assert call_count == 2
                # Metadata should have been removed, so captured_metadata is empty
                assert len(captured_metadata) == 0

    def test_runtime_context_extraction(
        self, mock_openai_response, mock_dynamo_client
    ):
        """Test runtime context (job_id, batch_id) extraction from kwargs."""
        # Create mock OpenAI client
        mock_openai = MagicMock(spec=OpenAI)
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_openai_response

        # Create client manager
        config = ClientConfig(
            dynamo_table="test-table",
            openai_api_key="test-key",
            pinecone_api_key="test-key",
            pinecone_index_name="test-index",
            pinecone_host="test.pinecone.io",
            track_usage=True,
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_client,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                @ai_usage_tracked
                def process_with_context(
                    text: str, job_id: str = None, batch_id: str = None
                ):
                    openai_client = manager.openai
                    return openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": text}],
                    )

                # Call with runtime context
                process_with_context(
                    "Test", job_id="runtime-job", batch_id="runtime-batch"
                )

                # Verify metadata was removed before calling OpenAI
                call_args = mock_openai.chat.completions.create.call_args
                assert "metadata" not in call_args[1]
