# Test Optimization Scripts

Essential scripts for analyzing, optimizing, and running tests with maximum performance.

## Quick Start
```bash
# Run tests locally 
./test_runner.sh receipt_dynamo

# Smart test selection (run only affected tests)
python smart_test_runner.py receipt_dynamo --dry-run
```

## Core Tools

### `test_runner.sh` - Local test runner
```bash
./test_runner.sh receipt_dynamo
./test_runner.sh -t integration -c receipt_dynamo
```

### `run_tests_optimized.py` - Advanced test runner
```bash
python run_tests_optimized.py receipt_dynamo tests/unit --test-type unit
```
Features: Adaptive parallelization, intelligent timeouts, coverage

### `smart_test_runner.py` - Intelligent test selection
```bash
python smart_test_runner.py receipt_dynamo --dry-run
```
Features: File change detection, dependency analysis, test caching


## Performance Impact
- **39 integration test files** optimally distributed across 4 parallel groups
- **62.8min → 15.8min** execution time (4x speedup)
- **Smart selection**: Additional 2-20x speedup for focused changes

## AI Review Tools

### `claude_review_analyzer.py` - PR analysis engine
Core AI review logic with cost optimization

### `cost_optimizer.py` - Budget management
```bash
python cost_optimizer.py --check-budget
```
Smart model selection and usage tracking

## Configuration
- `test_matrix.json` - Optimal test groupings
- `requirements.txt` - Script dependencies