"""
Test fixtures for ChromaDB Compaction tests.

This module provides reusable test fixtures for AWS services,
DynamoDB entities, and SQS message testing.
"""

from .aws_services import (
    mock_sqs_queues,
    mock_dynamodb_table,
    mock_s3_bucket,
    mock_chromadb_collections,
    mock_s3_operations,
    mock_dynamo_client,
    aws_test_environment,
    integration_test_environment,
)

from .stream_events import (
    TARGET_METADATA_UPDATE_EVENT,
    WORD_LABEL_UPDATE_EVENT,
    WORD_LABEL_REMOVE_EVENT,
    COMPACTION_RUN_INSERT_EVENT,
    target_event_factory,
    word_label_event_factory,
    compaction_run_event_factory,
)

from .expected_messages import (
    METADATA_MESSAGE_SCHEMA,
    LABEL_MESSAGE_SCHEMA,
    COMPACTION_RUN_MESSAGE_SCHEMA,
    validate_message_schema,
)

__all__ = [
    # AWS Service Fixtures
    "mock_sqs_queues",
    "mock_dynamodb_table", 
    "mock_s3_bucket",
    "mock_chromadb_collections",
    "mock_s3_operations",
    "mock_dynamo_client",
    "aws_test_environment",
    "integration_test_environment",
    
    # Stream Event Fixtures
    "TARGET_METADATA_UPDATE_EVENT",
    "WORD_LABEL_UPDATE_EVENT", 
    "WORD_LABEL_REMOVE_EVENT",
    "COMPACTION_RUN_INSERT_EVENT",
    "target_event_factory",
    "word_label_event_factory",
    "compaction_run_event_factory",
    
    # Message Schema Fixtures
    "METADATA_MESSAGE_SCHEMA",
    "LABEL_MESSAGE_SCHEMA",
    "COMPACTION_RUN_MESSAGE_SCHEMA", 
    "validate_message_schema",
]
