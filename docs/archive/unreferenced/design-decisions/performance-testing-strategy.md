# Design Decision: Performance Testing Strategy

**Date**: 2025-07-01
**Status**: Approved
**Author**: Team

## Context

Our CI/CD pipeline has been experiencing intermittent failures due to performance tests that rely on time-based assertions. These tests fail unpredictably because:

1. **Environment Variability**: CI runners (GitHub Actions) have significantly different performance characteristics than local development machines
2. **Shared Infrastructure**: CI environments run on shared infrastructure with unpredictable resource availability
3. **Hardcoded Thresholds**: Tests use fixed timing expectations (e.g., "must complete in < 1 second") that don't account for environment differences

Example failure from CI run #16003254125:
```
AssertionError: Peak latency 82.4ms exceeds threshold 72.7ms
```

## Decision

We will implement a **short-term mitigation strategy** while planning for long-term improvements:

### Short-term (Implemented)
1. Add `SKIP_PERFORMANCE_TESTS=true` environment variable to all CI workflows
2. Configure pytest to automatically skip tests marked with `@pytest.mark.performance` when this variable is set
3. Performance tests can still be run locally by developers and in dedicated performance testing environments

### Medium-term (Planned)
1. Convert time-based performance tests to use mocked services with deterministic behavior
2. Focus on testing algorithmic complexity and resilience patterns rather than absolute speed
3. Implement relative performance benchmarking (compare against baseline, not absolute values)

### Long-term (Future)
1. Set up dedicated performance testing pipeline that runs periodically (weekly/monthly)
2. Implement production monitoring with APM tools (DataDog, New Relic, AWS X-Ray)
3. Use load testing tools (Locust, K6) against staging environments

## Implementation Details

### 1. CI Configuration
Added to `.github/workflows/main.yml` and `.github/workflows/pr-checks.yml`:
```yaml
env:
  SKIP_PERFORMANCE_TESTS: "true"
```

### 2. Pytest Configuration
Updated `conftest.py` to skip performance tests when environment variable is set:
```python
def pytest_runtest_setup(item: Item) -> None:
    if item.get_closest_marker("performance"):
        if os.environ.get("SKIP_PERFORMANCE_TESTS", "").lower() == "true":
            pytest.skip("Skipping performance tests (SKIP_PERFORMANCE_TESTS=true)")
```

### 3. Running Performance Tests Locally
Developers can still run performance tests locally:
```bash
# Run all tests including performance
pytest

# Run only performance tests
pytest -m performance

# Skip performance tests locally
SKIP_PERFORMANCE_TESTS=true pytest
```

## Rationale

### Why Not Fix the Tests?
While we have utilities like `performance_utils.py` that attempt to make tests environment-aware, the fundamental issue remains:
- Performance tests against mocked services don't test real performance
- Time-based assertions are inherently brittle in shared environments
- The value provided by these tests doesn't justify the CI instability they cause

### Why This Approach?
1. **Immediate Relief**: Stops spurious CI failures immediately
2. **No Loss of Functionality**: Tests can still be run when needed
3. **Clear Migration Path**: Provides time to implement better testing strategies
4. **Cost Effective**: Reduces wasted CI minutes on flaky tests

## Consequences

### Positive
- CI pipeline becomes more stable and predictable
- Developers aren't distracted by false-positive failures
- Reduces CI costs by avoiding re-runs of failed builds
- Clear separation between functional and performance testing

### Negative
- Performance regressions might not be caught automatically in CI
- Requires discipline to run performance tests locally when making performance-sensitive changes
- Need to implement alternative performance monitoring strategies

## Alternatives Considered

1. **Increase timing thresholds**: Would reduce failures but not eliminate them, and would make tests less useful
2. **Use adaptive thresholds**: Already attempted with `performance_utils.py`, but still brittle
3. **Mock time in tests**: Would test code paths but not actual performance
4. **Remove performance tests entirely**: Would lose the ability to catch regressions during development

## References

- [Performance Tests Analysis](../spec/performance-tests-analysis.md)
- [CI/CD Workflow Guidelines](../../.github/CLAUDE.md)
- [Performance Testing Guidelines](../../CLAUDE.md#performance-testing-guidelines)

## Future Work

1. Create dedicated performance testing environment with consistent resources
2. Implement performance regression detection using statistical analysis
3. Set up production performance monitoring dashboards
4. Document performance testing best practices for the team
