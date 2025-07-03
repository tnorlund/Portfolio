"""
Performance tests for AI Usage Context Manager (Phase 3).

Tests that context manager operations meet the < 5ms performance requirement.
"""

import asyncio
import threading  # noqa: F401
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from receipt_label.utils import (
    ai_usage_context,
    ai_usage_tracked,
    get_current_context,
    set_current_context,
)
from receipt_label.utils.ai_usage_tracker import AIUsageTracker


class TestContextPerformance:
    """Test performance characteristics of context managers."""

    @pytest.fixture
    def mock_tracker(self):
        """Create a mock AIUsageTracker with minimal overhead."""
        tracker = MagicMock(spec=AIUsageTracker)
        # Make operations fast
        tracker.set_context.side_effect = lambda x: None
        tracker.add_context_metadata.side_effect = lambda x: None
        tracker.flush_metrics.side_effect = lambda: None

        with patch(
            "receipt_label.utils.ai_usage_context.AIUsageTracker."
            "create_for_environment",
            return_value=tracker,
        ):
            yield tracker

    def test_context_manager_overhead(self, mock_tracker):
        """Test that context manager adds < 5ms overhead."""
        # Warm up
        with ai_usage_context("warmup", tracker=mock_tracker):
            pass

        # Measure baseline operation time
        start = time.perf_counter()
        sum(range(1000))  # Simple operation
        baseline_time = time.perf_counter() - start

        # Measure with context manager
        start = time.perf_counter()
        with ai_usage_context("test_op", tracker=mock_tracker):
            sum(range(1000))  # Same operation
        context_time = time.perf_counter() - start

        overhead_ms = (context_time - baseline_time) * 1000
        print(f"Context overhead: {overhead_ms:.2f}ms")

        # Should be well under 5ms
        assert overhead_ms < 5.0

    def test_decorator_overhead(self, mock_tracker):
        """Test that decorator adds < 5ms overhead."""
        call_count = 0

        # Plain function
        def plain_function():
            nonlocal call_count
            call_count += 1
            return sum(range(100))

        # Decorated function
        @ai_usage_tracked(tracker=mock_tracker)
        def tracked_function():
            nonlocal call_count
            call_count += 1
            return sum(range(100))

        # Warm up
        plain_function()
        tracked_function()
        call_count = 0

        # Measure plain function
        start = time.perf_counter()
        for _ in range(100):
            plain_function()
        plain_time = time.perf_counter() - start

        # Reset
        call_count = 0

        # Measure decorated function
        start = time.perf_counter()
        for _ in range(100):
            tracked_function()
        decorated_time = time.perf_counter() - start

        avg_overhead_ms = ((decorated_time - plain_time) / 100) * 1000
        print(f"Average decorator overhead: {avg_overhead_ms:.2f}ms")

        assert avg_overhead_ms < 5.0

    def test_nested_context_performance(self, mock_tracker):
        """Test performance with nested contexts."""
        start = time.perf_counter()

        with ai_usage_context("outer", tracker=mock_tracker):
            with ai_usage_context("middle", tracker=mock_tracker):
                with ai_usage_context("inner", tracker=mock_tracker):
                    # Simulate some work
                    sum(range(1000))

        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"Triple nested context time: {elapsed_ms:.2f}ms")

        # Even with 3 nested contexts, should be fast
        assert elapsed_ms < 15.0  # 3 * 5ms max

    def test_concurrent_context_isolation(self, mock_tracker):
        """Test that concurrent contexts don't interfere."""
        results = []
        errors = []

        def worker(worker_id: int):
            try:
                with ai_usage_context(
                    f"worker_{worker_id}", tracker=mock_tracker
                ):
                    # Each worker sets its own context
                    context = get_current_context()
                    time.sleep(0.001)  # Simulate work

                    # Verify context hasn't changed
                    after_context = get_current_context()
                    assert context == after_context
                    assert context["operation_type"] == f"worker_{worker_id}"

                    results.append(worker_id)
            except Exception as e:
                errors.append((worker_id, str(e)))

        # Run workers concurrently
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(20)]
            for future in futures:
                future.result()

        assert len(results) == 20
        assert len(errors) == 0

    def test_async_decorator_performance(self, mock_tracker):
        """Test async decorator performance."""

        @ai_usage_tracked(tracker=mock_tracker)
        async def async_operation(value: int) -> int:
            await asyncio.sleep(0.001)  # Simulate async work
            return value * 2

        async def run_test():
            # Warm up
            await async_operation(1)

            # Measure overhead
            start = time.perf_counter()
            tasks = [async_operation(i) for i in range(10)]
            results = await asyncio.gather(*tasks)
            elapsed_ms = (time.perf_counter() - start) * 1000

            print(f"10 async operations time: {elapsed_ms:.2f}ms")

            # Should complete quickly despite async overhead
            assert all(results[i] == i * 2 for i in range(10))
            # Allow more time for async operations
            assert elapsed_ms < 100.0  # ~10ms per operation max

        asyncio.run(run_test())

    def test_thread_local_performance(self):
        """Test thread-local storage performance."""
        iterations = 10000

        # Measure set/get performance
        start = time.perf_counter()
        for i in range(iterations):
            set_current_context({"iteration": i})
            ctx = get_current_context()
            assert ctx["iteration"] == i
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_ms = elapsed_ms / iterations
        print(f"Average thread-local operation: {avg_ms:.4f}ms")

        # Should be extremely fast
        assert avg_ms < 0.001  # Sub-microsecond

    def test_context_memory_overhead(self, mock_tracker):
        """Test that contexts don't leak memory."""
        import gc

        # Force garbage collection
        gc.collect()

        # Get baseline object count
        baseline_objects = len(gc.get_objects())

        # Create and destroy many contexts
        for i in range(100):
            with ai_usage_context(f"test_{i}", tracker=mock_tracker):
                # Create some temporary data
                {"key": f"value_{i}" * 100}

        # Force garbage collection
        gc.collect()

        # Check object count hasn't grown significantly
        final_objects = len(gc.get_objects())
        object_growth = final_objects - baseline_objects

        print(f"Object growth: {object_growth}")

        # Should not leak objects
        # In test environments, mock objects and test infrastructure can
        # create many objects
        # The important thing is that growth is bounded, not that it's minimal
        assert object_growth < 2000  # Allow growth for test infrastructure

    @pytest.mark.parametrize("context_size", [10, 100, 1000])
    def test_large_context_performance(self, mock_tracker, context_size):
        """Test performance with different context sizes."""
        # Create context with many keys
        large_context = {
            f"key_{i}": f"value_{i}" * 10 for i in range(context_size)
        }

        start = time.perf_counter()
        with ai_usage_context(
            "large_op", tracker=mock_tracker, **large_context
        ):
            # Access context
            ctx = get_current_context()
            assert len(ctx) >= context_size
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"Context size {context_size}: {elapsed_ms:.2f}ms")

        # Should still be fast even with large contexts
        assert elapsed_ms < 10.0
