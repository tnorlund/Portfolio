"""
Tests for AI Usage Context Manager (Phase 2).

Tests the context manager pattern for consistent tracking across AI operations,
including thread safety, context propagation, and metric flushing.
"""

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from receipt_label.utils.ai_usage_context import (
    ai_usage_context,
    batch_ai_usage_context,
    get_current_context,
    set_current_context,
)
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
            'test_operation',
            tracker=mock_tracker,
            custom_field='test_value'
        ) as tracker:
            # Verify tracker is returned
            assert tracker == mock_tracker
            
            # Verify context was set
            mock_tracker.set_context.assert_called_once()
            context = mock_tracker.set_context.call_args[0][0]
            
            assert context['operation_type'] == 'test_operation'
            assert context['custom_field'] == 'test_value'
            assert 'start_time' in context
            assert 'thread_id' in context
        
        # Verify flush was called on exit
        mock_tracker.flush_metrics.assert_called_once()
        
        # Verify duration was added
        mock_tracker.add_context_metadata.assert_called_once()
        duration_metadata = mock_tracker.add_context_metadata.call_args[0][0]
        assert 'operation_duration_ms' in duration_metadata

    def test_nested_contexts(self, mock_tracker):
        """Test nested context managers maintain parent context."""
        with ai_usage_context('parent_op', tracker=mock_tracker, parent_id='123') as tracker:
            parent_context = mock_tracker.set_context.call_args[0][0]
            
            with ai_usage_context('child_op', tracker=mock_tracker, child_id='456') as nested_tracker:
                child_context = mock_tracker.set_context.call_args[0][0]
                
                # Child should have parent operation reference
                assert child_context['parent_operation'] == 'parent_op'
                assert 'parent_context' in child_context
                assert child_context['parent_context']['parent_id'] == '123'
        
        # Should have been called twice (parent and child)
        assert mock_tracker.set_context.call_count == 2
        assert mock_tracker.flush_metrics.call_count == 2

    def test_batch_context_manager(self, mock_tracker):
        """Test batch-specific context manager."""
        with batch_ai_usage_context(
            'batch-123',
            item_count=100,
            tracker=mock_tracker
        ) as tracker:
            # Verify batch mode was enabled
            mock_tracker.set_batch_mode.assert_called_once_with(True)
            
            # Verify batch context
            context = mock_tracker.set_context.call_args[0][0]
            assert context['batch_id'] == 'batch-123'
            assert context['is_batch'] is True
            assert context['item_count'] == 100
            assert context['operation_type'] == 'batch_processing'

    def test_thread_safety(self):
        """Test thread-local context isolation."""
        contexts_captured = {}
        
        def capture_context(thread_name):
            with ai_usage_context(f'op_{thread_name}', thread_name=thread_name):
                # Capture the current context
                contexts_captured[thread_name] = get_current_context()
                time.sleep(0.01)  # Allow other threads to run
        
        # Run in multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=capture_context, args=(f'thread_{i}',))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # Verify each thread had isolated context
        assert len(contexts_captured) == 5
        for thread_name, context in contexts_captured.items():
            assert context['operation_type'] == f'op_{thread_name}'
            assert context['thread_name'] == thread_name

    def test_context_propagation_to_metrics(self):
        """Test that context is properly propagated to metrics."""
        # Create a real tracker with mocked storage
        mock_dynamo = MagicMock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name='AIUsageMetrics-development',
            track_to_dynamo=True,
            track_to_file=False
        )
        
        with ai_usage_context(
            'test_operation',
            tracker=tracker,
            request_id='req-123',
            custom_tag='important'
        ):
            # Simulate tracking a metric
            metadata = tracker._create_base_metadata()
            
            # Verify context is in metadata
            assert metadata['operation_type'] == 'test_operation'
            assert metadata['request_id'] == 'req-123'
            assert metadata['custom_tag'] == 'important'
            assert 'start_time' in metadata
            assert 'thread_id' in metadata

    def test_exception_handling(self, mock_tracker):
        """Test that flush is called even when exception occurs."""
        with pytest.raises(ValueError):
            with ai_usage_context('failing_op', tracker=mock_tracker):
                raise ValueError("Test error")
        
        # Verify flush was still called
        mock_tracker.flush_metrics.assert_called_once()
        mock_tracker.add_context_metadata.assert_called_once()

    def test_auto_environment_detection(self):
        """Test context manager with auto environment detection."""
        with patch.dict('os.environ', {'ENVIRONMENT': 'staging'}):
            with ai_usage_context('test_op') as tracker:
                assert tracker.environment_config.environment == Environment.STAGING
                assert tracker.table_name == 'AIUsageMetrics-staging'

    def test_context_without_tracker(self):
        """Test that context can be used without explicit tracker."""
        with ai_usage_context('standalone_op', test_field='value') as tracker:
            # Should create a tracker automatically
            assert isinstance(tracker, AIUsageTracker)
            
            # Should have context set
            assert tracker.current_context['operation_type'] == 'standalone_op'
            assert tracker.current_context['test_field'] == 'value'

    @pytest.mark.parametrize('environment', [
        Environment.PRODUCTION,
        Environment.STAGING,
        Environment.CICD,
        Environment.DEVELOPMENT
    ])
    def test_environment_specific_contexts(self, environment):
        """Test context manager respects environment configuration."""
        with ai_usage_context('env_test', environment=environment) as tracker:
            assert tracker.environment_config.environment == environment
            
            # Verify environment is in auto-tags
            assert tracker.environment_config.auto_tag['environment'] == environment.value


class TestThreadLocalContext:
    """Test thread-local context storage functions."""

    def test_set_and_get_context(self):
        """Test basic set and get of thread-local context."""
        # Initially should be None
        assert get_current_context() is None
        
        # Set context
        test_context = {'operation': 'test', 'id': '123'}
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
            set_current_context({'thread_id': thread_id})
            time.sleep(0.01)  # Allow thread switching
            
            # Retrieve and store
            results[thread_id] = get_current_context()
        
        # Create multiple threads
        threads = []
        for i in range(3):
            thread = threading.Thread(target=thread_function, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Verify each thread had isolated context
        assert len(results) == 3
        for thread_id, context in results.items():
            assert context['thread_id'] == thread_id