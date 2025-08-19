"""
Tests for AI Usage Context Manager (Phase 2).

Tests the context manager pattern for consistent tracking across AI operations,
including thread safety, context propagation, and metric flushing.
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from receipt_label.utils.ai_usage_context import (
    ai_usage_context,
    batch_ai_usage_context,
    get_current_context,
    set_current_context)
from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.environment_config import Environment


class TestAIUsageContext:
    """Test the AI usage context manager functionality."""

    @pytest.fixture
    def mock_tracker(self):
        """Create a mock AIUsageTracker."""
        tracker = MagicMock(spec=AIUsageTracker)
        tracker.set_context = Mock()
        tracker.flush_metrics = Mock()
        tracker.add_context_metadata = Mock()
        tracker.set_batch_mode = Mock()
        return tracker

    def test_basic_context_manager(self, mock_tracker):
        """Test basic context manager functionality."""
        with ai_usage_context(
            "test_operation", tracker=mock_tracker, custom_field="test_value"
        ) as tracker:
            # Verify tracker is returned
            assert tracker == mock_tracker

            # Verify context was set
            mock_tracker.set_context.assert_called_once()
            context = mock_tracker.set_context.call_args[0][0]

            assert context["operation_type"] == "test_operation"
            assert context["custom_field"] == "test_value"
            assert "start_time" in context
            assert "thread_id" in context

        # Verify flush was called on exit
        mock_tracker.flush_metrics.assert_called_once()

        # Verify duration was added
        mock_tracker.add_context_metadata.assert_called_once()
        duration_metadata = mock_tracker.add_context_metadata.call_args[0][0]
        assert "operation_duration_ms" in duration_metadata

    def test_nested_contexts(self, mock_tracker):
        """Test nested context managers maintain parent context."""
        with ai_usage_context(
            "parent_op", tracker=mock_tracker, parent_id="123"
        ):
            mock_tracker.set_context.call_args[0][0]

            with ai_usage_context(
                "child_op", tracker=mock_tracker, child_id="456"
            ):
                child_context = mock_tracker.set_context.call_args[0][0]

                # Child should have parent operation reference
                assert child_context["parent_operation"] == "parent_op"
                assert "parent_context" in child_context
                assert child_context["parent_context"]["parent_id"] == "123"

        # Should have been called twice (parent and child)
        assert mock_tracker.set_context.call_count == 2
        assert mock_tracker.flush_metrics.call_count == 2

    def test_batch_context_manager(self, mock_tracker):
        """Test batch-specific context manager."""
        with batch_ai_usage_context(
            "batch-123", item_count=100, tracker=mock_tracker
        ):
            # Verify batch mode was enabled
            mock_tracker.set_batch_mode.assert_called_once_with(True)

            # Verify batch context
            context = mock_tracker.set_context.call_args[0][0]
            assert context["batch_id"] == "batch-123"
            assert context["is_batch"] is True
            assert context["item_count"] == 100
            assert context["operation_type"] == "batch_processing"

    def test_thread_safety(self):
        """Test thread-local context isolation."""
        contexts_captured = {}

        def capture_context(thread_name):
            with ai_usage_context(
                f"op_{thread_name}", thread_name=thread_name
            ):
                # Capture the current context
                contexts_captured[thread_name] = get_current_context()
                time.sleep(0.01)  # Allow other threads to run

        # Run in multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(
                target=capture_context, args=(f"thread_{i}")
            )
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # Verify each thread had isolated context
        assert len(contexts_captured) == 5
        for thread_name, context in contexts_captured.items():
            assert context["operation_type"] == f"op_{thread_name}"
            assert context["thread_name"] == thread_name

    def test_context_propagation_to_metrics(self):
        """Test that context is properly propagated to metrics."""
        # Create a real tracker with mocked storage
        mock_dynamo = MagicMock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="AIUsageMetrics-development",
            track_to_dynamo=True,
            track_to_file=False)

        with ai_usage_context(
            "test_operation",
            tracker=tracker,
            request_id="req-123",
            custom_tag="important"):
            # Simulate tracking a metric
            metadata = tracker._create_base_metadata()

            # Verify context is in metadata
            assert metadata["operation_type"] == "test_operation"
            assert metadata["request_id"] == "req-123"
            assert metadata["custom_tag"] == "important"
            assert "start_time" in metadata
            assert "thread_id" in metadata

    def test_exception_handling(self, mock_tracker):
        """Test that flush is called even when exception occurs."""
        with pytest.raises(ValueError):
            with ai_usage_context("failing_op", tracker=mock_tracker):
                raise ValueError("Test error")

        # Verify flush was still called
        mock_tracker.flush_metrics.assert_called_once()
        mock_tracker.add_context_metadata.assert_called_once()

    def test_auto_environment_detection(self):
        """Test context manager with auto environment detection."""
        with patch.dict("os.environ", {"ENVIRONMENT": "staging"}):
            with ai_usage_context("test_op") as tracker:
                assert (
                    tracker.environment_config.environment
                    == Environment.STAGING
                )
                # Table name depends on environment config
                assert "staging" in tracker.table_name

    def test_context_without_tracker(self):
        """Test that context can be used without explicit tracker."""
        with ai_usage_context("standalone_op", test_field="value") as tracker:
            # Should create a tracker automatically
            assert isinstance(tracker, AIUsageTracker)

            # Should have context set
            assert tracker.current_context["operation_type"] == "standalone_op"
            assert tracker.current_context["test_field"] == "value"

    @pytest.mark.parametrize(
        "environment",
        [
            Environment.PRODUCTION,
            Environment.STAGING,
            Environment.CICD,
            Environment.DEVELOPMENT,
        ])
    def test_environment_specific_contexts(self, environment):
        """Test context manager respects environment configuration."""
        with ai_usage_context("env_test", environment=environment) as tracker:
            assert tracker.environment_config.environment == environment

            # Verify environment is in auto-tags
            assert (
                tracker.environment_config.auto_tag["environment"]
                == environment.value
            )


class TestThreadLocalContext:
    """Test thread-local context storage functions."""

    def test_set_and_get_context(self):
        """Test basic set and get of thread-local context."""
        # Initially should be None
        assert get_current_context() is None

        # Set context
        test_context = {"operation": "test", "id": "123"}
        set_current_context(test_context)

        # Should retrieve same context
        retrieved = get_current_context()
        assert retrieved == test_context

        # Clear context
        set_current_context(None)
        assert get_current_context() is None

    def test_thread_isolation(self):
        """Test that contexts are isolated between threads."""
        results = {}

        def thread_function(thread_id):
            # Set thread-specific context
            set_current_context({"thread_id": thread_id})
            time.sleep(0.01)  # Allow thread switching

            # Retrieve and store
            results[thread_id] = get_current_context()

        # Create multiple threads
        threads = []
        for i in range(3):
            thread = threading.Thread(target=thread_function, args=(i))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify each thread had isolated context
        assert len(results) == 3
        for thread_id, context in results.items():
            assert context["thread_id"] == thread_id


class TestAIUsageDecorator:
    """Test the AI usage tracking decorator functionality."""

    @pytest.fixture
    def mock_tracker(self):
        """Create a mock AIUsageTracker."""
        tracker = MagicMock(spec=AIUsageTracker)
        tracker.set_context = Mock()
        tracker.flush_metrics = Mock()
        tracker.add_context_metadata = Mock()
        tracker.current_context = {}
        return tracker

    def test_basic_decorator_no_args(self):
        """Test decorator without arguments."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        call_count = 0

        @ai_usage_tracked
        def test_function(value: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"processed: {value}"

        # Call the decorated function
        result = test_function("test_input")

        assert result == "processed: test_input"
        assert call_count == 1

    def test_decorator_with_operation_type(self, mock_tracker):
        """Test decorator with custom operation type."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked(
            operation_type="custom_operation", tracker=mock_tracker
        )
        def process_data(data: str) -> str:
            return data.upper()

        result = process_data("hello")

        assert result == "HELLO"
        mock_tracker.set_context.assert_called_once()
        context = mock_tracker.set_context.call_args[0][0]
        assert context["operation_type"] == "custom_operation"

    def test_decorator_with_context_kwargs(self, mock_tracker):
        """Test decorator with additional context kwargs."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked(
            operation_type="batch_process",
            tracker=mock_tracker,
            batch_id="batch-123",
            priority="high")
        def batch_operation(items: list) -> int:
            return len(items)

        result = batch_operation([1, 2, 3])

        assert result == 3
        context = mock_tracker.set_context.call_args[0][0]
        assert context["batch_id"] == "batch-123"
        assert context["priority"] == "high"

    def test_decorator_extracts_runtime_context(self, mock_tracker):
        """Test that decorator extracts context from function kwargs."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked(tracker=mock_tracker)
        def process_with_context(data: str, job_id: str = None) -> str:
            # job_id should be extracted by decorator
            return f"processed: {data}"

        result = process_with_context("test", job_id="job-456")

        assert result == "processed: test"
        context = mock_tracker.set_context.call_args[0][0]
        assert context["job_id"] == "job-456"
        assert context["operation_type"] == "process_with_context"

    def test_decorator_passes_tracker_to_function(self):
        """Test that decorator passes tracker to function if requested."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        captured_tracker = None

        @ai_usage_tracked
        def function_needs_tracker(data: str, tracker=None) -> str:
            nonlocal captured_tracker
            captured_tracker = tracker
            return data

        result = function_needs_tracker("test")

        assert result == "test"
        assert captured_tracker is not None
        assert isinstance(captured_tracker, AIUsageTracker)

    def test_async_decorator(self):
        """Test decorator with async functions."""
        import asyncio

        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        call_count = 0

        @ai_usage_tracked(operation_type="async_op")
        async def async_process(value: str) -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return f"async: {value}"

        # Run async function
        result = asyncio.run(async_process("test"))

        assert result == "async: test"
        assert call_count == 1

    def test_decorator_with_exception_handling(self, mock_tracker):
        """Test that decorator handles exceptions properly."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked(tracker=mock_tracker)
        def failing_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_function()

        # Verify flush was still called
        mock_tracker.flush_metrics.assert_called_once()

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function metadata."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked
        def documented_function(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "Add two numbers."
        assert documented_function(2, 3) == 5

    def test_nested_decorated_functions(self, mock_tracker):
        """Test nested decorated functions maintain separate contexts."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked(operation_type="outer", tracker=mock_tracker)
        def outer_function(value: str) -> str:
            result = inner_function(value)
            return f"outer({result})"

        @ai_usage_tracked(operation_type="inner", tracker=mock_tracker)
        def inner_function(value: str) -> str:
            return f"inner({value})"

        result = outer_function("test")

        assert result == "outer(inner(test))"
        assert mock_tracker.set_context.call_count == 2

        # Check both contexts were set
        contexts = [
            call[0][0] for call in mock_tracker.set_context.call_args_list
        ]
        assert contexts[0]["operation_type"] == "outer"
        assert contexts[1]["operation_type"] == "inner"
        assert contexts[1].get("parent_operation") == "outer"

    def test_decorator_styles(self):
        """Test different decorator usage styles."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        # Test style 1: @ai_usage_tracked
        @ai_usage_tracked
        def test_func1():
            return "success1"

        # Test style 2: @ai_usage_tracked()
        @ai_usage_tracked()
        def test_func2():
            return "success2"

        # Test style 3: @ai_usage_tracked(...)
        @ai_usage_tracked(operation_type="test")
        def test_func3():
            return "success3"

        assert test_func1() == "success1"
        assert test_func2() == "success2"
        assert test_func3() == "success3"


class TestErrorRecoveryAndPartialFailures:
    """Test error recovery and partial failure handling."""

    @pytest.fixture
    def mock_tracker(self):
        """Create a mock AIUsageTracker."""
        tracker = MagicMock(spec=AIUsageTracker)
        # Mock the create_for_environment method
        with patch(
            "receipt_label.utils.ai_usage_context.AIUsageTracker."
            "create_for_environment",
            return_value=tracker):
            yield tracker

    def test_context_manager_error_recovery(self, mock_tracker):
        """Test that context manager handles errors and still flushes metrics."""
        from receipt_label.utils.ai_usage_context import ai_usage_context

        with pytest.raises(ValueError, match="Test error"):
            with ai_usage_context("test_op", tracker=mock_tracker):
                raise ValueError("Test error")

        # Verify context metadata was updated with error info
        mock_tracker.add_context_metadata.assert_called()
        metadata = mock_tracker.add_context_metadata.call_args[0][0]
        assert metadata["error_occurred"] is True
        assert metadata["error_message"] == "Test error"

        # Verify flush was still called
        mock_tracker.flush_metrics.assert_called_once()

    def test_context_manager_flush_failure_handling(
        self, mock_tracker, caplog
    ):
        """Test that flush failures don't mask original errors."""
        from receipt_label.utils.ai_usage_context import ai_usage_context

        # Make flush_metrics raise an error
        mock_tracker.flush_metrics.side_effect = RuntimeError("Flush failed")

        # Should not raise the flush error
        with ai_usage_context("test_op", tracker=mock_tracker):
            pass

        # Check that flush error was logged
        assert "Failed to flush metrics: Flush failed" in caplog.text

    def test_partial_failure_context_basic(self, mock_tracker):
        """Test basic partial failure context functionality."""
        from receipt_label.utils.ai_usage_context import (
            partial_failure_context)

        with partial_failure_context("batch_op", tracker=mock_tracker) as ctx:
            # Process some items successfully
            for i in range(3):
                ctx["success_count"] += 1

            # Process some items with failures
            for i in range(2):
                ctx["errors"].append(
                    {"item": f"item_{i}", "error": "Processing failed"}
                )
                ctx["failure_count"] += 1

        # Verify summary was added to metadata
        mock_tracker.add_context_metadata.assert_called()
        # Find the call that has partial_failure_summary
        all_calls = mock_tracker.add_context_metadata.call_args_list
        summary = None
        for call in all_calls:
            metadata = call[0][0]
            if "partial_failure_summary" in metadata:
                summary = metadata["partial_failure_summary"]
                break

        assert summary is not None

        assert summary["total_operations"] == 5
        assert summary["successful"] == 3
        assert summary["failed"] == 2
        assert summary["error_count"] == 2
        assert len(summary["errors"]) == 2

    def test_partial_failure_continue_on_error(self, mock_tracker):
        """Test partial failure context with continue_on_error=True."""
        from receipt_label.utils.ai_usage_context import (
            partial_failure_context)

        items = ["item1", "item2", "item3", "item4"]
        processed = []

        with partial_failure_context(
            "batch_op", tracker=mock_tracker, continue_on_error=True
        ) as ctx:
            for item in items:
                try:
                    if item == "item2":
                        raise ValueError(f"Failed to process {item}")
                    processed.append(item)
                    ctx["success_count"] += 1
                except Exception as e:
                    ctx["errors"].append({"item": item, "error": str(e)})
                    ctx["failure_count"] += 1

        # Should have processed all items except the failing one
        assert len(processed) == 3
        assert "item2" not in processed

        # Check summary
        all_calls = mock_tracker.add_context_metadata.call_args_list
        summary = None
        for call in all_calls:
            metadata = call[0][0]
            if "partial_failure_summary" in metadata:
                summary = metadata["partial_failure_summary"]
                break

        assert summary is not None
        assert summary["successful"] == 3
        assert summary["failed"] == 1

    def test_partial_failure_stop_on_error(self, mock_tracker):
        """Test partial failure context with continue_on_error=False."""
        from receipt_label.utils.ai_usage_context import (
            partial_failure_context)

        items = ["item1", "item2", "item3", "item4"]
        processed = []

        with pytest.raises(ValueError, match="Failed to process item2"):
            with partial_failure_context(
                "batch_op", tracker=mock_tracker, continue_on_error=False
            ) as ctx:
                for item in items:
                    try:
                        if item == "item2":
                            raise ValueError(f"Failed to process {item}")
                        processed.append(item)
                        ctx["success_count"] += 1
                    except Exception as e:
                        ctx["errors"].append({"item": item, "error": str(e)})
                        ctx["failure_count"] += 1
                        if not ctx["continue_on_error"]:
                            raise

        # Should have only processed first item
        assert len(processed) == 1
        assert processed == ["item1"]

    def test_partial_failure_error_limit(self, mock_tracker):
        """Test that partial failure context limits stored errors to 10."""
        from receipt_label.utils.ai_usage_context import (
            partial_failure_context)

        with partial_failure_context("batch_op", tracker=mock_tracker) as ctx:
            # Add 15 errors
            for i in range(15):
                ctx["errors"].append(
                    {"item": f"item_{i}", "error": f"Error {i}"}
                )
                ctx["failure_count"] += 1

        # Check that only first 10 errors are stored in summary
        all_calls = mock_tracker.add_context_metadata.call_args_list
        summary = None
        for call in all_calls:
            metadata = call[0][0]
            if "partial_failure_summary" in metadata:
                summary = metadata["partial_failure_summary"]
                break

        assert summary is not None

        assert summary["error_count"] == 15  # Total count preserved
        assert len(summary["errors"]) == 10  # But only 10 stored
        assert summary["errors"][0]["item"] == "item_0"
        assert summary["errors"][9]["item"] == "item_9"

    def test_decorator_with_error_metadata(self, mock_tracker):
        """Test that decorator captures error information in context."""
        from receipt_label.utils.ai_usage_context import ai_usage_tracked

        @ai_usage_tracked(tracker=mock_tracker)
        def failing_operation():
            raise RuntimeError("Operation failed")

        with pytest.raises(RuntimeError):
            failing_operation()

        # Check that error metadata was captured
        mock_tracker.add_context_metadata.assert_called()
        metadata = mock_tracker.add_context_metadata.call_args[0][0]
        assert metadata["error_occurred"] is True
        assert "Operation failed" in metadata["error_message"]
