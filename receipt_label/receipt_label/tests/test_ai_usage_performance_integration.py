"""
Performance integration tests for AI Usage Tracking system.

Tests system behavior under load, stress conditions, and edge cases.
"""

import gc
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice, CompletionUsage
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

# Add the parent directory to the path to access the tests utils
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.client_manager import ClientConfig, ClientManager
from receipt_label.utils.cost_calculator import AICostCalculator
from tests.utils.ai_usage_helpers import (
    MockServiceFactory,
    create_mock_openai_response,
)


@pytest.fixture
def performance_env():
    """Environment for performance testing."""
    env_vars = {
        "DYNAMODB_TABLE_NAME": "perf-test-table",
        "OPENAI_API_KEY": "test-key",
        "PINECONE_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test.pinecone.io",
        "TRACK_AI_USAGE": "true",
        "USER_ID": "perf-test-user",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def mock_high_performance_dynamo():
    """Mock DynamoDB client optimized for performance testing."""
    client = MagicMock(spec=DynamoClient)
    client.table_name = "perf-test-table"

    # High-performance storage
    import threading

    client._storage_lock = threading.Lock()
    client._stored_items = []
    client._write_latencies = []

    def fast_put_item(**kwargs):
        start = time.perf_counter()
        with client._storage_lock:
            client._stored_items.append(kwargs["Item"])
        latency = (time.perf_counter() - start) * 1000  # ms
        client._write_latencies.append(latency)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def fast_put_metric(metric):
        """Store AIUsageMetric for new resilient client interface."""
        start = time.perf_counter()
        item = metric.to_dynamodb_item()
        with client._storage_lock:
            client._stored_items.append(item)
        latency = (time.perf_counter() - start) * 1000  # ms
        client._write_latencies.append(latency)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    client.put_item = MagicMock(side_effect=fast_put_item)
    client.put_ai_usage_metric = MagicMock(side_effect=fast_put_metric)

    def fast_query(**kwargs):
        with client._storage_lock:
            # Simple in-memory query simulation
            return {
                "Items": client._stored_items[:100]
            }  # Limit for performance

    client.query = MagicMock(side_effect=fast_query)

    return client


@pytest.mark.integration
@pytest.mark.performance
class TestAIUsagePerformanceIntegration:
    """Performance tests for AI usage tracking system."""

    def test_high_throughput_tracking(
        self, performance_env, mock_high_performance_dynamo
    ):
        """Test system can handle high throughput of API calls."""
        config = ClientConfig.from_env()

        # Mock OpenAI with minimal latency
        mock_openai = MagicMock(spec=OpenAI)

        def create_fast_response(i):
            return ChatCompletion(
                id=f"perf-{i}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content=f"Response {i}", role="assistant"
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-3.5-turbo",
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )

        responses = [create_fast_response(i) for i in range(1000)]
        mock_openai.chat.completions.create.side_effect = responses

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_high_performance_dynamo,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)
                openai_client = manager.openai

                # Measure throughput
                start_time = time.perf_counter()

                for i in range(1000):
                    openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": f"Message {i}"}],
                    )

                elapsed = time.perf_counter() - start_time

                # Calculate metrics
                throughput = 1000 / elapsed  # requests per second
                avg_latency = statistics.mean(
                    mock_high_performance_dynamo._write_latencies
                )
                p99_latency = statistics.quantiles(
                    mock_high_performance_dynamo._write_latencies, n=100
                )[98]

                # Performance assertions using environment-aware thresholds
                from receipt_label.tests.utils.performance_utils import (
                    AdaptiveThresholds,
                    EnvironmentProfile,
                )

                thresholds = AdaptiveThresholds()
                perf_class = EnvironmentProfile.get_performance_class()

                # Adjust expectations based on environment
                min_throughput = thresholds.get_threshold("throughput_ops")
                max_avg_latency = thresholds.get_threshold("latency_ms")
                max_p99_latency = max_avg_latency * 5  # P99 can be 5x average

                # Log environment info for debugging
                print(f"Environment: {perf_class}")
                print(f"Expected min throughput: {min_throughput:.0f} req/s")

                assert (
                    throughput > min_throughput
                ), f"Throughput {throughput:.2f} req/s below threshold {min_throughput:.0f} req/s"
                assert (
                    avg_latency < max_avg_latency
                ), f"Avg latency {avg_latency:.2f}ms exceeds threshold {max_avg_latency:.2f}ms"
                assert (
                    p99_latency < max_p99_latency
                ), f"P99 latency {p99_latency:.2f}ms exceeds threshold {max_p99_latency:.2f}ms"

                # Verify all metrics stored
                assert len(mock_high_performance_dynamo._stored_items) == 1000

                print(f"Performance Metrics:")
                print(f"  Throughput: {throughput:.2f} req/s")
                print(f"  Avg Latency: {avg_latency:.2f} ms")
                print(f"  P99 Latency: {p99_latency:.2f} ms")

    def test_concurrent_load_handling(
        self, performance_env, mock_high_performance_dynamo
    ):
        """Test system under concurrent load from multiple threads."""
        config = ClientConfig.from_env()

        # Shared mock OpenAI
        mock_openai = MagicMock(spec=OpenAI)

        import threading

        call_counter = {"count": 0}
        counter_lock = threading.Lock()

        def thread_safe_response(*args, **kwargs):
            with counter_lock:
                call_counter["count"] += 1
                count = call_counter["count"]

            return ChatCompletion(
                id=f"concurrent-{count}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content=f"Response {count}", role="assistant"
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-3.5-turbo",
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=50, completion_tokens=25, total_tokens=75
                ),
            )

        mock_openai.chat.completions.create = thread_safe_response

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_high_performance_dynamo,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):

                def worker_task(worker_id: int, num_requests: int):
                    """Worker function for concurrent testing."""
                    manager = ClientManager(config)
                    manager.set_tracking_context(job_id=f"worker-{worker_id}")

                    openai_client = manager.openai
                    latencies = []

                    for i in range(num_requests):
                        start = time.perf_counter()
                        openai_client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {
                                    "role": "user",
                                    "content": f"Worker {worker_id} msg {i}",
                                }
                            ],
                        )
                        latency = (time.perf_counter() - start) * 1000
                        latencies.append(latency)

                    return {
                        "worker_id": worker_id,
                        "requests": num_requests,
                        "avg_latency": statistics.mean(latencies),
                        "max_latency": max(latencies),
                    }

                # Run concurrent workers
                num_workers = 10
                requests_per_worker = 100

                start_time = time.perf_counter()

                with ThreadPoolExecutor(max_workers=num_workers) as executor:
                    futures = [
                        executor.submit(worker_task, i, requests_per_worker)
                        for i in range(num_workers)
                    ]

                    results = [f.result() for f in as_completed(futures)]

                total_elapsed = time.perf_counter() - start_time

                # Analyze results
                total_requests = num_workers * requests_per_worker
                overall_throughput = total_requests / total_elapsed
                avg_worker_latency = statistics.mean(
                    r["avg_latency"] for r in results
                )
                max_worker_latency = max(r["max_latency"] for r in results)

                # Performance assertions with environment awareness
                from receipt_label.tests.utils.performance_utils import (
                    AdaptiveThresholds,
                    EnvironmentProfile,
                )

                thresholds = AdaptiveThresholds()
                perf_class = EnvironmentProfile.get_performance_class()

                # Concurrent operations should achieve better throughput
                min_concurrent_throughput = (
                    thresholds.get_threshold("throughput_ops") * 1.5
                )
                # IMPORTANT: These thresholds are environment-dependent
                # CI environments are less performant than local development machines
                # Concurrent operations experience higher latency due to contention
                max_avg_latency = (
                    thresholds.get_threshold("latency_ms") * 5
                )  # More lenient for concurrent operations in CI
                max_peak_latency = (
                    max_avg_latency * 7
                )  # Peak can be much higher under concurrent load in CI

                print(f"Concurrent performance (env: {perf_class}):")
                print(f"  Overall throughput: {overall_throughput:.1f} req/s")
                print(f"  Avg worker latency: {avg_worker_latency:.1f}ms")
                print(f"  Max worker latency: {max_worker_latency:.1f}ms")

                assert (
                    overall_throughput > min_concurrent_throughput
                ), f"Concurrent throughput {overall_throughput:.1f} below threshold {min_concurrent_throughput:.1f}"
                assert (
                    avg_worker_latency < max_avg_latency
                ), f"Avg latency {avg_worker_latency:.1f}ms exceeds threshold {max_avg_latency:.1f}ms"
                assert (
                    max_worker_latency < max_peak_latency
                ), f"Peak latency {max_worker_latency:.1f}ms exceeds threshold {max_peak_latency:.1f}ms"

                # Verify data integrity
                assert (
                    len(mock_high_performance_dynamo._stored_items)
                    == total_requests
                )

                # Check job isolation
                for worker_id in range(num_workers):
                    worker_metrics = [
                        m
                        for m in mock_high_performance_dynamo._stored_items
                        if m.get("jobId", {}).get("S") == f"worker-{worker_id}"
                    ]
                    assert len(worker_metrics) == requests_per_worker

    def test_memory_efficiency_under_load(
        self, performance_env, mock_high_performance_dynamo
    ):
        """Test system memory efficiency during sustained load."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)

        # Simple response generator
        mock_openai.chat.completions.create.return_value = ChatCompletion(
            id="memory-test",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(
                        content="Response", role="assistant"
                    ),
                )
            ],
            created=int(time.time()),
            model="gpt-3.5-turbo",
            object="chat.completion",
            usage=CompletionUsage(
                prompt_tokens=100, completion_tokens=50, total_tokens=150
            ),
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_high_performance_dynamo,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)
                openai_client = manager.openai

                # Measure memory before load
                gc.collect()
                try:
                    import psutil
                except ImportError:
                    pytest.skip("psutil not installed")

                process = psutil.Process()
                memory_before = process.memory_info().rss / 1024 / 1024  # MB

                # Sustained load test
                batch_size = 1000
                num_batches = 10

                for batch in range(num_batches):
                    for i in range(batch_size):
                        openai_client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {
                                    "role": "user",
                                    "content": f"Batch {batch} msg {i}",
                                }
                            ],
                        )

                    # Force garbage collection between batches
                    gc.collect()

                # Measure memory after load
                memory_after = process.memory_info().rss / 1024 / 1024  # MB
                memory_increase = memory_after - memory_before

                # Memory efficiency assertions with environment awareness
                from receipt_label.tests.utils.performance_utils import (
                    AdaptiveThresholds,
                    EnvironmentProfile,
                )

                thresholds = AdaptiveThresholds()
                perf_class = EnvironmentProfile.get_performance_class()
                max_memory_increase = thresholds.get_threshold("memory_mb")

                # Calculate total operations
                total_operations = batch_size * num_batches

                # Also check relative memory usage
                memory_per_operation = (
                    memory_increase / total_operations * 1000
                )  # MB per 1k ops

                print(f"Memory usage (env: {perf_class}):")
                print(f"  Total increase: {memory_increase:.1f}MB")
                print(f"  Per 1k operations: {memory_per_operation:.2f}MB")
                print(f"  Threshold: {max_memory_increase:.0f}MB")

                assert (
                    memory_increase < max_memory_increase
                ), f"Memory increase {memory_increase:.1f}MB exceeds threshold {max_memory_increase:.0f}MB (env: {perf_class})"

                # Relative assertion: memory per operation should be reasonable
                # Allow up to 20MB per 1k operations (accounts for Python object overhead)
                assert (
                    memory_per_operation < 20
                ), f"Memory per 1k operations {memory_per_operation:.1f}MB seems excessive"

                # Verify operations completed
                assert (
                    len(mock_high_performance_dynamo._stored_items)
                    >= total_operations * 0.95
                )  # Allow 5% loss

    def test_burst_traffic_handling(
        self, performance_env, mock_high_performance_dynamo
    ):
        """Test system behavior during traffic bursts."""
        config = ClientConfig.from_env()

        mock_openai = MagicMock(spec=OpenAI)

        # Variable latency to simulate real conditions
        def variable_latency_response(*args, **kwargs):
            import random

            time.sleep(random.uniform(0.001, 0.01))  # 1-10ms

            return ChatCompletion(
                id=f"burst-{time.time_ns()}",
                choices=[
                    Choice(
                        finish_reason="stop",
                        index=0,
                        message=ChatCompletionMessage(
                            content="Burst response", role="assistant"
                        ),
                    )
                ],
                created=int(time.time()),
                model="gpt-3.5-turbo",
                object="chat.completion",
                usage=CompletionUsage(
                    prompt_tokens=100, completion_tokens=50, total_tokens=150
                ),
            )

        mock_openai.chat.completions.create.side_effect = (
            variable_latency_response
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_high_performance_dynamo,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)
                openai_client = manager.openai

                # Simulate traffic pattern: steady -> burst -> steady
                phases = [
                    ("steady", 10, 0.1),  # 10 requests, 100ms apart
                    ("burst", 100, 0.001),  # 100 requests, 1ms apart
                    ("steady", 10, 0.1),  # Back to steady
                ]

                phase_metrics = {}

                for phase_name, count, delay in phases:
                    phase_start = time.perf_counter()
                    latencies = []

                    for i in range(count):
                        req_start = time.perf_counter()
                        openai_client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {
                                    "role": "user",
                                    "content": f"{phase_name} {i}",
                                }
                            ],
                        )
                        latencies.append(
                            (time.perf_counter() - req_start) * 1000
                        )

                        if i < count - 1:
                            time.sleep(delay)

                    phase_duration = time.perf_counter() - phase_start
                    phase_metrics[phase_name] = {
                        "duration": phase_duration,
                        "throughput": count / phase_duration,
                        "avg_latency": statistics.mean(latencies),
                        "p99_latency": (
                            statistics.quantiles(latencies, n=100)[98]
                            if len(latencies) > 1
                            else latencies[0]
                        ),
                    }

                # Verify burst handling
                burst_metrics = phase_metrics["burst"]
                assert (
                    burst_metrics["throughput"] > 50
                )  # Handle at least 50 req/s during burst
                assert (
                    burst_metrics["p99_latency"] < 100
                )  # Maintain reasonable latency

                # System should recover after burst
                steady_after = phase_metrics["steady"]
                assert steady_after["avg_latency"] < 50  # Back to normal

    def test_query_performance_with_large_dataset(
        self, performance_env, mock_high_performance_dynamo
    ):
        """Test query performance with large amounts of historical data."""
        config = ClientConfig.from_env()

        # Pre-populate with historical data
        base_time = datetime.now(timezone.utc) - timedelta(days=30)

        # Generate 10,000 historical metrics
        for day in range(30):
            date = base_time + timedelta(days=day)
            date_str = date.strftime("%Y-%m-%d")

            for hour in range(24):
                for i in range(14):  # ~14 requests per hour = 10,080 total
                    timestamp = date.replace(hour=hour, minute=i * 4)

                    metric_item = {
                        "PK": {"S": f"AI_USAGE#openai#gpt-3.5-turbo"},
                        "SK": {"S": f"USAGE#{timestamp.isoformat()}#{i}"},
                        "GSI1PK": {"S": "AI_USAGE#openai"},
                        "GSI1SK": {"S": f"DATE#{date_str}"},
                        "GSI2PK": {"S": "AI_USAGE_COST"},
                        "GSI2SK": {"S": f"COST#{date_str}#openai"},
                        "service": {"S": "openai"},
                        "model": {"S": "gpt-3.5-turbo"},
                        "operation": {"S": "completion"},
                        "timestamp": {"S": timestamp.isoformat()},
                        "requestId": {"S": f"req-{day}-{hour}-{i}"},
                        "apiCalls": {"N": "1"},
                        "date": {"S": date_str},
                        "totalTokens": {"N": str(100 + i)},
                        "costUSD": {"N": str(0.0001 * (100 + i))},
                    }
                    mock_high_performance_dynamo._stored_items.append(
                        metric_item
                    )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_high_performance_dynamo,
        ):
            manager = ClientManager(config)

            # Test various query patterns
            query_patterns = [
                # Single day query
                ("single_day", "2024-01-15", "2024-01-15"),
                # Week query
                ("week", "2024-01-08", "2024-01-14"),
                # Month query
                ("month", "2024-01-01", "2024-01-31"),
            ]

            query_performance = {}

            for pattern_name, start_date, end_date in query_patterns:
                start = time.perf_counter()

                metrics = AIUsageMetric.query_by_service_date(
                    manager.dynamo,
                    service="openai",
                    start_date=start_date,
                    end_date=end_date,
                )

                query_time = (time.perf_counter() - start) * 1000  # ms

                # Calculate aggregations
                agg_start = time.perf_counter()

                total_cost = sum(
                    float(m.get("costUSD", {}).get("N", 0))
                    for m in mock_high_performance_dynamo._stored_items
                    if start_date <= m.get("date", {}).get("S", "") <= end_date
                )

                agg_time = (time.perf_counter() - agg_start) * 1000  # ms

                query_performance[pattern_name] = {
                    "query_time_ms": query_time,
                    "aggregation_time_ms": agg_time,
                    "total_time_ms": query_time + agg_time,
                    "result_count": len(metrics),
                }

            # Performance assertions using relative thresholds
            from receipt_label.tests.utils.performance_utils import (
                EnvironmentProfile,
            )

            # Establish baseline with simplest query
            baseline_time = query_performance["single_day"]["total_time_ms"]
            perf_class = EnvironmentProfile.get_performance_class()

            # Scale expectations based on environment
            scaling_factor = EnvironmentProfile.get_scaling_factor()
            base_single_day_ms = 50 / scaling_factor  # Adjust base expectation

            # Relative assertions - week should be < 3x single day, month < 10x
            assert (
                query_performance["single_day"]["total_time_ms"]
                < base_single_day_ms
            ), f"Single day query {query_performance['single_day']['total_time_ms']:.1f}ms exceeds threshold {base_single_day_ms:.1f}ms (env: {perf_class})"

            assert (
                query_performance["week"]["total_time_ms"] < baseline_time * 3
            ), f"Week query should be < 3x single day query time"

            assert (
                query_performance["month"]["total_time_ms"]
                < baseline_time * 10
            ), f"Month query should be < 10x single day query time"

            print(f"Query performance (env: {perf_class}):")
            for pattern, perf in query_performance.items():
                print(
                    f"  {pattern}: {perf['total_time_ms']:.1f}ms ({perf['result_count']} results)"
                )

    def test_graceful_degradation_under_stress(self, performance_env):
        """Test system degrades gracefully under extreme stress."""
        config = ClientConfig.from_env()

        # Mock components with failure scenarios
        mock_dynamo = MagicMock(spec=DynamoClient)
        mock_dynamo.table_name = "stress-test-table"

        # Simulate increasing latency and failures
        dynamo_state = {"call_count": 0, "failure_rate": 0.0}

        def stressed_put_item(**kwargs):
            dynamo_state["call_count"] += 1

            # Increase failure rate as load increases
            if dynamo_state["call_count"] > 500:
                dynamo_state["failure_rate"] = 0.1  # 10% failures
            if dynamo_state["call_count"] > 1000:
                dynamo_state["failure_rate"] = 0.25  # 25% failures

            import random

            if random.random() < dynamo_state["failure_rate"]:
                raise Exception("DynamoDB throttled")

            # Simulate increasing latency
            base_latency = 0.001
            if dynamo_state["call_count"] > 500:
                base_latency = 0.01
            if dynamo_state["call_count"] > 1000:
                base_latency = 0.05

            time.sleep(base_latency)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        mock_dynamo.put_item = MagicMock(side_effect=stressed_put_item)

        mock_openai = MockServiceFactory.create_openai_client(
            completion_response=create_mock_openai_response()
        )

        with patch(
            "receipt_label.utils.client_manager.DynamoClient",
            return_value=mock_dynamo,
        ):
            with patch(
                "receipt_label.utils.client_manager.OpenAI",
                return_value=mock_openai,
            ):
                manager = ClientManager(config)
                openai_client = manager.openai

                # Track success/failure rates over time
                windows = []
                window_size = 100

                for window_num in range(20):  # 2000 total requests
                    window_start = time.perf_counter()
                    successes = 0
                    failures = 0

                    for i in range(window_size):
                        try:
                            openai_client.chat.completions.create(
                                model="gpt-3.5-turbo",
                                messages=[
                                    {
                                        "role": "user",
                                        "content": f"Stress test {window_num}-{i}",
                                    }
                                ],
                            )
                            successes += 1
                        except:
                            failures += 1

                    window_duration = time.perf_counter() - window_start
                    success_rate = successes / window_size

                    windows.append(
                        {
                            "window": window_num,
                            "success_rate": success_rate,
                            "throughput": window_size / window_duration,
                            "avg_latency": window_duration
                            / window_size
                            * 1000,
                        }
                    )

                # Verify graceful degradation
                early_windows = windows[:5]
                late_windows = windows[-5:]

                # Success rate should degrade but not catastrophically
                early_success = statistics.mean(
                    w["success_rate"] for w in early_windows
                )
                late_success = statistics.mean(
                    w["success_rate"] for w in late_windows
                )

                assert early_success > 0.95  # Nearly perfect when not stressed
                assert late_success > 0.70  # Still functional under stress

                # Throughput should decrease but remain reasonable
                early_throughput = statistics.mean(
                    w["throughput"] for w in early_windows
                )
                late_throughput = statistics.mean(
                    w["throughput"] for w in late_windows
                )

                # IMPORTANT: These thresholds are environment-dependent
                # CI environments are less performant than local development machines
                # Values tuned for GitHub Actions CI environment performance
                # With resilient tracker, we should maintain at least 3% throughput (CI-tuned)
                expected_throughput_ratio = (
                    0.03 if config.use_resilient_tracker else 0.01
                )
                assert (
                    late_throughput
                    > early_throughput * expected_throughput_ratio
                )  # Resilient tracker should maintain better throughput under stress

    def test_resilient_tracker_maintains_throughput(self, performance_env):
        """Test that resilient tracker maintains >3% throughput under stress (CI-tuned)."""
        # Force use of resilient tracker
        os.environ["USE_RESILIENT_TRACKER"] = "true"
        os.environ["CIRCUIT_BREAKER_THRESHOLD"] = "5"
        os.environ["CIRCUIT_BREAKER_TIMEOUT"] = (
            "2.0"  # Short timeout for testing
        )
        os.environ["MAX_RETRY_ATTEMPTS"] = "3"
        os.environ["RETRY_BASE_DELAY"] = "0.1"  # Short delay for testing
        os.environ["BATCH_SIZE"] = "10"
        os.environ["BATCH_FLUSH_INTERVAL"] = "1.0"

        config = ClientConfig.from_env()
        assert config.use_resilient_tracker is True

        # Mock components with failure scenarios
        from receipt_dynamo import ResilientDynamoClient

        mock_dynamo = MagicMock(spec=ResilientDynamoClient)
        mock_dynamo.table_name = "resilient-test-table"

        # Add resilient features to mock
        mock_dynamo.circuit_state = "closed"
        mock_dynamo.failure_count = 0
        mock_dynamo.enable_batch_processing = True
        mock_dynamo.metric_queue = []

        # Track actual DynamoDB calls
        dynamo_calls = {"put_item": 0, "batch_write_item": 0}

        def tracked_put_item(**kwargs):
            dynamo_calls["put_item"] += 1

            # Simulate failures that increase over time
            if dynamo_calls["put_item"] > 20:
                if dynamo_calls["put_item"] % 5 == 0:  # Every 5th call fails
                    raise Exception("DynamoDB throttled")

            # Minimal latency to simulate real DynamoDB
            time.sleep(0.001)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        def tracked_batch_write(**kwargs):
            dynamo_calls["batch_write_item"] += 1

            # Batch writes have lower failure rate
            if dynamo_calls["batch_write_item"] > 10:
                if dynamo_calls["batch_write_item"] % 10 == 0:
                    raise Exception("Batch write throttled")

            return {"UnprocessedItems": {}}

        mock_dynamo.put_item = MagicMock(side_effect=tracked_put_item)
        mock_dynamo.batch_write_item = MagicMock(
            side_effect=tracked_batch_write
        )

        mock_openai = MockServiceFactory.create_openai_client(
            completion_response=create_mock_openai_response()
        )

        with patch(
            "receipt_label.utils.ai_usage_tracker_resilient.ResilientDynamoClient",
            return_value=mock_dynamo,
        ):
            with patch(
                "receipt_label.utils.client_manager.DynamoClient",
                return_value=mock_dynamo,
            ):
                with patch(
                    "receipt_label.utils.client_manager.OpenAI",
                    return_value=mock_openai,
                ):
                    manager = ClientManager(config)
                    openai_client = manager.openai

                    # Track success/failure over time
                    results = []
                    start_time = time.perf_counter()

                    # Run 500 requests
                    for i in range(500):
                        request_start = time.perf_counter()
                        try:
                            openai_client.chat.completions.create(
                                model="gpt-3.5-turbo",
                                messages=[
                                    {"role": "user", "content": f"Test {i}"}
                                ],
                            )
                            # Exclude the OpenAI mock latency from throughput measurement
                            # We only care about tracker overhead
                            request_end = time.perf_counter()
                            results.append(
                                {
                                    "success": True,
                                    "time": request_end,
                                    "tracker_time": request_end
                                    - request_start
                                    - 0.001,  # Subtract mock latency
                                }
                            )
                        except Exception as e:
                            request_end = time.perf_counter()
                            results.append(
                                {
                                    "success": False,
                                    "time": request_end,
                                    "tracker_time": request_end
                                    - request_start,
                                }
                            )

                    total_time = time.perf_counter() - start_time

                    # Calculate metrics
                    successful_requests = sum(
                        1 for r in results if r["success"]
                    )
                    success_rate = successful_requests / len(results)
                    throughput = len(results) / total_time

                    # Verify resilience features worked
                    tracker = manager.usage_tracker
                    if hasattr(tracker, "get_stats"):
                        stats = tracker.get_stats()
                        print(f"Tracker stats: {stats}")

                        # Should have used batch processing
                        assert stats.get("batch_queue") is not None

                        # Circuit breaker should have helped
                        cb_state = stats.get("circuit_breaker_state", {})
                        assert cb_state.get("state") in [
                            "closed",
                            "half_open",
                            "open",
                        ]

                    # Should maintain high success rate
                    assert (
                        success_rate > 0.85
                    ), f"Success rate too low: {success_rate:.2%}"

                    # Calculate throughput degradation
                    # First 100 requests (baseline)
                    early_results = results[:100]
                    early_time = (
                        early_results[-1]["time"] - early_results[0]["time"]
                    )
                    early_throughput = 100 / early_time

                    # Last 100 requests (under stress)
                    late_results = results[-100:]
                    late_time = (
                        late_results[-1]["time"] - late_results[0]["time"]
                    )
                    late_throughput = 100 / late_time

                    throughput_ratio = late_throughput / early_throughput

                    # Should maintain at least 10% of original throughput
                    assert throughput_ratio > 0.10, (
                        f"Throughput degraded too much: {throughput_ratio:.2%} "
                        f"(early: {early_throughput:.1f} req/s, late: {late_throughput:.1f} req/s)"
                    )

                    # Verify batch processing reduced DynamoDB calls
                    total_dynamo_calls = (
                        dynamo_calls["put_item"]
                        + dynamo_calls["batch_write_item"]
                    )
                    calls_per_request = total_dynamo_calls / len(results)

                    # With batching, should have fewer than 1 DynamoDB call per request
                    assert calls_per_request < 0.5, (
                        f"Too many DynamoDB calls: {calls_per_request:.2f} per request "
                        f"(put_item: {dynamo_calls['put_item']}, batch: {dynamo_calls['batch_write_item']})"
                    )
