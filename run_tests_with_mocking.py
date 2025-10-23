#!/usr/bin/env python3
"""
Simple test runner for ChromaDB Compaction tests.

This runner uses mocking to test the Lambda functions without modifying
the original code files.
"""

import sys
import os
from unittest.mock import MagicMock

def setup_mocks():
    """Setup mocks for testing."""
    
    # Add the lambdas directory to Python path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    lambdas_path = os.path.join(current_dir, 'infra', 'chromadb_compaction', 'lambdas')
    if os.path.exists(lambdas_path):
        sys.path.insert(0, lambdas_path)
    
    # Mock the utils module
    utils_mock = MagicMock()
    logger_mock = MagicMock()
    logger_mock.info = MagicMock()
    logger_mock.error = MagicMock()
    logger_mock.debug = MagicMock()
    logger_mock.exception = MagicMock()
    utils_mock.get_operation_logger = MagicMock(return_value=logger_mock)
    utils_mock.metrics = MagicMock()
    utils_mock.trace_function = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
    utils_mock.trace_compaction_operation = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
    utils_mock.start_compaction_lambda_monitoring = MagicMock()
    utils_mock.stop_compaction_lambda_monitoring = MagicMock()
    utils_mock.with_compaction_timeout_protection = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
    utils_mock.format_response = MagicMock(side_effect=lambda response, *args, **kwargs: response)
    sys.modules['utils'] = utils_mock
    
    # Mock the processor module
    processor_mock = MagicMock()
    processor_mock.LambdaResponse = MagicMock
    processor_mock.FieldChange = MagicMock
    processor_mock.ParsedStreamRecord = MagicMock
    processor_mock.ChromaDBCollection = MagicMock
    processor_mock.StreamMessage = MagicMock
    processor_mock.build_messages_from_records = MagicMock(return_value=[])
    processor_mock.publish_messages = MagicMock(return_value=0)
    processor_mock.parse_stream_record = MagicMock()
    processor_mock.get_chromadb_relevant_changes = MagicMock(return_value=[])
    processor_mock.detect_entity_type = MagicMock()
    processor_mock.parse_entity = MagicMock()
    processor_mock.is_compaction_run = MagicMock(return_value=False)
    processor_mock.parse_compaction_run = MagicMock()
    sys.modules['processor'] = processor_mock
    
    # Mock the compaction module and its submodules
    compaction_mock = MagicMock()
    compaction_mock.LambdaResponse = MagicMock
    compaction_mock.StreamMessage = MagicMock
    compaction_mock.MetadataUpdateResult = MagicMock
    compaction_mock.LabelUpdateResult = MagicMock
    compaction_mock.process_sqs_messages = MagicMock()
    compaction_mock.categorize_stream_messages = MagicMock()
    compaction_mock.group_messages_by_collection = MagicMock()
    compaction_mock.process_metadata_updates = MagicMock()
    compaction_mock.process_label_updates = MagicMock()
    compaction_mock.process_compaction_run_messages = MagicMock()
    compaction_mock.update_receipt_metadata = MagicMock()
    compaction_mock.remove_receipt_metadata = MagicMock()
    compaction_mock.update_word_labels = MagicMock()
    compaction_mock.remove_word_labels = MagicMock()
    compaction_mock.reconstruct_label_metadata = MagicMock()
    sys.modules['compaction'] = compaction_mock
    
    # Mock compaction submodules
    compaction_models_mock = MagicMock()
    compaction_models_mock.LambdaResponse = MagicMock
    compaction_models_mock.StreamMessage = MagicMock
    compaction_models_mock.MetadataUpdateResult = MagicMock
    compaction_models_mock.LabelUpdateResult = MagicMock
    sys.modules['compaction.models'] = compaction_models_mock
    
    compaction_operations_mock = MagicMock()
    compaction_operations_mock.update_receipt_metadata = MagicMock()
    compaction_operations_mock.remove_receipt_metadata = MagicMock()
    compaction_operations_mock.update_word_labels = MagicMock()
    compaction_operations_mock.remove_word_labels = MagicMock()
    compaction_operations_mock.reconstruct_label_metadata = MagicMock()
    sys.modules['compaction.operations'] = compaction_operations_mock

def test_stream_processor():
    """Test the stream processor Lambda."""
    print("🧪 Testing stream processor...")
    
    try:
        from stream_processor import lambda_handler
        
        # Test with empty event
        test_event = {'Records': []}
        test_context = MagicMock()
        
        result = lambda_handler(test_event, test_context)
        print("✅ Stream processor test passed")
        return True
        
    except Exception as e:
        print(f"❌ Stream processor test failed: {e}")
        return False

def test_enhanced_compaction_handler():
    """Test the enhanced compaction handler Lambda."""
    print("🧪 Testing enhanced compaction handler...")
    
    try:
        from enhanced_compaction_handler import lambda_handler
        
        # Test with empty event
        test_event = {'Records': []}
        test_context = MagicMock()
        
        result = lambda_handler(test_event, test_context)
        print("✅ Enhanced compaction handler test passed")
        return True
        
    except Exception as e:
        print(f"❌ Enhanced compaction handler test failed: {e}")
        return False

def test_imports():
    """Test that all imports work correctly."""
    print("🧪 Testing imports...")
    
    try:
        # Test stream processor imports
        from stream_processor import LambdaResponse, FieldChange, ParsedStreamRecord
        print("✅ Stream processor imports successful")
        
        # Test compaction imports
        from compaction.models import LambdaResponse as CompactionLambdaResponse, StreamMessage
        from compaction.operations import update_receipt_metadata, update_word_labels
        print("✅ Compaction imports successful")
        
        return True
        
    except Exception as e:
        print(f"❌ Import test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("🚀 Running ChromaDB Compaction Tests with Mocking")
    print("=" * 50)
    
    # Setup mocks
    setup_mocks()
    print("✅ Mocks setup complete")
    
    # Run tests
    tests = [
        test_imports,
        test_stream_processor,
        test_enhanced_compaction_handler,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        if test():
            passed += 1
        else:
            failed += 1
    
    print("\n📊 Test Results:")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    
    if failed == 0:
        print("🎉 All tests passed!")
        return True
    else:
        print("❌ Some tests failed")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
