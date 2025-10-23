# ChromaDB Compaction System Tests

Comprehensive test suite for the ChromaDB compaction system including stream processor and compaction handler Lambda functions.

## Testing Philosophy

**Core Principle**: Test business logic where it lives, not where it's used.

- **receipt_label package**: Tests its own ChromaDB/S3 business logic with real operations (using moto)
- **Lambda tests**: Test orchestration logic using real receipt_label calls with mocked AWS services
- **Integration tests**: Test cross-service workflows with real services

## Structure

```
tests/
├── fixtures/
│   ├── aws_services.py           # AWS service fixtures with moto
│   ├── stream_events.py           # Sample DynamoDB stream events
│   └── expected_messages.py       # Expected SQS message schemas
├── unit/
│   ├── test_parsing.py            # Entity parsing tests
│   ├── test_change_detection.py   # Change detection logic
│   ├── test_routing_logic.py      # Collection targeting
│   ├── test_compaction_handler_real_operations.py  # Real receipt_label operations
│   └── test_stream_processor_real_operations.py   # Real SQS operations
├── integration/
│   ├── test_aws_service_integration.py  # AWS service testing with moto
│   ├── test_stream_processor_integration.py  # Full Lambda tests
│   └── test_end_to_end_workflow.py  # Complete pipeline testing
├── contract/
│   └── test_message_schemas.py    # Downstream contract tests
├── conftest.py                    # Shared fixtures
└── README.md                      # This file
```

## Test Categories

### Unit Tests (`unit/`)

Fast tests with no external dependencies. Test individual functions and logic:

- **test_parsing.py**: DynamoDB stream record parsing, entity detection
- **test_change_detection.py**: ChromaDB-relevant field change detection
- **test_routing_logic.py**: Collection targeting (lines vs words)
- **test_compaction_handler_real_operations.py**: Lambda orchestration with real receipt_label calls
- **test_stream_processor_real_operations.py**: Stream processor with real SQS operations

### Integration Tests (`integration/`)

Tests with real AWS services (using moto):

- **test_aws_service_integration.py**: AWS service testing with moto
  - SQS queue operations
  - DynamoDB table operations
  - S3 bucket operations
- **test_stream_processor_integration.py**: Full Lambda handler with SQS routing
- **test_end_to_end_workflow.py**: Complete DynamoDB → SQS → S3 → ChromaDB pipeline

### Contract Tests (`contract/`)

Validate message schemas for downstream compatibility:

- **test_message_schemas.py**: Ensures messages match what compaction handler expects
  - Required fields validation
  - JSON serializability
  - Collection targeting rules
  - Timestamp format compliance

### Fixtures (`fixtures/`)

Reusable test data:

- **aws_services.py**: AWS service fixtures with moto
  - `mock_sqs_queues`: Real SQS queues with moto
  - `mock_dynamodb_table`: Real DynamoDB table with moto
  - `mock_s3_bucket`: Real S3 bucket with moto
  - `integration_test_environment`: Complete test environment
- **stream_events.py**: Sample DynamoDB stream events for all entity types
- **expected_messages.py**: Expected message schemas for contract testing

## Running Tests

### ✅ Recommended: Direct Test Runner

**Run all Lambda tests (stream processor + compaction handler):**
```bash
cd /path/to/example
python test_lambdas_direct.py
```

This approach:
- ✅ Works reliably without Pulumi conflicts
- ✅ Tests both Lambda functions with proper mocking
- ✅ Validates imports and basic execution
- ✅ Uses the same mocking pattern for both functions

### 🚀 NEW: Comprehensive AWS Service Testing

**Test AWS services with moto (SQS, DynamoDB, S3):**
```bash
cd /path/to/example/infra/chromadb_compaction
python -m pytest tests/integration/test_aws_service_integration.py -v
```

**Available AWS service fixtures:**
- `mock_sqs_queues`: Real SQS queues with moto
- `mock_dynamodb_table`: Real DynamoDB table with moto  
- `mock_s3_bucket`: Real S3 bucket with moto
- `mock_chromadb_collections`: Mock ChromaDB collections
- `mock_s3_operations`: Mock S3 snapshot operations
- `integration_test_environment`: Complete test environment

**Example usage in tests:**
```python
def test_sqs_integration(mock_sqs_queues):
    """Test SQS message flow with real queues."""
    sqs = mock_sqs_queues["sqs_client"]
    lines_queue_url = mock_sqs_queues["lines_queue_url"]
    
    # Send and receive real messages
    sqs.send_message(QueueUrl=lines_queue_url, MessageBody='{"test": "data"}')
    response = sqs.receive_message(QueueUrl=lines_queue_url)
    assert len(response.get("Messages", [])) == 1

def test_dynamodb_integration(mock_dynamodb_table):
    """Test DynamoDB operations with real table."""
    from receipt_dynamo.data.dynamo_client import DynamoClient
    
    client = DynamoClient(table_name=mock_dynamodb_table)
    # Test real DynamoDB queries
    lines = client.list_receipt_lines_from_receipt("test-image", 1)
    assert isinstance(lines, list)
```

### 🧪 Real Business Logic Testing

**Test receipt_label package with real operations:**
```bash
cd /path/to/example/receipt_label
python -m pytest receipt_label/tests/test_atomic_s3_operations.py -v
python -m pytest receipt_label/tests/test_chromadb_operations.py -v
```

**Test Lambda orchestration with real receipt_label calls:**
```bash
cd /path/to/example/infra/chromadb_compaction
python -m pytest tests/unit/test_compaction_handler_real_operations.py -v
python -m pytest tests/unit/test_stream_processor_real_operations.py -v
```

**Test end-to-end workflows:**
```bash
cd /path/to/example/infra/chromadb_compaction
python -m pytest tests/integration/test_end_to_end_workflow.py -v
```

## Mocking Strategy

### What We Mock vs. What We Test Real

**receipt_label Package Tests:**
- ✅ **Real**: ChromaDB operations, S3 atomic operations
- ✅ **Mocked**: AWS services (using moto)
- ✅ **Real**: Business logic, data transformations, error handling

**Lambda Tests:**
- ✅ **Real**: receipt_label business logic calls
- ✅ **Mocked**: AWS services (SQS, DynamoDB, S3) using moto
- ✅ **Real**: Orchestration logic, error handling, metrics

**Integration Tests:**
- ✅ **Real**: All AWS services using moto
- ✅ **Real**: Cross-service workflows
- ✅ **Real**: Error propagation, retry mechanisms

### Business Logic Boundaries

**receipt_label package tests:**
- ChromaDB collection management
- S3 atomic operations (upload_snapshot_atomic, download_snapshot_atomic)
- ChromaDB metadata updates
- Vector operations (add, update, delete, query)
- Error handling for ChromaDB/S3 failures

**Lambda tests:**
- SQS message processing
- DynamoDB query orchestration
- Error handling and retries
- Lambda-specific concerns (timeouts, memory, metrics)
- Cross-service workflows

**Integration tests:**
- Complete DynamoDB → SQS → S3 → ChromaDB pipeline
- Real data patterns and volumes
- Performance characteristics
- Data consistency guarantees
- Cross-service error scenarios

## Coverage Analysis

**Run coverage analysis:**
```bash
cd /path/to/example
python coverage_analysis.py
```

**Current Coverage:**
- Stream Processor: ~75%
- Compaction Handler: ~53%
- receipt_label S3 Operations: ~85%
- receipt_label ChromaDB Operations: ~70%

## Troubleshooting

### Common Issues

1. **Pulumi Import Conflicts**: Use the direct test runner approach
2. **AWS Service Mocking**: Ensure moto decorators are properly applied
3. **Module Import Issues**: Check sys.path modifications in test files
4. **Test Data Consistency**: Use receipt_dynamo entities for consistent test data

### Debug Mode

**Enable debug logging:**
```bash
export LOG_LEVEL=DEBUG
python test_lambdas_direct.py
```

**Run specific test categories:**
```bash
# Only unit tests
python -m pytest tests/unit/ -v

# Only integration tests  
python -m pytest tests/integration/ -v

# Only contract tests
python -m pytest tests/contract/ -v
```

## Test Data Patterns

### Using receipt_dynamo Entities

**Good**: Use well-tested receipt_dynamo entities
```python
from receipt_dynamo import DynamoClient, ReceiptWord, ReceiptLine

# Create test data using receipt_dynamo entities
test_word = ReceiptWord(
    image_id="test",
    receipt_id=1,
    line_id=1,
    word_id=1,
    text="Target",
    x1=100, y1=100, x2=200, y2=120
)
dynamo_client.put_receipt_word(test_word)
```

**Bad**: Write to DynamoDB directly
```python
# Don't do this - bypasses receipt_dynamo's tested logic
table.put_item(Item={
    "PK": "IMAGE#test#RECEIPT#00001#WORD#00001",
    "SK": "WORD#00001",
    "text": "Target"
})
```

### Real vs. Mocked Testing Examples

**Good**: Test real business logic
```python
@mock_aws
def test_real_s3_operations():
    # Create real S3 bucket with moto
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket="test-bucket")
    
    # Test real atomic operations
    result = upload_snapshot_atomic(
        local_path=test_snapshot,
        bucket="test-bucket",
        collection="lines"
    )
    
    # Verify real S3 operations
    objects = s3_client.list_objects_v2(Bucket="test-bucket")
    assert len(objects.get("Contents", [])) > 0
```

**Bad**: Test mocked functionality
```python
@patch('receipt_label.utils.chroma_s3_helpers.boto3.client')
def test_mocked_s3_operations(mock_boto3):
    # Mock S3 client
    mock_s3_client = MagicMock()
    mock_boto3.return_value = mock_s3_client
    
    # Test nothing real!
    result = upload_snapshot_atomic(...)
    mock_s3_client.put_object.assert_called_once()
```

## Current Status

- ✅ **Stream Processor**: Working with real SQS operations
- ✅ **Enhanced Compaction Handler**: Working with real receipt_label calls
- ✅ **AWS Services**: SQS, DynamoDB, S3 fully tested with moto
- ✅ **receipt_label Package**: Real ChromaDB/S3 operations tested
- ✅ **Test Coverage**: ~75% stream processor, ~53% compaction handler
- ✅ **Integration Tests**: Real AWS service testing available
- ✅ **End-to-End Tests**: Complete pipeline testing available
- ⚠️ **Pytest**: Has Pulumi import conflicts (use specific test files)
- ✅ **Direct Runner**: Reliable and working
