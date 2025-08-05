# MCP Development Server

This repository includes a simple Model Context Protocol server for running
common development tasks.

Start the server from the repository root:

```bash
python mcp_server.py
```

The server exposes nine tools:

### Development Tools

- `run_tests` &ndash; run pytest for a specific package (`receipt_label`,
  `receipt_dynamo`, etc.) using all available CPU cores.
- `run_lint` &ndash; format code with `isort` and `black` and then run `pylint`
  and `mypy` for the package.

### Pulumi Infrastructure Tools

- `pulumi_preview` &ndash; run `pulumi preview` in the infra directory to see
  planned infrastructure changes. Optionally specify a stack and additional args.
- `pulumi_up` &ndash; run `pulumi up` in the infra directory to apply
  infrastructure changes. Optionally specify a stack and additional args.
- `pulumi_stack_list` &ndash; list all available Pulumi stacks.
- `pulumi_stack_output` &ndash; get outputs from a Pulumi stack. Optionally
  specify the stack name and a specific output name.
- `pulumi_refresh` &ndash; refresh Pulumi state to match actual infrastructure.
  Optionally specify a stack and additional args.
- `pulumi_logs` &ndash; get logs from the last Pulumi operation. Optionally
  specify a stack and additional args.
- `pulumi_config_get` &ndash; get a configuration value from Pulumi. Requires
  a key parameter, optionally specify a stack.

The test and lint tools execute commands inside the package directory so
configuration files are discovered correctly. All Pulumi tools execute in the
infra directory.

## Worktree Virtual Environments

When working on multiple branches using Git worktrees, create a separate
virtual environment in each worktree directory. This keeps package
installations isolated and prevents interference between branches.

Example:

```bash
git worktree add ../feature-branch feature-branch
cd ../feature-branch
python -m venv .venv
source .venv/bin/activate
```

Activate the worktree's environment before running `python mcp_server.py` or
other development commands.
# Test Optimization and Execution

## Advanced Test Tools

This repository includes comprehensive test optimization tools for maximum performance:

### Quick Local Testing
```bash
# Recommended: Use the optimized test runner
./scripts/test_runner.sh receipt_dynamo
./scripts/test_runner.sh -t integration -c receipt_dynamo

# Advanced: Use the intelligent test runner
python scripts/run_tests_optimized.py receipt_dynamo tests/unit --test-type unit
```

### Test Analysis and Optimization
```bash
# Analyze test structure and generate optimal parallel groups
python scripts/analyze_tests.py

# Generate dynamic GitHub Actions matrix
python scripts/generate_test_matrix.py

# Identify performance bottlenecks
python scripts/optimize_slow_tests.py
```

### Performance Impact
- **Integration tests**: 39 files, 1,579 tests split into 4 optimal parallel groups
- **Speedup**: 62.8min → 15.8min execution time (4x faster)
- **Load balancing**: Each group has ~395 tests, ~16min execution time

See `scripts/README.md` for detailed documentation of all test optimization tools.

# End-to-End Tests

**IMPORTANT**: The `receipt_dynamo/tests/end_to_end` directory contains end-to-end tests that connect to REAL AWS services.

These tests:
- Require AWS credentials and internet connectivity
- Connect to actual DynamoDB tables and S3 buckets
- May incur AWS costs
- Should NOT be run automatically during code review or CI

To run end-to-end tests:
1. Set up AWS credentials (`aws configure` or environment variables)
2. Use `pulumi stack select tnorlund/portfolio/dev` to access configuration
3. Run with `pytest -m end_to_end`

Always skip these tests during normal development: `pytest -m "not end_to_end"`

See `receipt_dynamo/tests/end_to_end/README.md` for detailed setup instructions.

# Lambda Architecture Configuration

**IMPORTANT**: All Lambda functions in this project must use ARM64 architecture to match the Lambda layers.

The Lambda layers are built using ARM64 architecture (see `infra/fast_lambda_layer.py`). When creating or updating Lambda functions, always include:

```python
architectures=["arm64"],
```

This prevents architecture mismatch errors like "No module named 'pydantic_core._pydantic_core'" which occur when x86_64 Lambdas try to load ARM64-compiled C extensions.

Example:
```python
lambda_function = Function(
    "my-function",
    runtime="python3.12",
    architectures=["arm64"],  # Required for compatibility with layers
    handler="handler.main",
    layers=[dynamo_layer.arn, label_layer.arn],
    # ... other configuration
)
```

# CI/CD Workflow Guidelines

## Understanding PR Status Checks

**IMPORTANT**: The "PR Status" check in our CI/CD pipeline is designed to fail when code requires auto-formatting, even if the code ends up properly formatted. This is intentional behavior to encourage developers to format code locally before pushing.

### How It Works

1. **Format Check**: Runs `black` and `isort` to check if code needs formatting
2. **Auto-Format**: If formatting is needed, the workflow automatically formats and commits the changes
3. **PR Status**: FAILS if auto-formatting was needed (even though code is now correct)

### Why This Matters

This design prevents "formatting thrash" where developers push unformatted code and rely on CI to fix it. The failing PR Status serves as a reminder to run formatters locally.

### Best Practices to Avoid CI Distractions

1. **Always format locally before pushing**:
   ```bash
   make format  # or
   black . && isort .
   ```

2. **When you see formatting failures**:
   - Check WHICH files need formatting (look at the specific error message)
   - Only fix files that are part of your PR
   - Ignore formatting issues in unrelated files

3. **Maintain PR scope discipline**:
   - Don't fix issues outside your PR's scope
   - If you see unrelated formatting issues, create a separate PR
   - Focus only on your intended changes

4. **Understanding "failed" PR Status**:
   - A failed PR Status due to auto-formatting is not a blocker for merging
   - It's a gentle reminder for next time
   - Feature branches can be merged despite this "failure"

### Common Pitfall: The Formatting Rabbit Hole

**Scenario**: Your PR fails due to formatting, you start fixing it, then find more files need formatting, and suddenly you're reformatting half the codebase.

**Solution**:
- STOP and check which files are actually part of your PR
- Only format files you've modified
- Let other files remain as they are

### Example Workflow

```bash
# Before pushing your changes
git status  # Check which files you've modified
make format  # Format everything
git add -p  # Selectively add only YOUR changes
git commit -m "Add new feature"
git push

# If CI still complains about formatting in OTHER files:
# IGNORE IT - those aren't your responsibility
```

# Package Architecture and Boundaries

## CRITICAL: Maintain Strict Package Separation

**IMPORTANT**: This project follows a strict separation of concerns between packages. Each package has a specific responsibility and MUST NOT contain logic that belongs in another package.

### Package Responsibilities

1. **receipt_dynamo** - DynamoDB Data Layer
   - ALL DynamoDB-specific logic (queries, writes, batch operations)
   - ALL resilience patterns for DynamoDB (circuit breakers, retries, batching)
   - Entity definitions and DynamoDB item conversions
   - DynamoDB client implementations
   - DO NOT: Import from receipt_label or implement business logic

2. **receipt_label** - Business Logic Layer
   - Receipt labeling and analysis logic
   - AI service integrations (OpenAI, Anthropic) for labeling
   - Places API integration for location data
   - Uses receipt_dynamo as a data layer through high-level interfaces
   - DO NOT: Implement DynamoDB operations directly

3. **receipt_ocr** - OCR Processing Layer
   - OCR text extraction logic
   - Image processing
   - Text detection algorithms
   - DO NOT: Implement database operations or labeling logic

### Examples of Violations to Avoid

❌ **WRONG**: Implementing DynamoDB retry logic in receipt_label
```python
# receipt_label/utils/some_tracker.py
def put_with_retry(self, item):
    for attempt in range(3):
        try:
            self.dynamo_client.put_item(...)  # DynamoDB logic in wrong package!
        except:
            time.sleep(2 ** attempt)
```

✅ **CORRECT**: Use high-level interfaces from receipt_dynamo
```python
# receipt_label/utils/some_tracker.py
def store(self, item):
    self.dynamo_client.put_ai_usage_metric(item)  # Delegates to receipt_dynamo
```

❌ **WRONG**: Creating resilient DynamoDB patterns in receipt_label
```python
# receipt_label/utils/resilient_tracker.py
class ResilientTracker:
    def __init__(self):
        self.circuit_breaker = CircuitBreaker()  # Should be in receipt_dynamo!
```

✅ **CORRECT**: Import resilient implementations from receipt_dynamo
```python
# receipt_label/utils/resilient_tracker.py
from receipt_dynamo import ResilientDynamoClient

class ResilientTracker:
    def __init__(self):
        self.dynamo_client = ResilientDynamoClient()  # Use receipt_dynamo's implementation
```

### Checking for Violations

Before implementing any feature:
1. Ask: "Which package owns this responsibility?"
2. If it's database/DynamoDB related → receipt_dynamo
3. If it's labeling/analysis related → receipt_label
4. If it's OCR/image processing → receipt_ocr
5. Never mix responsibilities between packages

### Code Review Checklist

When reviewing code, check for:
- [ ] No direct DynamoDB operations in receipt_label
- [ ] No business logic in receipt_dynamo
- [ ] All resilience patterns for DynamoDB are in receipt_dynamo
- [ ] receipt_label only uses high-level interfaces from receipt_dynamo
- [ ] No circular dependencies between packages

# Boto3 Type Safety

**IMPORTANT**: This project uses boto3 type stubs for improved developer experience without runtime overhead.

## Implementation

We use the TYPE_CHECKING pattern to import boto3 type stubs only during development:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client
```

## Key Points

1. **Dev Dependencies**: `boto3-stubs[dynamodb,s3]` is in `[dev]` extras, not required at runtime
2. **Zero Runtime Impact**: TYPE_CHECKING imports are skipped during execution
3. **Full IDE Support**: Developers get autocomplete, parameter hints, and type checking
4. **CI Compatibility**: Tests run without dev dependencies installed

## Adding New AWS Services

When adding boto3 clients for new AWS services:
1. Add the service to boto3-stubs extras: `boto3-stubs[dynamodb,s3,new-service]`
2. Use TYPE_CHECKING pattern for imports
3. Add type annotations to client variables: `client: ServiceClient = boto3.client("service")`

This approach provides type safety during development while maintaining fast runtime performance.

# GitHub Actions Cost Optimization Strategy

## Overview

This document outlines strategies to reduce GitHub Actions spending from ~$48/month to under $5/month through self-hosted runners and local testing optimization.

## Current Spending Analysis

**Before Optimization (June 2024):**
- Total spend: $24.16 additional (on top of 3,000 included minutes)
- Estimated usage: ~6,000 minutes/month
- Cost per month: ~$48 (including overage)
- Primary cost drivers: Python test matrix (6 parallel jobs × 15-20 mins each)

## Cost Reduction Strategies

### 1. Self-Hosted Runner (Apple Silicon Mac)

**Setup Complete:**
- ✅ ARM64 runner configured with labels: `[self-hosted, python-tests]`
- ✅ Located at: `/Users/tnorlund/GitHub/actions-runner/`
- ✅ Hybrid approach: Python tests on self-hosted, security tasks on GitHub-hosted

**Workflows Updated:**
- ✅ `main.yml`: Python test matrix runs on self-hosted runner
- ✅ `pr-checks.yml`: Quick Python tests on self-hosted, TypeScript on GitHub-hosted
- ✅ Security-sensitive tasks (deploy, AWS operations) remain on GitHub-hosted

**Expected Savings:**
- Python tests: ~5,000 minutes/month → $0 (self-hosted)
- Remaining GitHub usage: ~1,000 minutes/month → ~$8/month
- **Total savings: ~$40/month (~$480/year)**

### 2. Local Testing Optimization

**Philosophy:** Test locally first, push only when confident

#### A. Local Test Scripts

**Quick Development Loop:**
```bash
# Format code locally (prevent CI formatting failures)
make format                    # or: black . && isort .

# Run specific package tests locally
cd receipt_dynamo && pytest tests/unit -v           # Unit tests only
cd receipt_label && pytest tests -m "unit" -v       # Marker-based tests

# Run integration tests for specific changes
pytest tests/integration/test__specific_module.py
```

**Pre-Push Validation:**
```bash
# Mirror CI quick-tests locally
./scripts/local_ci_check.sh receipt_dynamo          # Mirrors PR checks
./scripts/local_ci_check.sh receipt_label           # Before pushing

# Run comprehensive tests (mirrors main.yml)
./scripts/local_full_test.sh receipt_dynamo unit    # Full unit tests
./scripts/local_full_test.sh receipt_dynamo integration group-1  # Specific group

# Check formatting without fixing
black --check . && isort --check-only .
```

**Interactive Development Workflow:**
```bash
# Comprehensive guided workflow with cost estimates
./scripts/dev_workflow.sh                           # Interactive menu

# Quick formatting
./scripts/format_code.sh                           # Equivalent to make format
```

#### B. Development Workflow Best Practices

**1. Draft PRs for Development:**
```bash
# Create draft PR to prevent automatic CI triggers
gh pr create --draft --title "WIP: feature development"

# Push incremental changes without triggering expensive CI
git push  # Only quick format checks run for drafts
```

**2. Bundle Commits:**
```bash
# Instead of: 5 commits = 5 CI runs = 5 × 120 minutes = 600 minutes
# Do: Bundle into 1 commit = 1 CI run = 120 minutes

git commit --amend    # Add to previous commit
git rebase -i HEAD~3  # Squash multiple commits
```

**3. Skip CI for Documentation:**
```bash
# Skip CI entirely for doc-only changes
git commit -m "docs: update README [skip ci]"
```

**4. Target Specific Tests:**
```bash
# Instead of running full test suite, target specific changes
pytest tests/integration/test__specific_feature.py -v

# Use markers to run subset
pytest -m "not slow"          # Skip expensive tests locally
pytest -m "integration"       # Only integration tests
```

#### C. Local Environment Optimization

**Fast Local Testing Setup:**
```bash
# Create dedicated testing virtual environment
python -m venv .venv-testing
source .venv-testing/bin/activate

# Install minimal test dependencies for speed
pip install pytest pytest-xdist pytest-cov

# Install packages in development mode
pip install -e receipt_dynamo
pip install -e receipt_label
```

**Parallel Local Testing:**
```bash
# Use all CPU cores for faster local tests
pytest -n auto                          # Auto-detect cores
pytest -n 8                            # Explicit core count

# Distribute tests by file for better balance
pytest --dist worksteal tests/
```

#### D. IDE Integration

**VSCode pytest integration:**
```json
// .vscode/settings.json
{
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": [
    "tests",
    "-v",
    "--tb=short"
  ],
  "python.testing.autoTestDiscoverOnSaveEnabled": false
}
```

### 3. Selective CI Triggering

**When to Use Each Approach:**

| Change Type | Local Testing | Draft PR | Full CI | Cost Impact |
|-------------|---------------|----------|---------|-------------|
| Bug fixes | ✅ Required | Optional | When confident | Low |
| New features | ✅ Required | ✅ Recommended | Final validation | Medium |
| Refactoring | ✅ Required | ✅ Recommended | Before merge | Medium |
| Documentation | ✅ Light | ❌ Skip CI | ❌ Skip | None |
| Dependencies | ✅ Required | ❌ Direct PR | ✅ Required | High |

**Smart Commit Messages:**
```bash
# Skip CI for safe changes
git commit -m "docs: update API documentation [skip ci]"
git commit -m "style: fix code formatting [skip ci]"

# Trigger minimal CI
git commit -m "fix: minor bug in validation logic"  # Only quick tests

# Full CI for risky changes
git commit -m "feat: new authentication system"     # All tests needed
```

## 4. Monitoring and Alerts

**Monthly Usage Tracking:**
- Monitor GitHub billing page: https://github.com/settings/billing
- Set spending alerts at $10, $20, $30 thresholds
- Review Actions usage patterns monthly

**High Usage Patterns to Watch:**
- Multiple rapid commits to the same PR
- Large test matrix failures requiring reruns
- Dependency update PRs that trigger full test suites
- Concurrent work on multiple feature branches

## 5. Expected Total Savings

**Optimized Workflow:**
- Self-hosted Python tests: $0/month (was ~$40)
- Reduced GitHub-hosted usage: ~$5/month (was ~$8)
- Local testing prevents failed CI runs: Additional ~$2-3/month savings

**Total monthly cost:** ~$2-5/month (down from $48)
**Annual savings:** ~$500-550/year

## Implementation Checklist

- [x] Set up Apple Silicon self-hosted runner
- [x] Update workflows for hybrid approach
- [x] Document local testing strategies
- [x] Create local testing scripts
  - [x] `./scripts/local_ci_check.sh` - Mirror PR quick-tests
  - [x] `./scripts/local_full_test.sh` - Mirror main.yml test matrix
  - [x] `./scripts/format_code.sh` - Code formatting
  - [x] `./scripts/dev_workflow.sh` - Interactive guided workflow
- [ ] Test hybrid setup with sample PR
- [ ] Monitor first month of usage
- [ ] Fine-tune based on actual patterns

## Quick Start Guide

**For daily development:**
```bash
# Interactive workflow with cost estimates
./scripts/dev_workflow.sh

# Quick validation before push
./scripts/local_ci_check.sh receipt_dynamo
./scripts/format_code.sh && git add -A && git commit
```

**Start self-hosted runner:**
```bash
cd /Users/tnorlund/GitHub/actions-runner
./run.sh  # Or use option 15 in dev_workflow.sh
```

# DynamoDB GSI Optimization Strategy

## Current GSI Infrastructure Analysis

The receipt processing system uses a single DynamoDB table with 4 Global Secondary Indexes:

| GSI | Current Usage | Efficiency | Purpose |
|-----|---------------|------------|---------|
| **GSI1** | ✅ Active | High | Service/date queries (`AI_USAGE#{service}` / `DATE#{date}`) |
| **GSI2** | ✅ Active | High | Cost aggregation (`AI_USAGE_COST` / `COST#{date}#{service}`) |
| **GSI3** | ✅ Active | Medium | Job/batch queries (`JOB#{job_id}` / `AI_USAGE#{timestamp}`) |
| **GSITYPE** | ❌ Underutilized | Low | Only contains TYPE field discriminator |

## GSI Composite Key Strategy (Revised)

**Problem**: Cost monitoring system uses expensive scan operations for user and environment scope queries.

**Solution**: Enhance existing GSI3 with composite keys to support all scope types without new infrastructure.

### Enhanced GSI3 Design

```
GSI3 Index (Enhanced):
- PK: "JOB#{job_id}" | "USER#{user_id}" | "BATCH#{batch_id}" | "ENV#{environment}"
- SK: "AI_USAGE#{timestamp}"
```

### Query Patterns Enabled

```python
# User scope queries - eliminate scans using GSI3
GSI3PK = "USER#john_doe"
GSI3SK = "AI_USAGE#2024-01-01T00:00:00Z to AI_USAGE#2024-01-31T23:59:59Z"

# Environment scope queries - eliminate scans using GSI3
GSI3PK = "ENV#production"
GSI3SK = "AI_USAGE#2024-01-01T00:00:00Z to AI_USAGE#2024-01-31T23:59:59Z"

# Job scope queries - existing functionality preserved
GSI3PK = "JOB#batch-123"
GSI3SK = "AI_USAGE#2024-01-01T00:00:00Z to AI_USAGE#2024-01-31T23:59:59Z"
```

### Benefits

- **Eliminate scan operations**: User/environment queries become O(log n) instead of O(n)
- **Zero additional GSI costs**: Uses existing GSI3 infrastructure with enhanced composite keys
- **Preserve TYPE GSI**: Maintains efficient entity-wide queries using GSITYPE as intended
- **Priority hierarchy**: Smart scope assignment ensures optimal query performance
- **Backward compatible**: Existing job/batch queries continue to work unchanged

### Implementation Status

**Phase 1 Completed**:
- ✅ Optimized job queries to use GSI3 instead of scans
- ✅ Added fallback scan operations for reliability

**Phase 2 Planned**:
- 🔄 Enhance GSI3 composite keys for user/environment scopes
- 🔄 Migrate CostMonitor to use enhanced GSI3 queries
- 🔄 Backfill historical records with enhanced GSI3 keys

**Performance Impact**:
- Job queries: Scan → GSI3 query (60-80% faster) ✅ **Completed**
- User/environment queries: Scan → Enhanced GSI3 query (planned 60-80% faster)
- Cost monitoring scalability: Linear → Logarithmic performance

### Reference Documentation

See `GSITYPE_OPTIMIZATION_STRATEGY.md` for detailed implementation guide, code examples, and migration strategy.

## Key Design Principles

1. **Maximize existing infrastructure** - Use provisioned GSIs efficiently before adding new ones
2. **Maintain backward compatibility** - Existing query patterns continue to work
3. **Graceful degradation** - Scan fallbacks ensure reliability during migration
4. **Cost-effective optimization** - Significant performance gains with minimal additional AWS costs

# Performance Testing Guidelines

## CRITICAL: Environment-Dependent Performance Tests

**IMPORTANT**: Performance tests with hardcoded thresholds WILL fail in different environments. CI environments (GitHub Actions) are significantly less performant than local development machines.

### The Problem

Performance tests that pass locally often fail in CI because:
- CI runners have limited CPU and memory resources
- Network latency varies between environments
- Shared infrastructure causes unpredictable performance
- Background processes affect timing measurements

### Example Issue

```python
# ❌ WRONG: Hardcoded performance threshold
expected_throughput_ratio = 0.10  # Expects 10% throughput
assert late_throughput > early_throughput * expected_throughput_ratio
```

This test might pass locally (powerful development machine) but fail in CI (constrained GitHub Actions runner).

### Solution: Environment-Aware Thresholds

```python
# ✅ CORRECT: Environment-aware thresholds
# IMPORTANT: These thresholds are environment-dependent
# CI environments are less performant than local development machines
# Values tuned for GitHub Actions CI environment performance
expected_throughput_ratio = (
    0.03 if config.use_resilient_tracker else 0.01  # CI-tuned values
)
```

### Best Practices

1. **Always document performance thresholds**:
   - Explain why specific values were chosen
   - Note which environment they were tuned for
   - Add comments about environment dependencies

2. **Tune thresholds for CI environment**:
   - Use the lowest-performance environment as baseline
   - Test thoroughly in CI before merging
   - Consider using environment detection for different thresholds

3. **Avoid absolute performance measurements**:
   - Use relative improvements instead of absolute values
   - Focus on "better than baseline" rather than specific numbers
   - Test resilience patterns, not raw performance

4. **Alternative approaches**:
   - Mock time-dependent operations
   - Use deterministic test scenarios
   - Focus on functional correctness over performance

### Code Review Checklist

When reviewing performance tests:
- [ ] Are performance thresholds documented and justified?
- [ ] Have thresholds been tested in CI environment?
- [ ] Are tests measuring relative improvement, not absolute performance?
- [ ] Do tests focus on resilience patterns rather than raw speed?
- [ ] Are environment dependencies clearly documented?

### Historical Context

This documentation was added after issue #130 where performance tests expected 10% throughput under stress but CI achieved only ~3%. The tests were updated to use CI-tuned thresholds (3%) to prevent spurious failures while maintaining the functional verification of resilience patterns.

## Performance Testing Strategy (Updated 2025-07-01)

### Short-term Solution: Skip Performance Tests in CI

Due to continued flakiness of time-based performance tests in CI environments, we have implemented a strategy to skip performance tests in CI while preserving the ability to run them locally.

**Implementation:**
1. CI workflows set `SKIP_PERFORMANCE_TESTS=true` environment variable
2. `conftest.py` automatically skips tests marked with `@pytest.mark.performance` when this variable is set
3. Developers can still run performance tests locally for validation

**Running Performance Tests:**
```bash
# Run all tests including performance (default local behavior)
pytest

# Run only performance tests
pytest -m performance

# Skip performance tests locally (mimics CI behavior)
SKIP_PERFORMANCE_TESTS=true pytest
```

**Rationale:**
- Performance tests against mocked services don't measure real performance
- Time-based assertions are inherently brittle in shared CI environments
- The maintenance cost of flaky tests outweighs their value in CI

**Future Work:**
- Implement production monitoring with APM tools for real performance insights
- Set up dedicated performance testing pipeline with consistent resources
- Convert time-based tests to algorithmic complexity validation

See [Design Decision: Performance Testing Strategy](docs/design-decisions/performance-testing-strategy.md) for detailed rationale and migration plan.
