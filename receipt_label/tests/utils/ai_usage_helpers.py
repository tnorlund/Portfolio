"""Shared test utilities for AI usage tracking tests."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from unittest.mock import MagicMock, Mock, patch

from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice, CompletionUsage
from openai.types.create_embedding_response import CreateEmbeddingResponse
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


def create_mock_usage_metric(**kwargs) -> AIUsageMetric:
    """Factory for creating test AIUsageMetric objects.

    Args:
        **kwargs: Override default values

    Returns:
        AIUsageMetric with test data
    """
    defaults = {
        "service": "openai",
        "model": "gpt-3.5-turbo",
        "operation": "chat_completion",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "total_cost": 0.000225,  # Based on actual pricing
        "latency_ms": 500,
        "timestamp": datetime.now(timezone.utc),
        "metadata": {"job_id": "test-job-123", "user_id": "test-user"},
    }
    defaults.update(kwargs)
    return AIUsageMetric(**defaults)


def create_mock_openai_response(
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    model: str = "gpt-3.5-turbo",
    content: str = "Test response",
) -> Mock:
    """Factory for creating mock OpenAI API responses.

    Args:
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        model: Model name
        content: Response content

    Returns:
        Mock object mimicking OpenAI response structure
    """
    response = Mock()
    response.usage = Mock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    response.model = model
    response.choices = [
        Mock(message=Mock(content=content), index=0, finish_reason="stop")
    ]
    response.id = "chatcmpl-test123"
    response.created = int(datetime.now().timestamp())
    return response


def create_mock_anthropic_response(
    input_tokens: int = 100,
    output_tokens: int = 50,
    model: str = "claude-3-opus-20240229",
    content: str = "Test response",
) -> Mock:
    """Factory for creating mock Anthropic API responses.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        model: Model name
        content: Response content

    Returns:
        Mock object mimicking Anthropic response structure
    """
    response = Mock()
    response.usage = Mock(
        input_tokens=input_tokens, output_tokens=output_tokens
    )
    response.model = model
    response.content = [Mock(text=content, type="text")]
    response.id = "msg_test123"
    response.type = "message"
    response.role = "assistant"
    return response


def create_mock_google_places_response(
    place_id: str = "ChIJtest123",
    name: str = "Test Restaurant",
    address: str = "123 Test St",
) -> Dict[str, Any]:
    """Factory for creating mock Google Places API responses.

    Args:
        place_id: Google place ID
        name: Place name
        address: Place address

    Returns:
        Dict mimicking Google Places response
    """
    return {
        "result": {
            "place_id": place_id,
            "name": name,
            "formatted_address": address,
            "geometry": {"location": {"lat": 37.7749, "lng": -122.4194}},
            "types": ["restaurant", "food", "establishment"],
        },
        "status": "OK",
    }


def assert_usage_metric_equal(
    metric1: AIUsageMetric,
    metric2: AIUsageMetric,
    ignore_fields: Optional[list] = None,
) -> None:
    """Compare two AIUsageMetric objects for equality.

    Args:
        metric1: First metric
        metric2: Second metric
        ignore_fields: Fields to ignore in comparison (e.g., ['timestamp', 'id'])
    """
    ignore_fields = ignore_fields or []

    fields_to_check = [
        "service",
        "model",
        "operation",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "total_cost",
        "latency_ms",
        "metadata",
    ]

    for field in fields_to_check:
        if field not in ignore_fields:
            val1 = getattr(metric1, field, None)
            val2 = getattr(metric2, field, None)
            assert val1 == val2, f"Field '{field}' mismatch: {val1} != {val2}"


def create_test_tracking_context(
    job_id: Optional[str] = "test-job-123",
    batch_id: Optional[str] = None,
    user_id: Optional[str] = "test-user",
) -> Dict[str, Any]:
    """Create a test tracking context dictionary.

    Args:
        job_id: Job identifier
        batch_id: Batch identifier
        user_id: User identifier

    Returns:
        Context dictionary for AI usage tracking
    """
    context = {}
    if job_id:
        context["job_id"] = job_id
    if batch_id:
        context["batch_id"] = batch_id
    if user_id:
        context["user_id"] = user_id
    return context


class MockServiceFactory:
    """Factory for creating properly configured mock services for testing."""

    @staticmethod
    def create_openai_client(
        completion_response: Optional[Mock] = None,
        embedding_response: Optional[Mock] = None,
    ) -> Mock:
        """Create a properly configured mock OpenAI client.

        Args:
            completion_response: Custom completion response (optional)
            embedding_response: Custom embedding response (optional)

        Returns:
            Mock OpenAI client with proper spec and structure
        """
        client = MagicMock(spec=OpenAI)

        # Set up chat completions
        chat_mock = MagicMock()
        completions_mock = MagicMock()

        if completion_response is None:
            completion_response = create_mock_openai_response()

        completions_mock.create.return_value = completion_response
        chat_mock.completions = completions_mock
        client.chat = chat_mock

        # Set up embeddings
        embeddings_mock = MagicMock()

        if embedding_response is None:
            embedding_response = create_mock_embedding_response()

        embeddings_mock.create.return_value = embedding_response
        client.embeddings = embeddings_mock

        # Add client attributes
        client.api_key = "test-key"
        client.organization = "test-org"
        client.base_url = "https://api.openai.com/v1"
        client.timeout = 30.0
        client.max_retries = 2

        return client

    @staticmethod
    def create_anthropic_client(response: Optional[Mock] = None) -> Mock:
        """Create a properly configured mock Anthropic client.

        Args:
            response: Custom response (optional)

        Returns:
            Mock Anthropic client with proper spec and structure
        """
        client = MagicMock()

        # Set up messages completion
        messages_mock = MagicMock()

        if response is None:
            response = create_mock_anthropic_response()

        messages_mock.create.return_value = response
        client.messages = messages_mock

        # Add client attributes
        client.api_key = "test-key"
        client.base_url = "https://api.anthropic.com"

        return client

    @staticmethod
    def create_google_places_response(
        place_id: str = "ChIJtest123",
        name: str = "Test Restaurant",
        address: str = "123 Test St",
        status: str = "OK",
    ) -> Dict[str, Any]:
        """Create a comprehensive Google Places API response.

        Args:
            place_id: Google place ID
            name: Place name
            address: Place address
            status: API response status

        Returns:
            Dict mimicking complete Google Places response
        """
        if status == "OK":
            return {
                "result": {
                    "place_id": place_id,
                    "name": name,
                    "formatted_address": address,
                    "geometry": {
                        "location": {"lat": 37.7749, "lng": -122.4194},
                        "viewport": {
                            "northeast": {
                                "lat": 37.7762487,
                                "lng": -122.4180757,
                            },
                            "southwest": {
                                "lat": 37.7735487,
                                "lng": -122.4207757,
                            },
                        },
                    },
                    "types": ["restaurant", "food", "establishment"],
                    "rating": 4.2,
                    "price_level": 2,
                    "opening_hours": {
                        "open_now": True,
                        "weekday_text": [
                            "Monday: 11:00 AM – 10:00 PM",
                            "Tuesday: 11:00 AM – 10:00 PM",
                            "Wednesday: 11:00 AM – 10:00 PM",
                            "Thursday: 11:00 AM – 10:00 PM",
                            "Friday: 11:00 AM – 11:00 PM",
                            "Saturday: 11:00 AM – 11:00 PM",
                            "Sunday: 11:00 AM – 9:00 PM",
                        ],
                    },
                    "photos": [
                        {
                            "height": 3024,
                            "width": 4032,
                            "photo_reference": "test_photo_reference",
                        }
                    ],
                },
                "status": status,
            }
        else:
            return {
                "error_message": "Request was invalid",
                "status": status,
            }

    @staticmethod
    def create_dynamo_client(
        put_response: Optional[Dict] = None,
        query_response: Optional[Dict] = None,
    ) -> Mock:
        """Create a properly configured mock DynamoDB client.

        Args:
            put_response: Custom put_item response (optional)
            query_response: Custom query response (optional)

        Returns:
            Mock DynamoDB client with proper methods
        """
        client = MagicMock()
        client.table_name = "test-table"

        if put_response is None:
            put_response = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        if query_response is None:
            query_response = {"Items": [], "Count": 0, "ScannedCount": 0}

        client.put_item.return_value = put_response
        client.query.return_value = query_response
        client.batch_write_item.return_value = {
            "UnprocessedItems": {},
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

        return client


def create_mock_embedding_response(
    model: str = "text-embedding-3-small",
    total_tokens: int = 100,
    embeddings_count: int = 1,
) -> Mock:
    """Factory for creating mock OpenAI embedding responses.

    Args:
        model: Model name
        total_tokens: Total tokens used
        embeddings_count: Number of embeddings in response

    Returns:
        Mock object mimicking OpenAI embedding response structure
    """
    response = MagicMock(spec=CreateEmbeddingResponse)
    response.model = model
    response.usage = Mock(total_tokens=total_tokens)

    # Create mock embeddings data
    embeddings = []
    for i in range(embeddings_count):
        embedding = Mock()
        embedding.embedding = [
            0.1 * j for j in range(1536)
        ]  # Standard embedding size
        embedding.index = i
        embeddings.append(embedding)

    response.data = embeddings
    response.object = "list"

    return response
