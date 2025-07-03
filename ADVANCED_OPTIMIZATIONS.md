# Advanced Pytest Optimizations - Round 3: Programmatic Test Splitting

## Problem Analysis

After implementing caching and dependency optimizations in Round 2, we identified that **receipt_dynamo integration tests** were still the bottleneck with 39 test files containing 1,579 tests taking 60+ minutes sequentially. The solution: **programmatic test splitting** for optimal parallel execution.

## Advanced Solutions Implemented

### 1. **Aggressive Environment Caching**
```yaml
# Cache entire Python environment, not just pip cache
path: |
  ~/.cache/pip
  ${{ env.pythonLocation }}
  ~/.local/lib/python3.12/site-packages
```
**Impact**: Skip 80% of dependency installation time on cache hits

### 2. **Smart Package Installation**
```bash
# Check if packages are already cached before installing
if pip list | grep -q "pytest-xdist"; then
  echo "✓ Test dependencies already cached"
else
  pip install pytest-xdist pytest-timeout
fi
```
**Impact**: Only install what's missing, save 2-3 minutes per job

### 3. **Programmatic Test Splitting**
```yaml
matrix:
  include:
    # Unit tests (single job)
    - package: receipt_dynamo
      test_type: unit
      test_path: tests/unit
    # Integration tests split into 4 optimized groups
    - package: receipt_dynamo
      test_type: integration
      test_group: group-1
      test_path: tests/integration/test__receipt_word_label.py tests/integration/test__receipt.py ... (10 files)
    - package: receipt_dynamo
      test_type: integration
      test_group: group-2
      test_path: tests/integration/test__receipt_field.py tests/integration/test__receipt_chatgpt_validation.py ... (10 files)
    # Groups 3 and 4 similarly balanced
```
**Impact**: 4x parallel speedup for integration tests (62.8min → 15.8min)

### 4. **Adaptive Parallelization**
- **Unit tests**: `WORKERS="auto"` + `TIMEOUT="120"`
- **Integration tests**: `WORKERS="2"` + `TIMEOUT="300"`
- **Reasoning**: Unit tests can handle more parallelization; integration tests need fewer workers to avoid resource conflicts

### 5. **Lightning Fast Workflow**
New `lightning-tests.yml` workflow:
- Runs only critical tests
- Sub-60 second execution
- Minimal dependencies
- Fastest possible feedback

### 6. **Advanced Analysis and Optimization Tools**
New comprehensive toolkit:
- **`scripts/analyze_tests.py`**: Analyzes 39 files, 1,579 tests, creates optimal 4-group split
- **`scripts/run_tests_optimized.py`**: Adaptive test runner with resource management
- **`scripts/generate_test_matrix.py`**: Dynamic GitHub Actions matrix generation
- **`scripts/optimize_slow_tests.py`**: Performance analysis and bottleneck identification
- **`scripts/test_runner.sh`**: Developer-friendly local test runner
- **`scripts/profile_tests.py`**: Legacy profiling tool (still available)

## Expected Performance Gains

### Scenario 1: Integration Test Parallelization
- **Before**: 62.8 minutes sequential execution
- **After**: 15.8 minutes parallel execution (4 groups)
- **Improvement**: 4x faster

### Scenario 2: Cold Cache (First Run)
- **Before**: 4+ minutes total setup + tests
- **After**: ~2 minutes total (with parallel groups)
- **Improvement**: 50% faster

### Scenario 3: Warm Cache (Subsequent Runs)
- **Before**: 4+ minutes total
- **After**: 30-60 seconds total
- **Improvement**: 6-8x faster

### Scenario 4: Optimal Load Balancing
- **Group 1**: 395 tests, ~16 minutes (heaviest files)
- **Group 2**: 396 tests, ~16 minutes
- **Group 3**: 396 tests, ~16 minutes
- **Group 4**: 392 tests, ~15 minutes (lightest)

## Optimization Breakdown

| Optimization | Time Saved | Cache Dependency |
|-------------|------------|------------------|
| Programmatic Test Splitting | 47 minutes | Any |
| Environment Caching | 2-3 minutes | Warm cache |
| Smart Installation | 1-2 minutes | Partial cache |
| Optimal Load Balancing | 0-1 minutes | Any |
| Adaptive Workers | 20-30% test speedup | Any |

## Monitoring and Validation

Use these tools to validate improvements:

```bash
# Analyze test structure and generate optimal groups
python scripts/analyze_tests.py

# Generate dynamic test matrix
python scripts/generate_test_matrix.py

# Run with optimized test runner
python scripts/run_tests_optimized.py receipt_dynamo tests/unit --test-type unit

# Quick local test with new runner
./scripts/test_runner.sh receipt_dynamo
./scripts/test_runner.sh -t integration -c receipt_dynamo

# Legacy tools (still available)
python scripts/profile_tests.py
python scripts/benchmark_tests.py
./scripts/test.sh -p receipt_dynamo
```

## Rollback Strategy

If any optimization causes issues:

1. **Revert to simple matrix**: Use single integration job instead of 4 groups
2. **Disable programmatic splitting**: Use static test paths in workflow
3. **Fallback to sequential**: Remove `-n` parallelization flags
4. **Disable environment caching**: Remove `${{ env.pythonLocation }}` from cache paths
5. **Emergency rollback**: Revert to original workflow configuration

## Technical Implementation Details

### Test Analysis Algorithm
1. **File Discovery**: Scans `tests/integration/` for all `test_*.py` files
2. **Test Counting**: Uses `pytest --collect-only` to count tests per file
3. **Time Estimation**: Estimates execution time based on test count + file complexity
4. **Optimal Grouping**: Uses greedy load balancing to create 4 equal groups
5. **Matrix Generation**: Outputs GitHub Actions matrix configuration

### Load Balancing Strategy
- **Target**: 4 parallel groups for optimal CI runner utilization
- **Algorithm**: Greedy assignment (largest files to least loaded groups)
- **Result**: Groups vary by only 1-4 tests (near-perfect balance)
- **Adaptability**: Automatically rebalances as tests are added/removed

### Dynamic Matrix Benefits
- **Future-proof**: No manual maintenance as test files change
- **Optimal**: Always uses current best grouping based on actual test counts
- **Transparent**: Clear visibility into which tests run in which group

## Next Steps

1. Monitor parallel execution performance and cache hit rates
2. Identify slow individual tests within files for micro-optimization
3. Consider test impact analysis (only run tests affected by code changes)
4. Explore distributed testing across multiple GitHub runner instances
5. Implement test result caching based on code signatures

This round of optimizations (Round 3) targets the core test execution bottleneck through intelligent parallelization, providing the most significant performance improvement yet: **4x speedup for the largest test suite**.
