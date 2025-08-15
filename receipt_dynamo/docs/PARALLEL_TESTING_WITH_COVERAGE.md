# Running Tests in Parallel with Coverage

## The Problem
When using `pytest-xdist` for parallel test execution (`-n auto`), coverage data collection often fails because each worker process needs special configuration to properly report coverage data.

## The Solution

### 1. Configuration Files Created

#### `.coveragerc`
- Configures coverage for parallel execution with `parallel = True`
- Sets `concurrency = multiprocessing` to handle multiple processes
- Defines source paths and exclusion patterns

#### `pyproject.toml` Updates
- Added coverage configuration under `[tool.coverage.run]`
- Set `parallel = true` and `concurrency = ["multiprocessing"]`
- Removed coverage from default `addopts` to allow flexible execution

#### Root `conftest.py`
- Added pytest configuration for better test discovery
- Includes coverage process startup for parallel execution

### 2. Running Tests in Parallel

#### Option 1: Use the provided script
```bash
./run_tests_parallel.sh tests/integration/test__receipt.py
```

This script:
- Cleans up previous coverage data
- Sets necessary environment variables
- Runs tests with proper coverage configuration
- Combines coverage data from all workers

#### Option 2: Manual execution
```bash
# Without coverage (fastest)
pytest tests/integration/test__receipt.py -n auto --no-cov

# With coverage (using environment variable)
export COVERAGE_CORE=sysmon
pytest tests/integration/test__receipt.py -n auto --cov=receipt_dynamo

# Run coverage separately
pytest tests/integration/test__receipt.py -n auto --no-cov
pytest tests/integration/test__receipt.py --cov=receipt_dynamo --cov-report=term-missing
```

#### Option 3: Sequential with coverage (most reliable)
```bash
# Run without -n flag for sequential execution with coverage
pytest tests/integration/test__receipt.py --cov=receipt_dynamo
```

## Best Practices

1. **For CI/CD**: Use sequential execution with coverage for reliability
2. **For local development**: Use parallel execution without coverage for speed
3. **For coverage reports**: Run tests sequentially or use the provided script

## Troubleshooting

If you see "Failed to generate report: No data to report":
1. Check that `.coveragerc` exists in the project root
2. Ensure `COVERAGE_CORE=sysmon` is set for Python 3.12+
3. Try running tests sequentially first to verify coverage works
4. Clean up old coverage files: `rm -f .coverage*`

## Known Limitations

- Some pytest plugins may not work correctly with xdist
- Coverage data from failed workers won't be collected
- Test discovery can be slower with many workers