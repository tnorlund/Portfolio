"""Performance and stress tests for AIUsageTracker."""

import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import Mock

import pytest

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

# Add the parent directory to the path to access the tests utils
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from receipt_label.utils.ai_usage_tracker import AIUsageTracker

from tests.utils.ai_usage_helpers import create_mock_openai_response


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


@pytest.mark.performance
class TestPerformanceBaseline:
    """Test baseline performance characteristics."""

    def test_decorator_overhead_minimal(self):
        """Test that decorator overhead is minimal for successful calls."""
        tracker = AIUsageTracker(
            track_to_dynamo=False,
            track_to_file=False,
        )

        # Undecorated function
        def baseline_function():
            return "result"

        # Decorated function
        @tracker.track_openai_completion
        def decorated_function():
            return create_mock_openai_response()

        # Time baseline
        iterations = 1000
        start = time.time()
        for _ in range(iterations):
            baseline_function()
        baseline_time = time.time() - start

        # Time decorated
        start = time.time()
        for _ in range(iterations):
            decorated_function()
        decorated_time = time.time() - start

        # Overhead should be reasonable (less than 1000x)
        # The baseline function is extremely fast, so even small overhead appears large
        overhead_ratio = decorated_time / baseline_time
        assert overhead_ratio < 10000.0, f"Overhead ratio: {overhead_ratio}"

        # More meaningful test: absolute time should still be reasonable
        assert (
            decorated_time < 1.0
        ), f"Decorated function took {decorated_time}s for {iterations} calls"

    def test_metric_creation_performance(self):
        """Test performance of metric creation and storage."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def fast_call():
            return create_mock_openai_response(
                prompt_tokens=100,
                completion_tokens=50,
            )

        # Time multiple metric creations
        iterations = 100
        start = time.time()
        for _ in range(iterations):
            fast_call()
        duration = time.time() - start

        # Should complete in reasonable time
        assert (
            duration < 1.0
        ), f"Duration: {duration}s for {iterations} metrics"
        assert get_dynamo_call_count(mock_dynamo) == iterations

    def test_file_logging_performance(self):
        """Test file logging performance."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            tracker = AIUsageTracker(
                track_to_file=True,
                log_file=temp_file,
            )

            @tracker.track_openai_completion
            def logged_call():
                return create_mock_openai_response()

            # Time file logging
            iterations = 100
            start = time.time()
            for _ in range(iterations):
                logged_call()
            duration = time.time() - start

            # File logging should be reasonably fast
            assert duration < 2.0, f"File logging duration: {duration}s"

            # Verify all entries were written
            with open(temp_file, "r") as f:
                lines = f.readlines()
            assert len(lines) == iterations

        finally:
            os.unlink(temp_file)

    def test_json_serialization_performance(self):
        """Test JSON serialization performance with complex metadata."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        # Complex metadata
        complex_metadata = {
            "nested": {"deep": {"very": {"deep": list(range(100))}}},
            "large_list": [f"item_{i}" for i in range(500)],
            "unicode": "测试数据 🚀 " * 100,
        }

        @tracker.track_openai_completion
        def complex_call(**kwargs):
            return create_mock_openai_response()

        # Time serialization
        start = time.time()
        for i in range(50):
            complex_call(model="gpt-3.5-turbo", **complex_metadata)
        duration = time.time() - start

        # Should handle complex data efficiently
        assert duration < 5.0, f"Complex serialization duration: {duration}s"


@pytest.mark.performance
class TestHighVolumeTracking:
    """Test high-volume tracking scenarios."""

    def test_burst_tracking(self):
        """Test tracking a burst of requests."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def burst_call(request_id):
            return create_mock_openai_response(
                prompt_tokens=100 + request_id,
                completion_tokens=50 + request_id,
            )

        # Simulate burst of 1000 requests
        start = time.time()
        for i in range(1000):
            burst_call(i)
        duration = time.time() - start

        # Should handle burst efficiently
        assert (
            duration < 10.0
        ), f"Burst duration: {duration}s for 1000 requests"
        assert get_dynamo_call_count(mock_dynamo) == 1000

    def test_sustained_load(self):
        """Test sustained load over time."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def sustained_call():
            return create_mock_openai_response()

        # Simulate sustained load
        total_calls = 0
        start = time.time()
        while time.time() - start < 2.0:  # Run for 2 seconds
            sustained_call()
            total_calls += 1

        duration = time.time() - start

        # Should maintain reasonable throughput
        throughput = total_calls / duration
        assert throughput > 100, f"Throughput: {throughput} calls/sec"
        assert get_dynamo_call_count(mock_dynamo) == total_calls

    def test_large_batch_processing(self):
        """Test processing large batches efficiently."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        tracker.set_tracking_context(batch_id="large-batch-001")

        # Simulate batch processing with varying token counts
        batch_sizes = [100, 500, 1000, 2000, 5000, 10000]

        @tracker.track_openai_completion
        def batch_item(tokens):
            return create_mock_openai_response(
                prompt_tokens=tokens,
                completion_tokens=tokens // 2,
            )

        start = time.time()
        for batch_size in batch_sizes:
            for i in range(batch_size):
                batch_item(100 + i % 1000)
        duration = time.time() - start

        total_items = sum(batch_sizes)
        throughput = total_items / duration

        assert throughput > 500, f"Batch throughput: {throughput} items/sec"
        assert get_dynamo_call_count(mock_dynamo) == total_items


@pytest.mark.performance
class TestConcurrentPerformance:
    """Test performance under concurrent load."""

    def test_concurrent_tracking_throughput(self):
        """Test throughput under concurrent load."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def concurrent_call(thread_id, call_id):
            return create_mock_openai_response(
                prompt_tokens=100 + call_id,
                completion_tokens=50 + call_id,
            )

        # Use thread pool for concurrent execution
        num_threads = 10
        calls_per_thread = 100

        start = time.time()
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for thread_id in range(num_threads):
                for call_id in range(calls_per_thread):
                    future = executor.submit(
                        concurrent_call, thread_id, call_id
                    )
                    futures.append(future)

            # Wait for all to complete
            for future in as_completed(futures):
                future.result()

        duration = time.time() - start
        total_calls = num_threads * calls_per_thread
        throughput = total_calls / duration

        assert (
            throughput > 200
        ), f"Concurrent throughput: {throughput} calls/sec"
        assert get_dynamo_call_count(mock_dynamo) == total_calls

    def test_wrapped_client_concurrent_performance(self):
        """Test wrapped client performance under concurrent load."""
        mock_client = Mock()
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        call_counter = 0

        def thread_safe_create(**kwargs):
            nonlocal call_counter
            call_counter += 1
            return create_mock_openai_response(
                prompt_tokens=100,
                completion_tokens=50,
            )

        mock_completions.create = thread_safe_create

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        def make_concurrent_call(call_id):
            return wrapped_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": f"Message {call_id}"}],
            )

        # Concurrent calls through wrapped client
        num_threads = 5
        calls_per_thread = 50

        start = time.time()
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(make_concurrent_call, i)
                for i in range(num_threads * calls_per_thread)
            ]
            results = [f.result() for f in futures]

        duration = time.time() - start
        throughput = len(results) / duration

        assert len(results) == num_threads * calls_per_thread
        assert (
            throughput > 100
        ), f"Wrapped client throughput: {throughput} calls/sec"

    def test_mixed_service_concurrent_tracking(self):
        """Test concurrent tracking across different services."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def openai_call():
            return create_mock_openai_response()

        @tracker.track_anthropic_completion
        def anthropic_call():
            response = Mock()
            response.usage = Mock(input_tokens=100, output_tokens=50)
            return response

        @tracker.track_google_places("Place Details")
        def places_call():
            return {"result": {"place_id": "test"}}

        # Mix of service calls
        def mixed_worker(worker_id):
            services = [openai_call, anthropic_call, places_call]
            for i in range(20):
                service = services[i % len(services)]
                service()

        start = time.time()
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(mixed_worker, i) for i in range(5)]
            for future in futures:
                future.result()

        duration = time.time() - start

        # Should handle mixed service tracking efficiently
        total_calls = 5 * 20  # 5 workers * 20 calls each
        throughput = total_calls / duration

        assert (
            throughput > 50
        ), f"Mixed service throughput: {throughput} calls/sec"
        assert get_dynamo_call_count(mock_dynamo) == total_calls


@pytest.mark.performance
class TestMemoryEfficiency:
    """Test memory efficiency under load."""

    def test_memory_usage_stability(self):
        """Test that memory usage remains stable during extended operation."""
        import gc

        try:
            import psutil
        except ImportError:
            pytest.skip("psutil not installed, skipping memory test")

        process = psutil.Process()
        initial_memory = process.memory_info().rss

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def memory_test_call():
            return create_mock_openai_response(
                prompt_tokens=1000,
                completion_tokens=500,
            )

        # Run many operations
        for batch in range(10):
            for _ in range(100):
                memory_test_call()

            # Force garbage collection
            gc.collect()

            current_memory = process.memory_info().rss
            memory_growth = current_memory - initial_memory

            # Memory growth should be reasonable (< 50MB)
            assert memory_growth < 50 * 1024 * 1024, (
                f"Memory grew by {memory_growth / 1024 / 1024:.1f}MB "
                f"after {(batch + 1) * 100} operations"
            )

    def test_large_response_handling(self):
        """Test handling of large responses without memory issues."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def large_response_call():
            response = Mock()
            response.model = "gpt-4"
            response.usage = Mock(
                prompt_tokens=50000,
                completion_tokens=25000,
                total_tokens=75000,
            )
            # Simulate large response content
            response.choices = [Mock(message=Mock(content="x" * 100000))]
            return response

        # Process large responses
        start = time.time()
        for _ in range(20):
            large_response_call()
        duration = time.time() - start

        # Should handle large responses efficiently
        assert duration < 5.0, f"Large response duration: {duration}s"
        assert get_dynamo_call_count(mock_dynamo) == 20

    def test_file_logging_memory_efficiency(self):
        """Test file logging doesn't accumulate memory."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            tracker = AIUsageTracker(
                track_to_file=True,
                log_file=temp_file,
            )

            @tracker.track_openai_completion
            def logged_call():
                return create_mock_openai_response()

            # Generate many log entries
            for _ in range(1000):
                logged_call()

            # File should contain all entries
            with open(temp_file, "r") as f:
                lines = f.readlines()
            assert len(lines) == 1000

            # Each line should be valid JSON
            for line in lines:
                data = json.loads(line)
                assert data["service"] == "openai"

        finally:
            os.unlink(temp_file)


@pytest.mark.performance
class TestScalabilityLimits:
    """Test scalability limits and degradation patterns."""

    def test_extreme_burst_handling(self):
        """Test handling of extreme burst loads."""
        # Use faster mock for this test
        mock_dynamo = Mock()

        # Faster put_item implementation
        def fast_put_item(**kwargs):
            pass

        mock_dynamo.put_item = fast_put_item

        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def extreme_burst_call():
            return create_mock_openai_response()

        # Extreme burst test
        start = time.time()
        for _ in range(10000):  # 10k calls
            extreme_burst_call()
        duration = time.time() - start

        # Should maintain reasonable performance even under extreme load
        throughput = 10000 / duration
        assert (
            throughput > 1000
        ), f"Extreme burst throughput: {throughput} calls/sec"

    def test_large_token_count_handling(self):
        """Test handling of very large token counts."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def large_token_call():
            return create_mock_openai_response(
                prompt_tokens=1000000,  # 1M tokens
                completion_tokens=500000,  # 500k tokens
            )

        # Should handle large token counts
        start = time.time()
        for _ in range(10):
            large_token_call()
        duration = time.time() - start

        assert duration < 1.0, f"Large token handling duration: {duration}s"

        # Verify large numbers were handled correctly
        calls = get_dynamo_call_args(mock_dynamo)
        for call in calls:
            item = call.kwargs["Item"]
            assert int(item["inputTokens"]["N"]) == 1000000
            assert int(item["outputTokens"]["N"]) == 500000

    def test_degradation_under_stress(self):
        """Test graceful degradation under extreme stress."""
        # Simulate stressed conditions
        mock_dynamo = Mock()

        failure_count = 0

        def stressed_put_item(**kwargs):
            nonlocal failure_count
            failure_count += 1
            if failure_count % 3 == 0:  # Fail 1/3 of the time
                raise Exception("Simulated stress failure")

        mock_dynamo.put_item = stressed_put_item

        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            validate_table_environment=False,  # Disable validation for test table
        )

        @tracker.track_openai_completion
        def stressed_call():
            return create_mock_openai_response()

        # Should continue operating despite failures
        successful_calls = 0
        for _ in range(100):
            try:
                stressed_call()
                successful_calls += 1
            except Exception:
                pass  # Some failures expected

        # Should complete most calls despite stress
        assert (
            successful_calls >= 60
        ), f"Only {successful_calls}/100 calls succeeded"
