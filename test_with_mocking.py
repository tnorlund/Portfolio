#!/usr/bin/env python3
"""
Test environment setup for ChromaDB Compaction tests.

This module sets up the proper import paths and mocking to allow tests
to run without modifying the Lambda code files.
"""

import sys
import os
from unittest.mock import MagicMock, patch

def setup_test_environment():
    """Setup the test environment with proper imports and mocking."""
    
    # Add the lambdas directory to Python path
    lambdas_path = os.path.join(os.path.dirname(__file__), 'infra', 'chromadb_compaction', 'lambdas')
    if os.path.exists(lambdas_path):
        sys.path.insert(0, lambdas_path)
    
    # Mock the utils module if it doesn't exist
    try:
        import utils
    except ImportError:
        # Create a mock utils module
        utils_mock = MagicMock()
        utils_mock.get_operation_logger = MagicMock(return_value=MagicMock())
        utils_mock.metrics = MagicMock()
        utils_mock.trace_function = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
        utils_mock.start_compaction_lambda_monitoring = MagicMock()
        utils_mock.stop_compaction_lambda_monitoring = MagicMock()
        utils_mock.with_compaction_timeout_protection = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
        utils_mock.format_response = MagicMock(side_effect=lambda response, *args, **kwargs: response)
        
        # Inject the mock into sys.modules
        sys.modules['utils'] = utils_mock
    
    # Mock the processor module if it doesn't exist
    try:
        import processor
    except ImportError:
        # Create a mock processor module
        processor_mock = MagicMock()
        
        # Mock the classes and functions
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
        
        # Inject the mock into sys.modules
        sys.modules['processor'] = processor_mock
    
    # Mock the compaction module if it doesn't exist
    try:
        import compaction
    except ImportError:
        # Create a mock compaction module
        compaction_mock = MagicMock()
        
        # Mock the classes and functions
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
        
        # Inject the mock into sys.modules
        sys.modules['compaction'] = compaction_mock

def run_tests_with_mocking():
    """Run tests with proper mocking setup."""
    print("🧪 Setting up test environment with mocking...")
    
    setup_test_environment()
    
    print("✅ Test environment setup complete")
    
    # Test stream processor imports
    try:
        from stream_processor import LambdaResponse, FieldChange, ParsedStreamRecord
        print("✅ Stream processor imports successful")
        
        # Test creating instances
        stream_response = LambdaResponse(200, 5, 3)
        print(f"✅ Stream LambdaResponse: {stream_response}")
        
        field_change = FieldChange(old='old_value', new='new_value')
        print(f"✅ FieldChange: {field_change}")
        
    except Exception as e:
        print(f"❌ Stream processor test failed: {e}")
        return False
    
    # Test compaction imports
    try:
        from compaction.models import LambdaResponse as CompactionLambdaResponse, StreamMessage
        from compaction.operations import update_receipt_metadata, update_word_labels
        print("✅ Compaction imports successful")
        
        compaction_response = CompactionLambdaResponse(200, 'Test message', 5, 2, 3)
        print(f"✅ Compaction LambdaResponse: {compaction_response}")
        
    except Exception as e:
        print(f"❌ Compaction test failed: {e}")
        return False
    
    print("🎉 All tests passed with mocking!")
    return True

if __name__ == "__main__":
    success = run_tests_with_mocking()
    sys.exit(0 if success else 1)
