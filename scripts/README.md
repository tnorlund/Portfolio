# CI/CD and Test Optimization Scripts

Essential scripts for managing GitHub Actions self-hosted runners, CI/CD workflows, and running tests with maximum performance.

## Quick Start

### Self-Hosted Runners

```bash
# Initial setup
./setup_python312.sh
./fix_runner_path.sh

# Start runners (saves $48/month)
./start_runners.sh
```

### Running Tests

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

Removed. Claude-based CI review tooling is no longer used.

## Configuration

- `test_matrix.json` - Optimal test groupings
- `requirements.txt` - Script dependencies

## Self-Hosted Runner Scripts

### start_runners.sh

Starts all configured GitHub Actions self-hosted runners.

- Automatically detects system resources
- Starts optimal number of runners (2-4 based on CPU/memory)
- Shows cost savings (~$48/month)

### setup_python312.sh

Configures Python 3.12 specifically for self-hosted runners.

- Adds Python 3.12 to runner PATH
- Creates python3 symlink pointing to python3.12
- Ensures CI uses Python 3.12 for all operations

### fix_runner_path.sh

Fixes PATH for self-hosted runners to include system utilities.

- Adds essential system paths (/usr/bin, /bin, etc.)
- Ensures tar, sh, git are available
- Required for GitHub Actions to function properly

### fix_runner_python.sh

Configures Python environment for self-hosted runners.

- Sets up local tool cache
- Configures environment variables
- Installs Python via Homebrew if needed

### quick_runner_setup.sh

Quick setup for multiple GitHub Actions runners.

- Downloads and configures runner software
- Sets up directories for multiple runners
- Provides registration instructions

## Cost Savings

Using self-hosted runners saves approximately:

- **$48/month** in GitHub Actions minutes
- **$576/year** in annual costs
