"""Shared test utilities for AI usage tracking tests."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import Mock

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
