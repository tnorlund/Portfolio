# Testing the Poll Step Optimizations

This directory contains tests for the poll step Lambda optimizations, including:
- Batched chunk processing
- N-way merge grouping
- Chunk batching in normalize handler

## Running the Tests

### Prerequisites

```bash
# Install test dependencies
pip install pytest pytest-mock

# Install boto3 and other AWS dependencies
pip install boto3 moto
```

### Run All Tests

```bash
# From project root
pytest infra/embedding_step_functions -v

# Run with coverage
pytest infra/embedding_step_functions --cov=infra/embedding_step_functions --cov-report=html
```

### Run Specific Test Files

```bash
# Batched chunk processing tests
pytest infra/embedding_step_functions/unified_embedding/handlers/tests/test_batched_chunk_processing.py -v

# N-way merge tests
pytest infra/embedding_step_functions/simple_lambdas/prepare_merge_pairs/test_handler.py -v

# Normalize batching tests
pytest infra/embedding_step_functions/simple_lambdas/normalize_poll_batches_data/test_handler.py -v
```

### Run Tests with Detailed Output

```bash
# Show print statements and detailed output
pytest infra/embedding_step_functions -v -s

# Show only failing tests
pytest infra/embedding_step_functions -v --tb=short

# Run tests in parallel (faster)
pip install pytest-xdist
pytest infra/embedding_step_functions -v -n auto
```

## Test Coverage

The tests cover:

### 1. Batched Chunk Processing (`test_batched_chunk_processing.py`)
- ✅ Backward compatibility with single `chunk_index`
- ✅ Processing multiple `chunk_indices` in one invocation
- ✅ Handling empty chunks
- ✅ Handling all-empty chunks
- ✅ Order preservation during processing
- ✅ Error handling and exceptions

### 2. N-way Merge (`test_handler.py` in prepare_merge_pairs)
- ✅ Grouping into groups of 10 (configurable)
- ✅ Configurable `MERGE_GROUP_SIZE`
- ✅ Single intermediate returns `done=True`
- ✅ Zero intermediates handling
- ✅ Order preservation in groups
- ✅ Filtering invalid intermediates
- ✅ Round number incrementing
- ✅ Backward compatibility of field names
- ✅ Pass-through of poll_results S3 keys

### 3. Normalize Batching (`test_handler.py` in normalize_poll_batches_data)
- ✅ Default `CHUNKS_PER_LAMBDA=4`
- ✅ Correct chunk batch structure
- ✅ Different batch sizes
- ✅ Empty delta handling
- ✅ S3 keys passed correctly
- ✅ Poll results keys preserved
- ✅ Exact multiples of batch size
- ✅ Single delta edge case
- ✅ Words vs Lines database chunk sizes

## Mocking Strategy

The tests use mocking to avoid AWS dependencies:

```python
# Mock S3 operations
@patch("handlers.compaction.s3_client")

# Mock ChromaDB operations
@patch("handlers.compaction.process_chunk_deltas")

# Mock environment variables
def test_something(self, monkeypatch):
    monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")
```

## CI/CD Integration

Add to your GitHub Actions workflow:

```yaml
- name: Run poll step optimization tests
  run: |
    pytest infra/embedding_step_functions/unified_embedding/handlers/tests/test_batched_chunk_processing.py -v
    pytest infra/embedding_step_functions/simple_lambdas/prepare_merge_pairs/test_handler.py -v
    pytest infra/embedding_step_functions/simple_lambdas/normalize_poll_batches_data/test_handler.py -v
```

## Expected Test Results

All tests should pass with:
- ✅ 0 failures
- ✅ 100% coverage of new optimization code
- ✅ < 5 seconds total execution time (mocked tests are fast)

## Troubleshooting

### Import Errors

If you get import errors, ensure you're running from the project root:

```bash
cd /home/user/Portfolio
export PYTHONPATH="${PYTHONPATH}:$(pwd)/infra/embedding_step_functions"
pytest infra/embedding_step_functions -v
```

### Environment Variable Tests

Some tests require reloading modules to pick up environment variable changes:

```python
import importlib
importlib.reload(handler)
```

This is expected and handled in the tests.

### Mock Patches

Ensure patch paths match the actual import paths in the handlers:
- `handlers.compaction` for compaction handler
- `handler` for simple_lambdas handlers

## Next Steps

After tests pass:
1. Deploy to dev environment
2. Monitor CloudWatch logs
3. Verify Lambda invocation reduction
4. Compare performance metrics (before/after)
5. Promote to production after validation
