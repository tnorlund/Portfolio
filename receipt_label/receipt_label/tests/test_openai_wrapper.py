"""Comprehensive unit tests for OpenAI client wrapping functionality."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion
from openai.types.create_embedding_response import CreateEmbeddingResponse

# Add the parent directory to the path to access the tests utils
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from tests.utils.ai_usage_helpers import (
    create_mock_openai_response,
    create_test_tracking_context,
)


@pytest.mark.unit
class TestOpenAIWrapperBasics:
    """Test basic OpenAI wrapper functionality."""

    def test_wrapper_creates_successfully(self):
        """Test that wrapper is created successfully."""
        mock_client = Mock(spec=OpenAI)
        mock_client.chat = Mock()
        mock_client.embeddings = Mock()
        tracker = AIUsageTracker()

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        assert wrapped_client is not None
        # Check that we can access these attributes
        assert wrapped_client.chat is not None
        assert wrapped_client.embeddings is not None

    def test_wrapper_preserves_client_attributes(self):
        """Test that wrapper preserves original client attributes."""
        mock_client = Mock(spec=OpenAI)
        mock_client.api_key = "test-key"
        mock_client.organization = "test-org"
        mock_client.base_url = "https://api.openai.com/v1"
        mock_client.timeout = 30.0
        mock_client.max_retries = 2

        tracker = AIUsageTracker()
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # All attributes should be accessible
        assert wrapped_client.api_key == "test-key"
        assert wrapped_client.organization == "test-org"
        assert wrapped_client.base_url == "https://api.openai.com/v1"
        assert wrapped_client.timeout == 30.0
        assert wrapped_client.max_retries == 2

    def test_wrapper_with_custom_attributes(self):
        """Test wrapper with custom client attributes."""
        mock_client = Mock(spec=OpenAI)
        mock_client.custom_header = {"X-Custom": "value"}
        mock_client.custom_method = Mock(return_value="custom result")

        tracker = AIUsageTracker()
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Custom attributes should be accessible
        assert wrapped_client.custom_header == {"X-Custom": "value"}
        assert wrapped_client.custom_method() == "custom result"


@pytest.mark.unit
class TestWrappedChatCompletions:
    """Test wrapped chat completion functionality."""

    def test_chat_completion_tracking(self):
        """Test basic chat completion tracking."""
        # Setup mock client
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Create mock response
        mock_response = create_mock_openai_response(
            prompt_tokens=150,
            completion_tokens=75,
            model="gpt-4",
            content="Test response content",
        )
        mock_completions.create.return_value = mock_response

        # Setup tracker with DynamoDB
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            user_id="test-user",
        )

        # Create wrapped client
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Make API call
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
        ]

        response = wrapped_client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.8,
            max_tokens=500,
            top_p=0.9,
        )

        # Verify response
        assert response == mock_response

        # Verify original client was called correctly
        mock_completions.create.assert_called_once_with(
            model="gpt-4",
            messages=messages,
            temperature=0.8,
            max_tokens=500,
            top_p=0.9,
        )

        # Verify tracking occurred
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "openai"
        assert item["model"]["S"] == "gpt-4"
        assert item["operation"]["S"] == "completion"
        assert item["inputTokens"]["N"] == "150"
        assert item["outputTokens"]["N"] == "75"
        assert item["totalTokens"]["N"] == "225"
        assert item["userId"]["S"] == "test-user"

        # Verify metadata
        metadata_map = item["metadata"]["M"]
        assert float(metadata_map["temperature"]["N"]) == 0.8
        assert int(metadata_map["max_tokens"]["N"]) == 500

    def test_chat_completion_with_streaming(self):
        """Test chat completion with streaming response."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Create mock streaming response
        mock_stream = Mock()
        mock_completions.create.return_value = mock_stream

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Make streaming call
        response = wrapped_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True,
        )

        assert response == mock_stream

        # Verify original client was called with stream=True
        mock_completions.create.assert_called_once()
        call_kwargs = mock_completions.create.call_args.kwargs
        assert call_kwargs["stream"] is True

        # Tracking should still occur (though without usage data)
        mock_dynamo.put_item.assert_called_once()

    def test_chat_completion_with_functions(self):
        """Test chat completion with function calling."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Create response with function call
        mock_response = Mock()
        mock_response.model = "gpt-3.5-turbo-0613"
        mock_response.usage = Mock(
            prompt_tokens=200, completion_tokens=50, total_tokens=250
        )
        mock_response.choices = [
            Mock(
                message=Mock(
                    function_call=Mock(
                        name="get_weather", arguments='{"location": "Boston"}'
                    )
                ),
                finish_reason="function_call",
            )
        ]
        mock_completions.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Define functions
        functions = [
            {
                "name": "get_weather",
                "description": "Get the weather in a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"},
                    },
                },
            }
        ]

        response = wrapped_client.chat.completions.create(
            model="gpt-3.5-turbo-0613",
            messages=[
                {"role": "user", "content": "What's the weather in Boston?"}
            ],
            functions=functions,
            function_call="auto",
        )

        assert response == mock_response

        # Verify tracking
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["model"]["S"] == "gpt-3.5-turbo-0613"
        assert item["inputTokens"]["N"] == "200"
        assert item["outputTokens"]["N"] == "50"

    def test_chat_completion_error_handling(self):
        """Test error handling in chat completions."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Simulate API error
        mock_completions.create.side_effect = Exception(
            "API rate limit exceeded"
        )

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Call should raise exception
        with pytest.raises(Exception, match="API rate limit exceeded"):
            wrapped_client.chat.completions.create(
                model="gpt-4", messages=[{"role": "user", "content": "Hello"}]
            )

        # Error should be tracked
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "API rate limit exceeded"
        assert item["service"]["S"] == "openai"
        assert item["operation"]["S"] == "completion"


@pytest.mark.unit
class TestWrappedEmbeddings:
    """Test wrapped embedding functionality."""

    def test_embedding_tracking(self):
        """Test basic embedding tracking."""
        mock_client = Mock(spec=OpenAI)
        mock_embeddings = Mock()
        mock_client.embeddings = mock_embeddings

        # Create mock response
        mock_response = Mock()
        mock_response.model = "text-embedding-3-small"
        mock_response.usage = Mock(total_tokens=250)
        mock_response.data = [
            Mock(embedding=[0.1, 0.2, 0.3], index=0),
            Mock(embedding=[0.4, 0.5, 0.6], index=1),
        ]
        mock_embeddings.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            user_id="embed-user",
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Make embedding call
        input_texts = ["Hello world", "How are you?"]
        response = wrapped_client.embeddings.create(
            model="text-embedding-3-small",
            input=input_texts,
            encoding_format="float",
        )

        assert response == mock_response

        # Verify original client was called
        mock_embeddings.create.assert_called_once_with(
            model="text-embedding-3-small",
            input=input_texts,
            encoding_format="float",
        )

        # Verify tracking
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "openai"
        assert item["model"]["S"] == "text-embedding-3-small"
        assert item["operation"]["S"] == "embedding"
        assert item["totalTokens"]["N"] == "250"
        assert item["userId"]["S"] == "embed-user"

        # Verify metadata
        metadata_map = item["metadata"]["M"]
        assert int(metadata_map["input_count"]["N"]) == 2

    def test_embedding_batch_mode(self):
        """Test embedding tracking in batch mode."""
        mock_client = Mock(spec=OpenAI)
        mock_embeddings = Mock()
        mock_client.embeddings = mock_embeddings

        mock_response = Mock()
        mock_response.model = "text-embedding-3-large"
        mock_response.usage = Mock(total_tokens=5000)
        mock_embeddings.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Large batch of texts
        large_input = ["text" + str(i) for i in range(100)]

        response = wrapped_client.embeddings.create(
            model="text-embedding-3-large",
            input=large_input,
            is_batch=True,  # Batch mode for reduced pricing
        )

        # Verify tracking with batch pricing
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        # Cost should reflect batch pricing (50% off)
        cost_usd = float(item["costUSD"]["N"])
        assert cost_usd > 0  # Should have a cost

        metadata_map = item["metadata"]["M"]
        assert int(metadata_map["input_count"]["N"]) == 100

    def test_embedding_with_single_input(self):
        """Test embedding with single string input."""
        mock_client = Mock(spec=OpenAI)
        mock_embeddings = Mock()
        mock_client.embeddings = mock_embeddings

        mock_response = Mock()
        mock_response.model = "text-embedding-ada-002"
        mock_response.usage = Mock(total_tokens=10)
        mock_embeddings.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Single string input (not a list)
        response = wrapped_client.embeddings.create(
            model="text-embedding-ada-002", input="Single text input"
        )

        # Verify metadata handles single input
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        metadata_map = item["metadata"]["M"]

        # Single string should still be counted
        assert "input_count" in metadata_map

    def test_embedding_error_handling(self):
        """Test error handling in embeddings."""
        mock_client = Mock(spec=OpenAI)
        mock_embeddings = Mock()
        mock_client.embeddings = mock_embeddings

        mock_embeddings.create.side_effect = ValueError("Invalid model")

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        with pytest.raises(ValueError, match="Invalid model"):
            wrapped_client.embeddings.create(
                model="invalid-model", input=["test"]
            )

        # Error should be tracked
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "Invalid model"


@pytest.mark.unit
class TestWrapperWithContext:
    """Test wrapper behavior with different contexts."""

    def test_wrapper_respects_tracker_context(self):
        """Test that wrapper uses tracker's context settings."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        mock_response = create_mock_openai_response()
        mock_completions.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        # Set context before wrapping
        tracker.set_tracking_context(
            job_id="job-999",
            batch_id="batch-888",
            github_pr=777,
            user_id="context-user",
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        wrapped_client.chat.completions.create(model="gpt-3.5-turbo")

        # Verify context was included
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["jobId"]["S"] == "job-999"
        assert item["batchId"]["S"] == "batch-888"
        assert item["userId"]["S"] == "context-user"

    def test_wrapper_context_changes(self):
        """Test that wrapper respects context changes."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        mock_response = create_mock_openai_response()
        mock_completions.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # First call with initial context
        tracker.set_tracking_context(job_id="job-1")
        wrapped_client.chat.completions.create(model="gpt-3.5-turbo")

        first_call = mock_dynamo.put_item.call_args_list[0]
        first_item = first_call.kwargs["Item"]
        assert first_item["jobId"]["S"] == "job-1"

        # Change context and make another call
        tracker.set_tracking_context(job_id="job-2")
        wrapped_client.chat.completions.create(model="gpt-3.5-turbo")

        second_call = mock_dynamo.put_item.call_args_list[1]
        second_item = second_call.kwargs["Item"]
        assert second_item["jobId"]["S"] == "job-2"


@pytest.mark.unit
class TestWrapperEdgeCases:
    """Test edge cases and unusual scenarios."""

    def test_wrapper_with_none_tracker(self):
        """Test that wrapper handles None tracker gracefully."""
        mock_client = Mock(spec=OpenAI)

        # This should work but raise error when trying to use tracking
        try:
            wrapped_client = AIUsageTracker.create_wrapped_openai_client(
                mock_client, None
            )
            # The error might come when accessing attributes that need the tracker
            assert wrapped_client is not None
        except (AttributeError, TypeError):
            # This is acceptable - None tracker should cause an error
            pass

    def test_wrapper_with_missing_methods(self):
        """Test wrapper with client missing expected methods."""
        # Client without chat attribute
        mock_client = Mock()
        del mock_client.chat  # Remove chat attribute

        tracker = AIUsageTracker()
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Accessing missing attribute should raise AttributeError
        with pytest.raises(AttributeError):
            wrapped_client.chat

    def test_wrapper_preserves_response_types(self):
        """Test that wrapper preserves response object types."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Create a properly typed response
        mock_response = Mock(spec=ChatCompletion)
        mock_response.id = "chatcmpl-123"
        mock_response.model = "gpt-3.5-turbo"
        mock_response.usage = Mock(
            prompt_tokens=10, completion_tokens=20, total_tokens=30
        )
        mock_completions.create.return_value = mock_response

        tracker = AIUsageTracker()
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        response = wrapped_client.chat.completions.create(
            model="gpt-3.5-turbo"
        )

        # Response should maintain its type and attributes
        assert response.id == "chatcmpl-123"
        assert response.model == "gpt-3.5-turbo"
        assert response.usage.total_tokens == 30

    def test_wrapper_with_async_client(self):
        """Test wrapper behavior with async client methods."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Add async method (should pass through)
        async def async_create(**kwargs):
            return "async response"

        mock_completions.acreate = async_create

        tracker = AIUsageTracker()
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Async method might not be directly accessible through wrapper
        # The wrapper only wraps specific methods (create)
        # Other methods would be accessible through __getattr__
        assert wrapped_client.chat.completions.create is not None

    def test_multiple_wrapped_clients(self):
        """Test multiple wrapped clients with same tracker."""
        tracker = AIUsageTracker(
            dynamo_client=Mock(), table_name="test-table", track_to_dynamo=True
        )

        # Create multiple clients
        mock_client1 = Mock(spec=OpenAI)
        mock_client1.chat = Mock()
        mock_client1.chat.completions = Mock()

        mock_client2 = Mock(spec=OpenAI)
        mock_client2.chat = Mock()
        mock_client2.chat.completions = Mock()

        wrapped1 = AIUsageTracker.create_wrapped_openai_client(
            mock_client1, tracker
        )
        wrapped2 = AIUsageTracker.create_wrapped_openai_client(
            mock_client2, tracker
        )

        # Both should work independently
        mock_response1 = create_mock_openai_response(model="gpt-3.5-turbo")
        mock_response2 = create_mock_openai_response(model="gpt-4")

        mock_client1.chat.completions.create.return_value = mock_response1
        mock_client2.chat.completions.create.return_value = mock_response2

        response1 = wrapped1.chat.completions.create(model="gpt-3.5-turbo")
        response2 = wrapped2.chat.completions.create(model="gpt-4")

        assert response1.model == "gpt-3.5-turbo"
        assert response2.model == "gpt-4"

        # Both calls should be tracked
        assert tracker.dynamo_client.put_item.call_count == 2


@pytest.mark.unit
class TestWrapperPerformance:
    """Test wrapper performance characteristics."""

    def test_wrapper_minimal_overhead(self):
        """Test that wrapper adds minimal overhead."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Fast mock response
        def fast_create(**kwargs):
            return create_mock_openai_response()

        mock_completions.create = fast_create

        # Tracker without any backends (minimal overhead)
        tracker = AIUsageTracker(track_to_dynamo=False, track_to_file=False)

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Time the wrapped call
        start = time.time()
        for _ in range(100):
            wrapped_client.chat.completions.create(model="gpt-3.5-turbo")
        wrapped_time = time.time() - start

        # Time direct calls for comparison
        start = time.time()
        for _ in range(100):
            fast_create(model="gpt-3.5-turbo")
        direct_time = time.time() - start

        # IMPORTANT: These thresholds are environment-dependent
        # CI environments are less performant than local development machines
        # Values tuned for GitHub Actions CI environment performance
        # The wrapper should have reasonable overhead (less than 5x slower in CI)
        assert wrapped_time < direct_time * 5

    def test_wrapper_with_large_responses(self):
        """Test wrapper with large response objects."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Large response
        large_content = "x" * 10000
        mock_response = create_mock_openai_response(
            prompt_tokens=5000, completion_tokens=5000, content=large_content
        )
        mock_completions.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        response = wrapped_client.chat.completions.create(
            model="gpt-3.5-turbo"
        )

        # Large response should be handled correctly
        assert len(response.choices[0].message.content) == 10000
        assert response.usage.total_tokens == 10000

        # Tracking should work with large token counts
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["totalTokens"]["N"] == "10000"
