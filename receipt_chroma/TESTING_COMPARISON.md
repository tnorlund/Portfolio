# Testing Comparison: receipt_dynamo vs receipt_chroma

This document compares the testing structure between `receipt_dynamo` (the reference) and `receipt_chroma` to ensure consistency.

## Structure Comparison

### receipt_dynamo Structure ✅
```
receipt_dynamo/
├── conftest.py                    # Root pytest configuration
├── tests/
│   ├── __init__.py
│   ├── unit/                      # Unit tests (no external dependencies)
│   │   ├── test_*.py
│   ├── integration/               # Integration tests (mocked services)
│   │   ├── conftest.py            # Integration-specific fixtures
│   │   ├── test_*.py
│   └── end_to_end/                # E2E tests (real AWS)
│       ├── conftest.py
│       └── test_*.py
```

### receipt_chroma Structure ✅ (Now Matches)
```
receipt_chroma/
├── conftest.py                    # Root pytest configuration
├── tests/
│   ├── __init__.py
│   ├── unit/                      # Unit tests (in-memory ChromaDB)
│   │   ├── test_chroma_client.py
│   └── integration/               # Integration tests (file I/O, persistence)
│       ├── conftest.py            # Integration-specific fixtures
│       └── test_chroma_client.py
```

## Key Similarities

### 1. Root conftest.py
Both packages have a root `conftest.py` that:
- Adds package to Python path
- Configures pytest for parallel execution
- Sets up coverage for xdist

### 2. Test Organization
- **Unit tests**: Fast, isolated, no file I/O
- **Integration tests**: Test actual ChromaDB operations, file persistence, resource management

### 3. Test Markers
Both use the same pytest markers:
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.end_to_end` - E2E tests (for future use)

### 4. Fixtures
Both use pytest fixtures for:
- Test data setup
- Resource cleanup
- Common test scenarios

## Differences (By Design)

### receipt_dynamo
- Uses `moto` to mock AWS services (DynamoDB, S3)
- Integration tests use mocked DynamoDB tables
- E2E tests require real AWS credentials

### receipt_chroma
- Uses temporary directories for file-based ChromaDB
- Integration tests use actual ChromaDB file operations
- Uses `moto` to mock S3 for upload/download tests (matching receipt_dynamo pattern)
- Tests S3 upload scenarios after closing client (critical for issue #5868)

## Test Coverage Comparison

### receipt_dynamo
- **Unit tests**: Entity validation, serialization, data transformations
- **Integration tests**: DynamoDB operations, CRUD operations, queries
- **E2E tests**: Real AWS operations

### receipt_chroma
- **Unit tests**:
  - Context manager behavior
  - Resource management (close())
  - API validation
  - Error handling
- **Integration tests**:
  - File persistence
  - File lock release (critical for issue #5868)
  - S3 upload with moto (matching receipt_dynamo pattern)
  - S3 upload after close() to prevent corruption
  - Multi-collection operations
  - Query/upsert workflows

## Running Tests

### receipt_dynamo
```bash
# Run all tests
pytest

# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run with coverage
pytest --cov=receipt_dynamo
```

### receipt_chroma
```bash
# Run all tests
pytest

# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run with coverage
pytest --cov=receipt_chroma
```

## Fixture Comparison

### receipt_dynamo Integration Fixtures
- `dynamodb_table`: Mocked DynamoDB table
- `s3_bucket`: Mocked S3 bucket
- `expected_results`: Load test data from JSON files

### receipt_chroma Integration Fixtures
- `temp_chromadb_dir`: Temporary directory for ChromaDB
- `chroma_client_read`: Read-only client fixture
- `chroma_client_write`: Write-enabled client fixture
- `chroma_client_delta`: Delta-mode client fixture
- `populated_chroma_db`: Pre-populated database for query tests
- `s3_bucket`: Mocked S3 bucket (using moto, matches receipt_dynamo pattern)

## Best Practices (Both Packages)

1. **Separation of Concerns**:
   - Unit tests: Fast, isolated, no I/O
   - Integration tests: Test actual operations

2. **Fixture Reuse**:
   - Common fixtures in `conftest.py`
   - Test-specific fixtures in test files

3. **Test Markers**:
   - Always mark tests with appropriate category
   - Allows selective test execution

4. **Cleanup**:
   - Fixtures handle cleanup automatically
   - Use context managers where possible

5. **Error Handling**:
   - Test both success and failure cases
   - Verify error messages and types

## Moto Usage

Both packages use `moto` for AWS service mocking:

### receipt_dynamo
- Uses `moto` to mock DynamoDB tables
- Uses `moto` to mock S3 buckets
- All integration tests use `@mock_aws` decorator

### receipt_chroma
- Uses `moto` to mock S3 buckets for upload/download tests
- Tests S3 operations after closing client (critical for issue #5868)
- ChromaDB operations use local files (no mocking needed)

## Future Enhancements

### receipt_chroma
- Add E2E tests for real S3 upload/download scenarios
- Add performance tests for large datasets
- Add concurrent access tests
- Add tests for HTTP client mode

## Conclusion

The `receipt_chroma` test structure now matches `receipt_dynamo`'s proven patterns:
- ✅ Root conftest.py
- ✅ Separated unit/integration tests
- ✅ Proper test markers
- ✅ Comprehensive fixtures
- ✅ Clean organization

This ensures consistency across packages and makes it easier for developers to understand and contribute to both codebases.

