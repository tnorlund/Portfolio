"""
Comprehensive integration tests for the AI Usage Tracking system.

Tests the complete integration between all components:
- ClientManager → AIUsageTracker → CostCalculator → AIUsageMetric → DynamoDB
- Batch processing and report generation
- Error recovery and data consistency
- Performance under load
"""

import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice, CompletionUsage
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.client_manager import ClientConfig, ClientManager
from receipt_label.utils.cost_calculator import AICostCalculator

sys.path.append(os.path.join(os.path.dirname(__file__), "../../.."))

from tests.utils.ai_usage_helpers import (
    create_mock_anthropic_response,
    create_mock_google_places_response,
    create_mock_openai_response,
    create_test_tracking_context,
)


@pytest.fixture
def integration_env():
    """Environment variables for integration testing."""
    env_vars = {
        "DYNAMODB_TABLE_NAME": "integration-test-table",
        "OPENAI_API_KEY": "test-openai-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "GOOGLE_PLACES_API_KEY": "test-google-key",
        "PINECONE_API_KEY": "test-pinecone-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
        "TRACK_AI_USAGE": "true",
        "USER_ID": "integration-test-user",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def mock_dynamo_with_data():
    """Mock DynamoDB client with realistic data."""
    client = MagicMock(spec=DynamoClient)
    client.table_name = "integration-test-table"

    # Store put_item calls for verification
    client._stored_items = []

    def store_item(**kwargs):
        client._stored_items.append(kwargs["Item"])
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def store_metric(metric):
        """Store AIUsageMetric for new resilient client interface."""
        item = metric.to_dynamodb_item()
        client._stored_items.append(item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    client.put_item = MagicMock(side_effect=store_item)
    client.put_ai_usage_metric = MagicMock(side_effect=store_metric)

    # Mock query responses
    def query_handler(**kwargs):
        # Return stored items based on query
        index = kwargs.get("IndexName")
        if index == "GSI1":
            # Filter by service
            pk_value = kwargs["ExpressionAttributeValues"][":pk"]["S"]
            service = pk_value.split("#")[1]
            items = [
                item
                for item in client._stored_items
                if item.get("service", {}).get("S") == service
            ]
            return {"Items": items}
        elif index == "GSI2":
            # Cost queries
            return {"Items": client._stored_items}
        elif index == "GSI3":
            # Job/batch queries
            pk_value = kwargs["ExpressionAttributeValues"][":pk"]["S"]
            if pk_value.startswith("JOB#"):
                job_id = pk_value.split("#")[1]
                items = [
                    item
                    for item in client._stored_items
                    if item.get("jobId", {}).get("S") == job_id
                ]
                return {"Items": items}
            elif pk_value.startswith("BATCH#"):
                batch_id = pk_value.split("#")[1]
                items = [
                    item
                    for item in client._stored_items
                    if item.get("batchId", {}).get("S") == batch_id
                ]
                return {"Items": items}
        return {"Items": []}

    client.query = MagicMock(side_effect=query_handler)
    return client


@pytest.mark.integration
class TestAIUsageSystemIntegration:
    """Test complete AI usage tracking system integration."""

    @pytest.mark.integration
    def test_complete_workflow_multiple_services(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test complete workflow across multiple AI services."""
        config = ClientConfig.from_env()

        # Mock service clients
        mock_openai = MagicMock(spec=OpenAI)
        mock_anthropic = MagicMock()

        # Create responses
        openai_response = create_mock_openai_response(
            prompt_tokens=100, completion_tokens=50
        )
        anthropic_response = create_mock_anthropic_response(
            input_tokens=150, output_tokens=75
        )

        mock_openai.chat.completions.create.return_value = openai_response
        mock_anthropic.messages.create.return_value = anthropic_response

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                # Track a job with multiple API calls
                job_id = "multi-service-job-123"
                manager.set_tracking_context(job_id=job_id)

                # Make OpenAI call
                openai_client = manager.openai
                openai_response = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "user", "content": "Analyze this receipt"}
                    ],
                )

                # Make Anthropic call (would need anthropic wrapper in real implementation)
                # For now, manually track it
                anthropic_metric = AIUsageMetric(
                    service="anthropic",
                    model="claude-3-opus-20240229",
                    operation="completion",
                    timestamp=datetime.now(timezone.utc),
                    input_tokens=150,
                    output_tokens=75,
                    total_tokens=225,
                    cost_usd=AICostCalculator.calculate_anthropic_cost(
                        model="claude-3-opus-20240229",
                        input_tokens=150,
                        output_tokens=75,
                    ),
                    job_id=job_id,
                    user_id="integration-test-user",
                )
                manager.usage_tracker._store_metric(anthropic_metric)

                # Make Google Places call
                places_metric = AIUsageMetric(
                    service="google_places",
                    model="places_api",
                    operation="place_lookup",
                    timestamp=datetime.now(timezone.utc),
                    cost_usd=AICostCalculator.calculate_google_places_cost(
                        operation="place_details"
                    ),
                    api_calls=1,
                    job_id=job_id,
                    user_id="integration-test-user",
                )
                manager.usage_tracker._store_metric(places_metric)

                # Verify all metrics were stored
                assert len(mock_dynamo_with_data._stored_items) == 3

                # Query metrics by job (simulate the query)
                job_metrics = [
                    m
                    for m in mock_dynamo_with_data._stored_items
                    if m.get("jobId", {}).get("S") == job_id
                ]
                assert len(job_metrics) == 3

                # Calculate total cost for job
                total_cost = sum(
                    float(m.get("costUSD", {}).get("N", 0))
                    for m in mock_dynamo_with_data._stored_items
                    if m.get("jobId", {}).get("S") == job_id
                )
                assert total_cost > 0

                # Verify services used
                services_used = {
                    m.get("service", {}).get("S")
                    for m in mock_dynamo_with_data._stored_items
                }
                assert services_used == {
                    "openai",
                    "anthropic",
                    "google_places",
                }

    @pytest.mark.integration
    def test_batch_processing_with_report_generation(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test batch processing workflow with report generation."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)
        batch_id = "batch-report-123"

        # Simulate batch of 100 API calls
        batch_responses = []
        for i in range(100):
            response = ChatCompletion(
                id=f"chatcmpl-batch-{i}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content=f"Batch response {i}",
                            role="assistant",
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-3.5-turbo",
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=50 + i,
                    completion_tokens=25 + i,
                    total_tokens=75 + i * 2,
                ),
            )
            batch_responses.append(response)

        mock_openai.chat.completions.create.side_effect = batch_responses

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)
                manager.set_tracking_context(batch_id=batch_id)

                # Process batch
                openai_client = manager.openai
                for i in range(100):
                    openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[
                            {"role": "user", "content": f"Batch item {i}"}
                        ],
                        is_batch=True,  # Apply batch pricing
                    )

                # Generate batch report
                batch_metrics = [
                    m
                    for m in mock_dynamo_with_data._stored_items
                    if m.get("batchId", {}).get("S") == batch_id
                ]

                assert len(batch_metrics) == 100

                # Calculate batch statistics
                total_tokens = sum(
                    int(m.get("totalTokens", {}).get("N", 0))
                    for m in batch_metrics
                )
                total_cost = sum(
                    float(m.get("costUSD", {}).get("N", 0))
                    for m in batch_metrics
                )
                avg_latency = sum(
                    int(m.get("latencyMs", {}).get("N", 0))
                    for m in batch_metrics
                ) / len(batch_metrics)

                # Create batch summary report
                report = {
                    "batch_id": batch_id,
                    "total_requests": len(batch_metrics),
                    "total_tokens": total_tokens,
                    "total_cost_usd": round(total_cost, 4),
                    "average_latency_ms": round(avg_latency, 2),
                    "cost_savings": round(
                        total_cost, 4
                    ),  # 50% discount for batch
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                # Verify batch pricing was applied
                # Each request should have batch pricing
                for metric in batch_metrics:
                    cost = float(metric.get("costUSD", {}).get("N", 0))
                    tokens = int(metric.get("totalTokens", {}).get("N", 0))
                    # Rough check that batch pricing is lower
                    assert (
                        cost < tokens * 0.000002
                    )  # Less than regular pricing

    @pytest.mark.integration
    def test_error_recovery_and_retry_logic(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test system behavior during errors and recovery."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)

        # Simulate intermittent failures
        call_count = {"count": 0}

        def mock_api_call(*args, **kwargs):
            call_count["count"] += 1
            if call_count["count"] % 3 == 0:  # Every 3rd call fails
                raise Exception("API temporarily unavailable")

            return ChatCompletion(
                id=f"chatcmpl-{call_count['count']}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content="Success",
                            role="assistant",
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-3.5-turbo",
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
            )

        mock_openai.chat.completions.create.side_effect = mock_api_call

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                success_count = 0
                error_count = 0

                # Make 10 API calls with retry logic
                for i in range(10):
                    for retry in range(3):  # Max 3 retries
                        try:
                            openai_client = manager.openai
                            response = openai_client.chat.completions.create(
                                model="gpt-3.5-turbo",
                                messages=[
                                    {"role": "user", "content": f"Request {i}"}
                                ],
                            )
                            success_count += 1
                            break
                        except Exception as e:
                            error_count += 1
                            if retry == 2:  # Last retry
                                print(f"Request {i} failed after 3 attempts")
                            else:
                                time.sleep(0.1)  # Brief delay before retry

                # Verify metrics were tracked for both successes and failures
                all_metrics = mock_dynamo_with_data._stored_items

                # Check error metrics
                error_metrics = [
                    m for m in all_metrics if m.get("error", {}).get("S")
                ]
                success_metrics = [
                    m for m in all_metrics if not m.get("error", {}).get("S")
                ]

                assert len(error_metrics) > 0  # Some errors were tracked
                assert len(success_metrics) > 0  # Some successes were tracked

                # Verify error details
                for error_metric in error_metrics:
                    assert (
                        "API temporarily unavailable"
                        in error_metric["error"]["S"]
                    )

    @pytest.mark.integration
    def test_concurrent_batch_processing(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test concurrent batch processing with proper isolation."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)

        # Thread-safe response generator
        import threading

        response_lock = threading.Lock()
        response_count = {"count": 0}

        def create_response():
            with response_lock:
                response_count["count"] += 1
                return ChatCompletion(
                    id=f"chatcmpl-concurrent-{response_count['count']}",
                    choices=[
                        Choice(
                            finish_reason="stop",
                            index=0,
                            message=ChatCompletionMessage(
                                content=f"Response {response_count['count']}",
                                role="assistant",
                            ),
                        )
                    ],
                    created=int(time.time()),
                    model="gpt-3.5-turbo",
                    object="chat.completion",
                    usage=CompletionUsage(
                        prompt_tokens=100,
                        completion_tokens=50,
                        total_tokens=150,
                    ),
                )

        mock_openai.chat.completions.create.side_effect = (
            lambda *args, **kwargs: create_response()
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):

                def process_batch(batch_id: str, num_items: int):
                    """Process a batch of items."""
                    # Create separate manager instance per thread
                    manager = ClientManager(config)
                    manager.set_tracking_context(batch_id=batch_id)

                    openai_client = manager.openai
                    for i in range(num_items):
                        openai_client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {
                                    "role": "user",
                                    "content": f"Batch {batch_id} item {i}",
                                }
                            ],
                        )

                    return batch_id

                # Process multiple batches concurrently
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = []
                    for i in range(5):
                        future = executor.submit(
                            process_batch,
                            f"concurrent-batch-{i}",
                            20,  # 20 items per batch
                        )
                        futures.append(future)

                    # Wait for completion
                    completed_batches = [f.result() for f in futures]

                # Verify all batches completed
                assert len(completed_batches) == 5

                # Verify total metrics
                assert (
                    len(mock_dynamo_with_data._stored_items) == 100
                )  # 5 batches * 20 items

                # Verify batch isolation
                for batch_num in range(5):
                    batch_id = f"concurrent-batch-{batch_num}"
                    batch_metrics = [
                        m
                        for m in mock_dynamo_with_data._stored_items
                        if m.get("batchId", {}).get("S") == batch_id
                    ]
                    assert (
                        len(batch_metrics) == 20
                    )  # Each batch has exactly 20 items

    @pytest.mark.integration
    def test_cost_threshold_monitoring(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test cost threshold monitoring and alerting."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)

        # Set up cost threshold
        cost_threshold = 1.0  # $1.00
        cost_alerts = []

        def check_cost_threshold(metric: AIUsageMetric):
            """Check if cost threshold is exceeded."""
            # Query total cost for the day
            today = metric.timestamp.strftime("%Y-%m-%d")
            today_metrics = [
                m
                for m in mock_dynamo_with_data._stored_items
                if m.get("date", {}).get("S") == today
            ]

            total_cost_today = sum(
                float(m.get("costUSD", {}).get("N", 0)) for m in today_metrics
            )

            if total_cost_today > cost_threshold:
                alert = {
                    "type": "COST_THRESHOLD_EXCEEDED",
                    "threshold": cost_threshold,
                    "current_cost": total_cost_today,
                    "date": today,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                cost_alerts.append(alert)
                return alert
            return None

        # Simulate expensive API calls
        expensive_responses = []
        for i in range(50):
            response = ChatCompletion(
                id=f"chatcmpl-expensive-{i}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content="Expensive response",
                            role="assistant",
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-4",  # More expensive model
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=1000,  # Large request
                    completion_tokens=500,
                    total_tokens=1500,
                ),
            )
            expensive_responses.append(response)

        mock_openai.chat.completions.create.side_effect = expensive_responses

        # Patch the tracker to include threshold checking
        original_store = AIUsageTracker._store_metric

        def store_with_threshold_check(self, metric):
            original_store(self, metric)
            check_cost_threshold(metric)

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                with patch.object(
                    AIUsageTracker, "_store_metric", store_with_threshold_check
                ):
                    manager = ClientManager(config)

                    # Make expensive API calls
                    openai_client = manager.openai
                    for i in range(50):
                        try:
                            openai_client.chat.completions.create(
                                model="gpt-4",
                                messages=[
                                    {
                                        "role": "user",
                                        "content": "Expensive request",
                                    }
                                ],
                            )
                        except:
                            pass  # Continue even if we hit limits

                    # Verify cost alerts were triggered
                    assert len(cost_alerts) > 0

                    # Check first alert
                    first_alert = cost_alerts[0]
                    assert first_alert["type"] == "COST_THRESHOLD_EXCEEDED"
                    assert first_alert["current_cost"] > cost_threshold

    @pytest.mark.integration
    def test_data_consistency_across_failures(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test data consistency when DynamoDB operations fail."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)

        # Track DynamoDB failures
        dynamo_failures = {"count": 0}
        original_put_item = mock_dynamo_with_data.put_item
        original_put_ai_usage_metric = (
            mock_dynamo_with_data.put_ai_usage_metric
        )

        def failing_put_item(**kwargs):
            dynamo_failures["count"] += 1
            if dynamo_failures["count"] % 4 == 0:  # Every 4th write fails
                raise Exception("DynamoDB write throttled")
            return original_put_item(**kwargs)

        def failing_put_ai_usage_metric(metric):
            dynamo_failures["count"] += 1
            if dynamo_failures["count"] % 4 == 0:  # Every 4th write fails
                raise Exception("DynamoDB write throttled")
            return original_put_ai_usage_metric(metric)

        mock_dynamo_with_data.put_item = MagicMock(
            side_effect=failing_put_item
        )
        mock_dynamo_with_data.put_ai_usage_metric = MagicMock(
            side_effect=failing_put_ai_usage_metric
        )

        # Create successful API responses
        responses = []
        for i in range(20):
            responses.append(
                create_mock_openai_response(
                    prompt_tokens=100 + i,
                    completion_tokens=50 + i,
                )
            )

        mock_openai.chat.completions.create.side_effect = responses

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)

                # Track successful API calls vs stored metrics
                api_call_count = 0

                openai_client = manager.openai
                for i in range(20):
                    try:
                        response = openai_client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {"role": "user", "content": f"Request {i}"}
                            ],
                        )
                        api_call_count += 1
                    except:
                        pass  # API call succeeded but metric storage might fail

                # Verify data consistency
                assert api_call_count == 20  # All API calls succeeded

                # Some metrics should be missing due to DynamoDB failures
                stored_count = len(mock_dynamo_with_data._stored_items)
                assert stored_count < api_call_count

                # Calculate lost metrics
                lost_metrics = api_call_count - stored_count
                assert (
                    lost_metrics == dynamo_failures["count"] // 4
                )  # Matches failure rate

    @pytest.mark.integration
    async def test_async_batch_processing(
        self, integration_env, mock_dynamo_with_data
    ):
        """Test asynchronous batch processing for high throughput."""
        config = ClientConfig.from_env()

        # Mock async OpenAI client
        mock_async_openai = AsyncMock()

        async def create_async_response(i):
            await asyncio.sleep(0.01)  # Simulate network delay
            return ChatCompletion(
                id=f"chatcmpl-async-{i}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content=f"Async response {i}",
                            role="assistant",
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-3.5-turbo",
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
            )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo_with_data,
        ):
            manager = ClientManager(config)

            async def process_async_batch():
                """Process batch of requests asynchronously."""
                tasks = []

                for i in range(100):
                    # Simulate async API call
                    task = create_async_response(i)
                    tasks.append(task)

                # Process all requests concurrently
                responses = await asyncio.gather(*tasks)

                # Track metrics for each response
                for i, response in enumerate(responses):
                    metric = AIUsageMetric(
                        service="openai",
                        model="gpt-3.5-turbo",
                        operation="completion",
                        timestamp=datetime.now(timezone.utc),
                        input_tokens=100,
                        output_tokens=50,
                        total_tokens=150,
                        cost_usd=AICostCalculator.calculate_openai_cost(
                            model="gpt-3.5-turbo",
                            input_tokens=100,
                            output_tokens=50,
                        ),
                        latency_ms=10,  # Async is fast!
                        user_id="async-test-user",
                        metadata={"async": True, "batch_index": i},
                    )
                    manager.usage_tracker._store_metric(metric)

                return len(responses)

            # Run async batch processing
            start_time = time.time()
            processed = await process_async_batch()
            elapsed = time.time() - start_time

            # Verify high throughput
            assert processed == 100
            assert elapsed < 2.0  # Should complete quickly due to async

            # Verify all metrics stored
            assert len(mock_dynamo_with_data._stored_items) == 100

            # Check async metadata
            async_metrics = [
                m
                for m in mock_dynamo_with_data._stored_items
                if m.get("metadata", {})
                .get("M", {})
                .get("async", {})
                .get("BOOL")
            ]
            assert len(async_metrics) == 100
