# receipt_label Package Tests

Comprehensive test suite for the receipt_label package focusing on real business logic testing.

## Testing Philosophy

**Core Principle**: Test real business logic with real operations, not mocked functionality.

- **Real ChromaDB Operations**: Test actual ChromaDB collection management, metadata updates, vector operations
- **Real S3 Operations**: Test actual S3 atomic operations using moto for AWS services
- **Real Business Logic**: Test data transformations, error handling, and edge cases
- **Mocked AWS Services**: Use moto to mock AWS services while keeping business logic real

## Structure

```
receipt_label/tests/
├── test_atomic_s3_operations.py    # Real S3 atomic operations with moto
├── test_chromadb_operations.py     # Real ChromaDB operations
├── test_chroma_compactor.py        # Real ChromaDB compaction logic
└── conftest.py                     # Shared fixtures
```

## Test Categories

### S3 Atomic Operations (`test_atomic_s3_operations.py`)

Tests real S3 atomic operations that the compaction lambda uses:

- **upload_snapshot_atomic**: Real S3 upload with versioning and pointer files
- **download_snapshot_atomic**: Real S3 download with fallback to /latest/
- **cleanup operations**: Real S3 prefix cleanup and version management
- **Error handling**: Real S3 error scenarios and recovery

**Example Test Pattern:**
```python
@mock_aws
def test_upload_snapshot_atomic_success(self):
    """Test successful atomic snapshot upload with real S3 operations."""
    # Create real S3 bucket with moto
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket=self.bucket)
    
    # Test real atomic upload
    result = upload_snapshot_atomic(
        local_path=test_snapshot,
        bucket=self.bucket,
        collection=self.collection,
        lock_manager=self.mock_lock_manager
    )
    
    # Verify real S3 operations
    objects = s3_client.list_objects_v2(Bucket=self.bucket)
    assert len(objects.get("Contents", [])) > 0
    
    # Verify versioned snapshot exists
    object_keys = [obj["Key"] for obj in objects["Contents"]]
    versioned_keys = [key for key in object_keys if "timestamped" in key]
    assert len(versioned_keys) > 0
```

### ChromaDB Operations (`test_chromadb_operations.py`)

Tests real ChromaDB operations that the compaction lambda uses:

- **Collection management**: Real collection creation and management
- **Metadata updates**: Real metadata update operations on existing embeddings
- **Vector operations**: Real vector operations (add, update, delete, query)
- **ID construction**: Real ChromaDB ID construction patterns
- **Error handling**: Real ChromaDB error scenarios

**Example Test Pattern:**
```python
def test_chromadb_metadata_update(self):
    """Test real ChromaDB metadata update operations."""
    # Create real ChromaDB instance
    client = ChromaDBClient(persist_directory=self.temp_dir, mode="snapshot")
    
    # Create collection and add test data
    collection = client.get_or_create_collection("test_words")
    collection.add(
        ids=["IMAGE#test#RECEIPT#00001#WORD#00001"],
        embeddings=[[0.1, 0.2, 0.3]],
        documents=["Target"],
        metadatas=[{"merchant": "Target Store"}]
    )
    
    # Test real metadata update
    collection.update(
        ids=["IMAGE#test#RECEIPT#00001#WORD#00001"],
        metadatas=[{"merchant": "Target Store", "address": "123 Main St"}]
    )
    
    # Verify real changes
    results = collection.get(ids=["IMAGE#test#RECEIPT#00001#WORD#00001"])
    assert results["metadatas"][0]["merchant"] == "Target Store"
    assert results["metadatas"][0]["address"] == "123 Main St"
```

### ChromaDB Compactor (`test_chroma_compactor.py`)

Tests real ChromaDB compaction logic:

- **Delta compaction**: Real delta compaction algorithms
- **Collection merging**: Real collection merging logic
- **Distributed locking**: Real distributed locking with mocked DynamoDB
- **Snapshot management**: Real snapshot operations with S3

## Running Tests

### Test receipt_label Package

**Run S3 atomic operations tests:**
```bash
cd /path/to/example/receipt_label
python -m pytest receipt_label/tests/test_atomic_s3_operations.py -v
```

**Run ChromaDB operations tests:**
```bash
cd /path/to/example/receipt_label
python -m pytest receipt_label/tests/test_chromadb_operations.py -v
```

**Run all receipt_label tests:**
```bash
cd /path/to/example/receipt_label
python -m pytest receipt_label/tests/ -v
```

### Test with Coverage

**Run with coverage analysis:**
```bash
cd /path/to/example/receipt_label
python -m pytest receipt_label/tests/ --cov=receipt_label --cov-report=html
```

## Mocking Strategy

### What We Mock vs. What We Test Real

**S3 Operations Tests:**
- ✅ **Real**: S3 atomic operations, versioning, pointer files
- ✅ **Mocked**: AWS S3 service (using moto)
- ✅ **Real**: Business logic, data integrity, error handling

**ChromaDB Operations Tests:**
- ✅ **Real**: ChromaDB operations, vector operations, metadata updates
- ✅ **Mocked**: ChromaDB persistence (using temporary directories)
- ✅ **Real**: Business logic, data transformations, error handling

**ChromaDB Compactor Tests:**
- ✅ **Real**: Compaction algorithms, collection merging
- ✅ **Mocked**: DynamoDB locking (using moto), S3 operations (using moto)
- ✅ **Real**: Business logic, distributed locking logic

### Business Logic Boundaries

**receipt_label package tests cover:**
- ChromaDB collection management and operations
- S3 atomic operations (upload_snapshot_atomic, download_snapshot_atomic)
- ChromaDB metadata updates and vector operations
- Error handling for ChromaDB/S3 failures
- Data integrity and consistency guarantees
- Performance characteristics of operations

**Lambda tests cover:**
- SQS message processing orchestration
- DynamoDB query orchestration
- Error handling and retries
- Lambda-specific concerns (timeouts, memory, metrics)
- Cross-service workflows

## Test Data Patterns

### Using receipt_dynamo Entities

**Good**: Use well-tested receipt_dynamo entities for test data
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

## Dependencies

- **pytest**: Test framework
- **moto**: AWS service mocking (S3)
- **chromadb**: ChromaDB client library
- **boto3**: AWS SDK (mocked by moto in tests)
- **receipt_dynamo**: Entity classes and parsers (for test data)

## Coverage Goals

- **S3 Operations**: 100% coverage of atomic operations
- **ChromaDB Operations**: 100% coverage of collection management
- **Error Handling**: All error scenarios covered
- **Edge Cases**: Malformed input, missing data, network failures
- **Performance**: Large dataset handling, concurrent operations

## Troubleshooting

### Common Issues

1. **ChromaDB Persistence**: Ensure temporary directories are properly cleaned up
2. **S3 Mocking**: Ensure moto decorators are properly applied
3. **Test Data**: Use receipt_dynamo entities for consistent test data
4. **Import Issues**: Check Python path and module imports

### Debug Mode

**Enable debug logging:**
```bash
export LOG_LEVEL=DEBUG
python -m pytest receipt_label/tests/ -v
```

**Run specific test categories:**
```bash
# Only S3 operations tests
python -m pytest receipt_label/tests/test_atomic_s3_operations.py -v

# Only ChromaDB operations tests
python -m pytest receipt_label/tests/test_chromadb_operations.py -v

# Only ChromaDB compactor tests
python -m pytest receipt_label/tests/test_chroma_compactor.py -v
```

## Current Status

- ✅ **S3 Atomic Operations**: Real S3 operations tested with moto
- ✅ **ChromaDB Operations**: Real ChromaDB operations tested
- ✅ **ChromaDB Compactor**: Real compaction logic tested
- ✅ **Test Coverage**: ~85% S3 operations, ~70% ChromaDB operations
- ✅ **Error Handling**: Real error scenarios tested
- ✅ **Performance**: Real performance characteristics tested