# AWS Service Coverage Test Plan

## Current Status: ❌ Inadequate AWS Service Coverage

Our current tests are **NOT** properly testing the AWS services that both Lambda functions depend on. This is a significant gap that needs to be addressed.

## AWS Services Used by Each Lambda

### Stream Processor Lambda
- **DynamoDB Streams**: Receives change events
- **SQS**: Publishes to `LINES_QUEUE_URL` and `WORDS_QUEUE_URL`
- **CloudWatch**: Logging and metrics

### Enhanced Compaction Handler Lambda  
- **SQS**: Receives messages from stream processor
- **DynamoDB**: Queries for receipt lines/words
- **S3**: Downloads/uploads ChromaDB snapshots (`CHROMADB_BUCKET`)
- **CloudWatch**: Logging and metrics

## Critical Test Coverage Gaps

### 🚨 High Priority (Critical for Production)

1. **S3 Operations** - Most critical gap
   - Snapshot downloads/uploads
   - Atomic operations (`download_snapshot_atomic`, `upload_snapshot_atomic`)
   - Bucket permissions and access
   - Error handling for S3 failures
   - Concurrent access scenarios

2. **DynamoDB Queries** - Essential for ChromaDB ID construction
   - `list_receipt_words_from_receipt()` queries
   - `list_receipt_lines_from_receipt()` queries  
   - Query performance and timeout handling
   - Error handling for DynamoDB failures

3. **SQS Message Processing** - Core functionality
   - Message parsing and validation
   - Batch processing (>10 messages)
   - Dead letter queue handling
   - Partial batch failures (`batchItemFailures`)

### 🔶 Medium Priority

4. **Cross-Service Integration** - End-to-end workflows
   - DynamoDB → SQS → S3 → ChromaDB pipeline
   - Error propagation between services
   - Retry mechanisms and circuit breakers

5. **CloudWatch Integration** - Observability
   - Metrics publishing
   - Structured logging
   - Correlation IDs
   - Performance monitoring

## Recommended Test Implementation

### Option 1: Enhanced Moto Testing (Recommended)
```python
# Use moto to mock AWS services with realistic behavior
from moto import mock_s3, mock_dynamodb, mock_sqs

@mock_s3
@mock_dynamodb  
@mock_sqs
def test_end_to_end_workflow():
    # Setup real AWS service mocks
    # Test actual S3 operations
    # Test actual DynamoDB queries
    # Test actual SQS message flow
```

### Option 2: LocalStack Testing
```python
# Use LocalStack for more realistic AWS service simulation
# Better for integration testing
# Requires LocalStack setup
```

### Option 3: Hybrid Approach
```python
# Combine moto for unit tests with LocalStack for integration tests
# Use real AWS services for end-to-end tests in dev environment
```

## Test Categories Needed

### Unit Tests (with moto)
- S3 snapshot operations
- DynamoDB query operations  
- SQS message processing
- Error handling scenarios

### Integration Tests (with moto/LocalStack)
- Cross-service workflows
- Message routing and processing
- Snapshot consistency
- Performance under load

### Contract Tests
- Message schema validation
- Service interface compliance
- Error response formats

## Implementation Priority

1. **Week 1**: S3 operations testing (most critical)
2. **Week 2**: DynamoDB query testing
3. **Week 3**: SQS message processing testing
4. **Week 4**: Cross-service integration testing

## Current Test Status

- **Stream Processor**: ~75% coverage (but missing AWS service testing)
- **Enhanced Compaction Handler**: ~53% coverage (but missing AWS service testing)
- **AWS Services**: ~0% coverage (all mocked, not tested)

## Action Required

The current test coverage is **insufficient for production deployment**. We need to implement comprehensive AWS service testing to ensure reliability and catch integration issues before they reach production.
