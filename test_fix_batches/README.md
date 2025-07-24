# Test Fix Batches - Parallelization Strategy

## Overview
This directory contains batch files for parallelizing the fixing of integration test failures in the receipt_dynamo package. Currently at **164 total failures** across 22 files.

## Batch Organization

### Batch 1: High Priority (64 failures) - `batch_1_high_priority.md`
**Files**: 3 files with 20+ failures each
- Most impactful fixes
- Complex patterns requiring attention
- **Target**: Reduce 64 → 20 failures

### Batch 2: Medium Priority (41 failures) - `batch_2_medium_priority.md` 
**Files**: 3 files with 10-17 failures each
- Parameter name consistency issues
- Exception type standardization
- **Target**: Reduce 41 → 15 failures

### Batch 3: Standard Patterns (36 failures) - `batch_3_standard_patterns.md`
**Files**: 6 files with 5-7 failures each  
- Straightforward pattern applications
- Validation message format updates
- **Target**: Reduce 36 → 12 failures

### Batch 4: Low Volume (25 failures) - `batch_4_low_volume.md`
**Files**: 10 files with 1-4 failures each
- Edge cases and isolated issues
- Quick individual fixes
- **Target**: Reduce 25 → 8 failures

## Execution Strategy

### Parallel Approach
1. **Assign batches to different workers** (human + Codex instances)
2. **Each batch is independent** - no conflicts between batches
3. **Commit each batch separately** with clear progress tracking
4. **Verify results** after each batch completion

### Success Metrics
- **Current**: 164 failures across 22 files
- **Target**: ~55 total failures (66% reduction)
- **Stretch Goal**: Under 40 failures (76% reduction)

### Time Estimates
- **Batch 1**: 30-45 minutes (highest complexity)
- **Batch 2**: 20-30 minutes (medium complexity)  
- **Batch 3**: 15-25 minutes (standard patterns)
- **Batch 4**: 20-30 minutes (many small fixes)

**Total Sequential Time**: 85-130 minutes
**Total Parallel Time**: 30-45 minutes (limited by slowest batch)

## Progress Tracking

### Before Starting
```bash
python -m pytest tests/integration/ -n auto --tb=no -q | tail -1
# Should show: 164 failed, 1320 passed
```

### After Each Batch
```bash
git add -A && git commit -m "fix: Complete batch X - reduced Y failures to Z"
python -m pytest tests/integration/ -n auto --tb=no -q | tail -1
```

### Final Verification
```bash
# Target result: ~55 failed, ~1429 passed
python -m pytest tests/integration/ -n auto --tb=short -q
```

## Key Patterns Being Fixed

1. **Exception Types**: `Exception` → `EntityAlreadyExistsError`/`EntityNotFoundError`/`DynamoDBError`
2. **Validation Messages**: `"X parameter is required and cannot be None"` → `"x cannot be None"`
3. **Parameter Names**: `"receiptlabelanalysis"` → `"receipt_label_analysis"`
4. **Error Content**: `"Unknown error"` → `"Something unexpected"`

## Common Imports Needed
```python
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    DynamoDBError,
    DynamoDBThroughputError,
    DynamoDBServerError,
    DynamoDBValidationError,
    DynamoDBAccessError,
)
```

## Dependencies Between Batches
**None** - All batches can be executed simultaneously without conflicts.

---

**Current Status**: Ready for parallel execution
**Next Step**: Assign batches to workers and begin parallel processing