"""Advanced unit tests for AIUsageTracker - edge cases and advanced scenarios."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from freezegun import freeze_time

# Add the parent directory to the path to access the tests utils
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.cost_calculator import AICostCalculator
from tests.utils.ai_usage_helpers import (
    create_mock_anthropic_response,
    create_mock_openai_response)


@pytest.mark.unit
class TestConcurrentTracking:
    """Test concurrent usage tracking scenarios."""

    def test_thread_safe_context_setting(self):
        """Test that context setting is thread-safe."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        results = []

        def set_and_check_context(job_id):
            tracker.set_tracking_context(job_id=job_id)
            time.sleep(
                0.01
            )  # Small delay to increase chance of race conditions
            results.append(tracker.current_job_id)

        # Run multiple threads
        threads = []
        for i in range(10):
            thread = threading.Thread(
                target=set_and_check_context, args=(f"job-{i}")
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Last set context should win
        assert tracker.current_job_id == "job-9"

    def test_concurrent_metric_storage(self):
        """Test concurrent metric storage to both backends."""
        mock_dynamo = Mock()
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            tracker = AIUsageTracker(
                dynamo_client=mock_dynamo,
                table_name="test-table",
                track_to_dynamo=True,
                track_to_file=True,
                log_file=temp_file
            )

            @tracker.track_openai_completion
            def concurrent_call(call_id):
                response = Mock()
                response.model = "gpt-3.5-turbo"
                response.usage = Mock(
                    prompt_tokens=100 + call_id,
                    completion_tokens=50 + call_id,
                    total_tokens=150 + call_id * 2)
                return response

            # Use ThreadPoolExecutor for concurrent calls
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(concurrent_call, i) for i in range(10)
                ]
                for future in futures:
                    future.result()

            # Verify all calls were tracked to DynamoDB
            assert mock_dynamo.put_item.call_count == 10

            # Verify all calls were logged to file
            with open(temp_file, "r") as f:
                lines = f.readlines()
            assert len(lines) == 10

            # Verify each line is valid JSON
            for line in lines:
                data = json.loads(line)
                assert data["service"] == "openai"
                assert data["model"] == "gpt-3.5-turbo"

        finally:
            os.unlink(temp_file)

    def test_concurrent_wrapped_client_usage(self):
        """Test concurrent usage of wrapped OpenAI client."""
        mock_client = Mock()
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        call_count = 0

        def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            return create_mock_openai_response(
                prompt_tokens=100 + call_count,
                completion_tokens=50 + call_count)

        mock_completions.create = mock_create

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Concurrent calls through wrapped client
        def make_call(thread_id):
            return wrapped_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": f"Message {thread_id}"}])

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_call, i) for i in range(10)]
            results = [f.result() for f in futures]

        # All calls should complete
        assert len(results) == 10
        assert call_count == 10
        assert mock_dynamo.put_item.call_count == 10


@pytest.mark.unit
class TestBatchProcessing:
    """Test batch processing and aggregation scenarios."""

    def test_batch_context_tracking(self):
        """Test tracking multiple operations within a batch."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        # Set batch context
        tracker.set_tracking_context(
            job_id="job-batch-001",
            batch_id="batch-large-001",
            user_id="batch-user")

        @tracker.track_openai_completion
        def process_item(item_id):
            return create_mock_openai_response(
                prompt_tokens=100,
                completion_tokens=50)

        # Process batch
        for i in range(100):
            process_item(i)

        # Verify all items tracked with same batch context
        assert mock_dynamo.put_item.call_count == 100

        for call in mock_dynamo.put_item.call_args_list:
            item = call.kwargs["Item"]
            assert item["jobId"]["S"] == "job-batch-001"
            assert item["batchId"]["S"] == "batch-large-001"
            assert item["userId"]["S"] == "batch-user"

    def test_batch_cost_aggregation(self):
        """Test aggregating costs across a batch."""
        costs = []
        mock_dynamo = Mock()

        def capture_cost(TableName, Item):
            if "costUSD" in Item:
                costs.append(float(Item["costUSD"]["N"]))

        mock_dynamo.put_item = capture_cost

        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        @tracker.track_openai_completion
        def batch_call(tokens):
            response = Mock()
            response.model = "gpt-4"
            response.usage = Mock(
                prompt_tokens=tokens,
                completion_tokens=tokens // 2,
                total_tokens=tokens + tokens // 2)
            return response

        # Process items with varying token counts
        token_counts = [100, 500, 1000, 2000, 5000]
        for tokens in token_counts:
            batch_call(tokens)

        # Verify costs were tracked
        assert len(costs) == len(token_counts)
        total_cost = sum(costs)
        assert total_cost > 0

    def test_mixed_service_batch_tracking(self):
        """Test tracking different AI services in same batch."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        tracker.set_tracking_context(batch_id="mixed-batch-001")

        # Track OpenAI
        @tracker.track_openai_completion
        def call_openai():
            return create_mock_openai_response()

        # Track Anthropic
        @tracker.track_anthropic_completion
        def call_anthropic():
            return create_mock_anthropic_response()

        # Track Google Places
        @tracker.track_google_places("Place Details")
        def call_places():
            return {"result": {"place_id": "test"}}

        # Make mixed calls
        call_openai()
        call_anthropic()
        call_places()
        call_openai()
        call_anthropic()

        # Verify all tracked with same batch ID
        assert mock_dynamo.put_item.call_count == 5

        services = []
        for call in mock_dynamo.put_item.call_args_list:
            item = call.kwargs["Item"]
            if "batchId" in item:
                assert item["batchId"]["S"] == "mixed-batch-001"
            services.append(item["service"]["S"])

        assert services.count("openai") == 2
        assert services.count("anthropic") == 2
        assert services.count("google_places") == 1


@pytest.mark.unit
class TestMemoryManagement:
    """Test memory efficiency and cleanup."""

    def test_large_metadata_handling(self):
        """Test handling of large metadata without memory issues."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        # Create large metadata
        large_list = ["item" * 100 for _ in range(1000)]
        large_dict = {f"key_{i}": f"value_{i}" * 100 for i in range(100)}

        @tracker.track_openai_completion
        def call_with_large_data(**kwargs):
            return create_mock_openai_response()

        # This should not cause memory issues
        call_with_large_data(
            model="gpt-3.5-turbo",
            large_param=large_list,
            nested_data=large_dict)

        # Should complete without error
        mock_dynamo.put_item.assert_called_once()

    def test_file_handle_management(self):
        """Test proper file handle management."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            # Create and use multiple trackers
            for i in range(10):
                tracker = AIUsageTracker(
                    track_to_file=True,
                    log_file=temp_file)

                @tracker.track_openai_completion
                def call_api():
                    return create_mock_openai_response()

                call_api()

            # File should still be accessible
            with open(temp_file, "r") as f:
                lines = f.readlines()
            assert len(lines) == 10

        finally:
            os.unlink(temp_file)

    def test_tracker_instance_isolation(self):
        """Test that tracker instances don't share state."""
        trackers = []
        for i in range(5):
            tracker = AIUsageTracker(user_id=f"user-{i}")
            tracker.set_tracking_context(job_id=f"job-{i}")
            trackers.append(tracker)

        # Verify each tracker maintains its own state
        for i, tracker in enumerate(trackers):
            assert tracker.user_id == f"user-{i}"
            assert tracker.current_job_id == f"job-{i}"


@pytest.mark.unit
class TestErrorRecovery:
    """Test error recovery and resilience."""

    def test_partial_metric_data_handling(self):
        """Test handling of metrics with missing data."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        @tracker.track_openai_completion
        def return_partial_response():
            # Response with some missing attributes
            response = Mock()
            response.model = "gpt-3.5-turbo"
            # No usage attribute
            delattr(response, "usage")
            return response

        # Should not crash
        result = return_partial_response()
        assert result is not None

        # Metric should still be stored
        mock_dynamo.put_item.assert_called_once()
        item = mock_dynamo.put_item.call_args.kwargs["Item"]
        assert item["model"]["S"] == "gpt-3.5-turbo"
        assert "input_tokens" not in item  # Should be omitted

    def test_dynamo_retry_logic(self, capsys):
        """Test retry behavior for DynamoDB failures."""
        mock_dynamo = Mock()
        call_count = 0

        def flaky_put_item(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary failure")
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        mock_dynamo.put_item = flaky_put_item

        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        @tracker.track_openai_completion
        def make_call():
            return create_mock_openai_response()

        # Make multiple calls
        make_call()
        make_call()
        make_call()

        # Should see failure messages but continue
        captured = capsys.readouterr()
        assert "Failed to store metric in DynamoDB" in captured.out

    def test_corrupted_file_recovery(self):
        """Test recovery from corrupted log file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name
            # Write some corrupted data
            f.write("corrupted non-json data\n")
            f.write('{"partial": "json\n')

        try:
            tracker = AIUsageTracker(
                track_to_file=True,
                log_file=temp_file)

            @tracker.track_openai_completion
            def make_call():
                return create_mock_openai_response()

            # Should append successfully despite corrupted file
            make_call()

            # Verify new data was appended
            with open(temp_file, "r") as f:
                lines = f.readlines()

            # Should have original corrupted lines plus new valid JSON
            assert len(lines) == 3
            # Last line should be valid JSON
            json.loads(lines[-1])

        finally:
            os.unlink(temp_file)


@pytest.mark.unit
class TestPerformanceOptimization:
    """Test performance characteristics and optimizations."""

    def test_minimal_overhead_no_tracking(self):
        """Test minimal overhead when tracking is disabled."""
        from receipt_label.tests.utils.performance_utils import (
            assert_performance_within_bounds,
            measure_operation_overhead)

        tracker = AIUsageTracker(
            track_to_dynamo=False,
            track_to_file=False)

        @tracker.track_openai_completion
        def fast_function():
            return create_mock_openai_response()

        # Baseline: function that does similar work without tracking
        def baseline_function():
            return create_mock_openai_response()

        # Measure overhead relative to baseline
        metrics = measure_operation_overhead(
            baseline_op=baseline_function,
            test_op=fast_function,
            iterations=1000)

        # When tracking is disabled, decorator overhead should be minimal
        # The overhead is primarily from the decorator wrapper itself
        # IMPORTANT: These thresholds are environment-dependent
        # CI environments are less performant than local development machines
        # Python 3.13 and CI environments may have higher decorator overhead
        assert_performance_within_bounds(
            metrics,
            max_overhead_ratio=6.0,  # CI-tuned: decorators can add up to 6x overhead in constrained environments
            custom_message="Decorator overhead with tracking disabled")

        # Also verify absolute performance
        print(f"Decorator overhead: {metrics['overhead_ms']:.3f}ms per call")
        print(f"Overhead ratio: {metrics['overhead_ratio']:.2f}x")

        # Absolute check: overhead should be minimal per operation
        # CI environments may have higher overhead due to resource constraints
        assert (
            metrics["overhead_ms"]
            < 0.5  # Increased from 0.1 for CI compatibility
        ), f"Overhead {metrics['overhead_ms']:.3f}ms per call exceeds CI threshold"

    def test_efficient_json_serialization(self):
        """Test efficient handling of JSON serialization."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        # Create response with various data types
        @tracker.track_openai_completion
        def call_with_complex_metadata(**kwargs):
            return create_mock_openai_response()

        # Complex but serializable data
        call_with_complex_metadata(
            model="gpt-3.5-turbo",
            numbers=[1, 2.5, -3, 1e10],
            booleans=[True, False, None],
            nested={"a": {"b": {"c": "deep"}}},
            unicode="Hello 世界 🌍",
            empty_list=[],
            empty_dict={})

        # Should serialize efficiently
        mock_dynamo.put_item.assert_called_once()

    @freeze_time("2024-01-01 12:00:00")
    def test_timestamp_consistency(self):
        """Test timestamp handling across operations."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        timestamps = []

        def capture_timestamp(TableName, Item):
            timestamps.append(Item["timestamp"]["S"])

        mock_dynamo.put_item = capture_timestamp

        @tracker.track_openai_completion
        def make_call():
            # Simulate some processing time
            time.sleep(0.001)
            return create_mock_openai_response()

        # Make rapid calls
        for _ in range(5):
            make_call()

        # All timestamps should be from the frozen time
        assert len(timestamps) == 5
        for ts in timestamps:
            assert "2024-01-01" in ts


@pytest.mark.unit
class TestAdvancedIntegration:
    """Test advanced integration scenarios."""

    def test_decorator_stacking(self):
        """Test stacking multiple tracking decorators."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        # Apply multiple decorators
        @tracker.track_google_places("Place Details")
        @tracker.track_openai_completion
        def complex_operation():
            # This simulates a function that calls multiple services
            # Both decorators will fire independently
            return create_mock_openai_response()

        result = complex_operation()

        # Both decorators should track independently
        assert mock_dynamo.put_item.call_count == 2

        # Check that both services were tracked
        calls = mock_dynamo.put_item.call_args_list
        services = [call.kwargs["Item"]["service"]["S"] for call in calls]
        assert "google_places" in services
        assert "openai" in services

    def test_nested_tracked_calls(self):
        """Test nested function calls with tracking."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        @tracker.track_openai_completion
        def inner_call():
            return create_mock_openai_response(model="gpt-3.5-turbo")

        @tracker.track_anthropic_completion
        def outer_call():
            # Call OpenAI from within Anthropic call
            inner_call()
            return create_mock_anthropic_response(model="claude-3-opus")

        outer_call()

        # Both calls should be tracked
        assert mock_dynamo.put_item.call_count == 2

        # Verify both services were tracked
        services = []
        for call in mock_dynamo.put_item.call_args_list:
            item = call.kwargs["Item"]
            services.append(item["service"]["S"])

        assert "openai" in services
        assert "anthropic" in services

    def test_exception_propagation_with_tracking(self):
        """Test that exceptions propagate correctly while still tracking."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True
        )

        class CustomException(Exception):
            pass

        @tracker.track_openai_completion
        def failing_call():
            raise CustomException("Specific error")

        # Exception should propagate
        with pytest.raises(CustomException, match="Specific error"):
            failing_call()

        # But tracking should still occur
        mock_dynamo.put_item.assert_called_once()
        item = mock_dynamo.put_item.call_args.kwargs["Item"]
        assert item["error"]["S"] == "Specific error"

    def test_context_inheritance(self):
        """Test context inheritance in complex scenarios."""
        mock_dynamo = Mock()
        parent_tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            user_id="parent-user"
        )

        child_tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            track_to_dynamo=True,
            user_id="child-user"
        )

        # Set different contexts
        parent_tracker.set_tracking_context(job_id="parent-job")
        child_tracker.set_tracking_context(job_id="child-job")

        @parent_tracker.track_openai_completion
        def parent_call():
            return create_mock_openai_response()

        @child_tracker.track_openai_completion
        def child_call():
            return create_mock_openai_response()

        parent_call()
        child_call()

        # Each should maintain its own context
        calls = mock_dynamo.put_item.call_args_list
        parent_item = calls[0].kwargs["Item"]
        child_item = calls[1].kwargs["Item"]

        assert parent_item["jobId"]["S"] == "parent-job"
        assert parent_item["userId"]["S"] == "parent-user"
        assert child_item["jobId"]["S"] == "child-job"
        assert child_item["userId"]["S"] == "child-user"
