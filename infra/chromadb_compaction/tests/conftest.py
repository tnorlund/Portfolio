"""
Pytest configuration for ChromaDB Compaction tests.

This module sets up proper mocking and import paths to allow tests
to run without modifying the Lambda code files.
"""

import sys
import os
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Setup the test environment with proper imports and mocking."""
    
    # Add the lambdas directory to Python path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    lambdas_path = os.path.join(current_dir, '..', 'lambdas')
    if os.path.exists(lambdas_path):
        sys.path.insert(0, lambdas_path)
    
    # Mock the utils module
    utils_mock = MagicMock()
    logger_mock = MagicMock()
    logger_mock.info = MagicMock()
    logger_mock.error = MagicMock()
    logger_mock.debug = MagicMock()
    logger_mock.exception = MagicMock()
    logger_mock.warning = MagicMock()
    logger_mock.critical = MagicMock()
    
    utils_mock.get_operation_logger = MagicMock(return_value=logger_mock)
    utils_mock.metrics = MagicMock()
    utils_mock.trace_function = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
    utils_mock.trace_compaction_operation = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
    utils_mock.start_compaction_lambda_monitoring = MagicMock()
    utils_mock.stop_compaction_lambda_monitoring = MagicMock()
    utils_mock.with_compaction_timeout_protection = MagicMock(side_effect=lambda *args, **kwargs: lambda func: func)
    utils_mock.format_response = MagicMock(side_effect=lambda response, *args, **kwargs: response)
    
    # Inject the mock into sys.modules
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
    
    # Inject the mock into sys.modules
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

# Import AWS service fixtures
from .fixtures.aws_services import (
    mock_sqs_queues,
    mock_dynamodb_table,
    mock_s3_bucket,
    mock_chromadb_collections,
    mock_s3_operations,
    mock_dynamo_client,
    aws_test_environment,
    integration_test_environment,
)

# Import stream event fixtures
from .fixtures.stream_events import (
    TARGET_METADATA_UPDATE_EVENT as target_metadata_event,
    WORD_LABEL_UPDATE_EVENT as word_label_update_event,
    WORD_LABEL_REMOVE_EVENT as word_label_remove_event,
    COMPACTION_RUN_INSERT_EVENT as compaction_run_insert_event,
    target_event_factory,
    word_label_event_factory,
    compaction_run_event_factory,
)

# Make fixtures available to all tests
pytest_plugins = ["fixtures.aws_services"]
