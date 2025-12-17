"""
Test file for ChromaDB Compaction Lambda functions.

This test file verifies that the Lambda functions can be imported and
basic functionality works with the mocked dependencies.
"""

from unittest.mock import MagicMock

import pytest


def test_stream_processor_import():
    """Test that stream_processor can be imported successfully."""
    try:
        from stream_processor import lambda_handler

        assert callable(lambda_handler)
        print("✅ Stream processor imported successfully")
    except Exception as e:
        pytest.fail(f"Failed to import stream_processor: {e}")


def test_enhanced_compaction_handler_import():
    """Test that enhanced_compaction_handler can be imported successfully."""
    try:
        from enhanced_compaction_handler import lambda_handler

        assert callable(lambda_handler)
        print("✅ Enhanced compaction handler imported successfully")
    except Exception as e:
        pytest.fail(f"Failed to import enhanced_compaction_handler: {e}")


def test_stream_processor_execution():
    """Test that stream_processor lambda_handler can be executed."""
    from stream_processor import lambda_handler

    # Test with empty event
    test_event = {"Records": []}
    test_context = MagicMock()

    result = lambda_handler(test_event, test_context)

    # Should return a response (mocked)
    assert result is not None
    print("✅ Stream processor executed successfully")


def test_enhanced_compaction_handler_execution():
    """Test that enhanced_compaction_handler lambda_handler can be executed."""
    from enhanced_compaction_handler import lambda_handler

    # Test with empty event
    test_event = {"Records": []}
    test_context = MagicMock()

    result = lambda_handler(test_event, test_context)

    # Should return a response (mocked)
    assert result is not None
    print("✅ Enhanced compaction handler executed successfully")


def test_processor_imports():
    """Test that processor module imports work."""
    try:
        from processor import FieldChange, LambdaResponse, ParsedStreamRecord

        assert LambdaResponse is not None
        assert FieldChange is not None
        assert ParsedStreamRecord is not None
        print("✅ Processor imports successful")
    except Exception as e:
        pytest.fail(f"Failed to import processor modules: {e}")


def test_compaction_imports():
    """Test that compaction module imports work."""
    try:
        from compaction.models import LambdaResponse, StreamMessage
        from compaction.operations import (
            update_receipt_metadata,
            update_word_labels,
        )

        assert LambdaResponse is not None
        assert StreamMessage is not None
        assert update_receipt_metadata is not None
        assert update_word_labels is not None
        print("✅ Compaction imports successful")
    except Exception as e:
        pytest.fail(f"Failed to import compaction modules: {e}")


def test_utils_imports():
    """Test that utils module imports work."""
    try:
        import utils

        assert utils.get_operation_logger is not None
        assert utils.metrics is not None
        assert utils.trace_function is not None
        assert utils.trace_compaction_operation is not None
        print("✅ Utils imports successful")
    except Exception as e:
        pytest.fail(f"Failed to import utils modules: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
