# Test Coverage Recommendations for Poll Step Optimizations

## Priority 1: Critical Path Tests (Must Have)

### 1. Batched Chunk Processing Tests
**File:** `infra/embedding_step_functions/unified_embedding/handlers/tests/test_batched_chunk_processing.py`

```python
"""Tests for batched chunk processing optimization."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from handlers.compaction import process_chunk_handler, _process_single_chunk

class TestBatchedChunkProcessing:
    """Test batched chunk processing with multiple chunks per Lambda."""

    def test_process_single_chunk_index_backward_compatibility(self):
        """Test that single chunk_index still works (backward compatibility)."""
        event = {
            "batch_id": "test-batch",
            "chunk_index": 0,
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines"
        }
        # Should process successfully and return intermediate_key
        # ... test implementation

    def test_process_multiple_chunk_indices(self):
        """Test processing multiple chunks in one Lambda invocation."""
        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1, 2, 3],  # 4 chunks
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines"
        }
        # Should process 4 chunks and return single merged intermediate_key
        # ... test implementation

    def test_process_chunk_with_empty_chunks(self):
        """Test handling of empty chunks in batch."""
        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1, 2],  # Some may be empty
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines"
        }
        # Should skip empty chunks and process valid ones
        # ... test implementation

    def test_process_chunk_all_empty(self):
        """Test handling when all chunks are empty."""
        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1],  # All empty
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines"
        }
        # Should return appropriate empty response
        # ... test implementation
```

### 2. N-way Merge Tests
**File:** `infra/embedding_step_functions/simple_lambdas/prepare_merge_pairs/test_handler.py`

```python
"""Tests for N-way merge optimization."""
import pytest
from handler import handle

class TestNWayMerge:
    """Test N-way merge grouping logic."""

    def test_merge_group_size_10(self, monkeypatch):
        """Test that intermediates are grouped into groups of 10."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        # Create 25 intermediates
        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(25)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0
        }

        result = handle(event, None)

        # Should create 3 groups: [10, 10, 5]
        assert result["done"] is False
        assert result["pair_count"] == 3
        assert len(result["pairs"][0]["intermediates"]) == 10
        assert len(result["pairs"][1]["intermediates"]) == 10
        assert len(result["pairs"][2]["intermediates"]) == 5

    def test_merge_group_size_configurable(self, monkeypatch):
        """Test that MERGE_GROUP_SIZE is configurable."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "8")

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(20)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0
        }

        result = handle(event, None)

        # Should create 3 groups: [8, 8, 4]
        assert result["pair_count"] == 3

    def test_single_intermediate_returns_done(self):
        """Test that single intermediate returns done=True."""
        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": [{"intermediate_key": "intermediate/batch/chunk-0/"}],
            "round": 0
        }

        result = handle(event, None)

        assert result["done"] is True
        assert result["single_intermediate"]["intermediate_key"] == "intermediate/batch/chunk-0/"
```

### 3. Normalize Batching Tests
**File:** `infra/embedding_step_functions/simple_lambdas/normalize_poll_batches_data/test_handler.py`

```python
"""Tests for chunk batching optimization."""
import pytest
from handler import lambda_handler

class TestChunkBatching:
    """Test chunk batching logic in normalize handler."""

    def test_chunks_per_lambda_default(self, monkeypatch):
        """Test default CHUNKS_PER_LAMBDA=4."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")

        # Mock poll results with 16 deltas
        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "poll_results": [
                {"delta_key": f"delta/{i}/", "collection": "lines"}
                for i in range(16)
            ]
        }

        result = lambda_handler(event, None)

        # With CHUNK_SIZE_LINES=25 and CHUNKS_PER_LAMBDA=4:
        # 16 deltas = 1 chunk, so we'd have 1 batch with 1 chunk
        # But let's test with more deltas

    def test_chunk_batching_creates_correct_structure(self, monkeypatch):
        """Test that chunk batches have correct structure."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "2")  # Small for testing

        # 16 deltas = 8 chunks, batched into 2 batches of 4 chunks each
        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "poll_results": [
                {"delta_key": f"delta/{i}/", "collection": "lines"}
                for i in range(16)
            ]
        }

        result = lambda_handler(event, None)

        assert result["total_chunks"] == 8
        assert result["total_batches"] == 2
        assert len(result["chunks"]) == 2

        # First batch should have chunk_indices [0,1,2,3]
        assert result["chunks"][0]["chunk_indices"] == [0, 1, 2, 3]
        # Second batch should have chunk_indices [4,5,6,7]
        assert result["chunks"][1]["chunk_indices"] == [4, 5, 6, 7]
```

## Priority 2: Integration Tests (Should Have)

### 4. Step Function Workflow Test
**File:** `infra/embedding_step_functions/tests/test_optimized_workflow_integration.py`

```python
"""Integration test for optimized step function workflow."""
import pytest
import boto3
from moto import mock_stepfunctions, mock_s3, mock_dynamodb

@pytest.mark.integration
class TestOptimizedWorkflow:
    """Test the full optimized step function workflow."""

    @mock_stepfunctions
    @mock_s3
    @mock_dynamodb
    def test_full_workflow_with_batched_processing(self):
        """Test complete workflow from poll to final merge with batching."""
        # 1. Set up mock AWS services
        # 2. Create step function with optimized definition
        # 3. Execute step function
        # 4. Verify:
        #    - Chunk batches are created correctly
        #    - N-way merge groups are formed
        #    - Final snapshot is produced
        #    - Total Lambda invocations reduced as expected
        pass
```

## Priority 3: Performance Tests (Nice to Have)

### 5. Performance Benchmark Test
**File:** `infra/embedding_step_functions/tests/test_optimization_benchmarks.py`

```python
"""Benchmark tests to verify optimization performance gains."""
import pytest

class TestOptimizationBenchmarks:
    """Benchmark tests for optimization performance."""

    def test_invocation_count_reduction(self):
        """Verify Lambda invocation reduction for various chunk counts."""
        test_cases = [
            (16, 4, 4),   # 16 chunks, batch size 4 → 4 invocations
            (32, 4, 8),   # 32 chunks, batch size 4 → 8 invocations
            (64, 4, 16),  # 64 chunks, batch size 4 → 16 invocations
        ]

        for chunk_count, batch_size, expected_invocations in test_cases:
            actual = calculate_invocation_count(chunk_count, batch_size)
            assert actual == expected_invocations

    def test_merge_rounds_reduction(self):
        """Verify merge round reduction with N-way merge."""
        test_cases = [
            (16, 10, 2),   # 16 intermediates, group size 10 → 2 rounds
            (32, 10, 2),   # 32 intermediates, group size 10 → 2 rounds
            (64, 10, 2),   # 64 intermediates, group size 10 → 2 rounds
            (100, 10, 2),  # 100 intermediates, group size 10 → 2 rounds
        ]

        for intermediate_count, group_size, expected_rounds in test_cases:
            actual = calculate_merge_rounds(intermediate_count, group_size)
            assert actual == expected_rounds
```

## Implementation Priority

1. **Start with Priority 1 tests** - These cover the critical new code paths
2. **Add mocking where needed** - Mock S3, DynamoDB, and ChromaDB operations
3. **Use pytest fixtures** - Leverage existing fixtures from `receipt_chroma/tests`
4. **Run tests locally** before deploying to AWS
5. **Add CI/CD integration** - Ensure tests run on every commit

## Test Coverage Goals

- **Unit tests:** 80%+ coverage for new code
- **Integration tests:** At least 1 end-to-end test per workflow
- **Performance tests:** Validate optimization claims (85% reduction, etc.)

## Next Steps

1. Create test files following the structure above
2. Implement tests incrementally (start with batched chunk processing)
3. Run tests: `pytest infra/embedding_step_functions -v`
4. Add tests to CI/CD pipeline
5. Monitor test results in production deployments
