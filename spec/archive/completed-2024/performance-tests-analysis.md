# Performance Tests Analysis: receipt_label Package

## Issue Summary
Performance tests for `receipt_label` rely on time-based assertions that can be brittle in CI. For example, the cost calculator's tests expect 1000 calculations to finish in under one second:

```python
567      @pytest.mark.performance
568      def test_bulk_openai_calculations_performance(self):
...
585          # Should complete within 1 second
586          assert duration < 1.0
```

The integration suite sets strict throughput/latency numbers, tuned specifically for CI, such as:

```python
292                  # Note: CI environments are less performant than local development
293                  assert (
294                      overall_throughput > 100  # CI-tuned: was 200
295                  )
296                  assert avg_worker_latency < 50  # CI-tuned: was 20
297                  assert max_worker_latency < 200  # CI-tuned: was 100
```

The README emphasizes high throughput and tight latency goals for these performance tests:

```yaml
59  - **High throughput** tracking (1000+ requests/second)
66  **Performance Metrics:**
67  - Throughput: >100 req/s under normal load
68  - Latency: <10ms average DynamoDB write latency
71  - Query performance: <500ms for month-long queries
```

Because these tests measure actual elapsed time, they can easily fail when CI machines are slower or more loaded than local environments.

## Are These Tests Necessary?

The logic they exercise (cost calculations, usage tracking) is already covered by functional tests without strict timing. The performance checks mainly ensure that large loops or concurrent calls don't degrade unreasonably—but the thresholds themselves aren't tied to correctness. Consequently they add flakiness without protecting against regressions that typical unit or integration tests would miss.

## Better Approaches

1. **Use dedicated benchmarking tools** – frameworks like `pytest-benchmark` can record baseline timings and detect regressions relative to historical results rather than fixed thresholds.

2. **Separate benchmarks from CI pass/fail** – treat performance checks as optional benchmarks run in a controlled environment or on demand, instead of gating regular CI.

3. **Profile real workloads** – capture performance metrics in production or staging with monitoring (e.g., logging throughput, latency) rather than synthetic loops.

4. **Focus on algorithmic complexity** – ensure functions have appropriate big-O behavior or operate within expected call counts, which is deterministic in unit tests.

## Recommendation

Keeping these time-sensitive tests in the main suite may not be worthwhile. Converting them to optional benchmarks or monitoring performance in production would provide more consistent insight without the flaky failures.

## Implementation Options

### Option 1: Mark Performance Tests as Optional
```python
@pytest.mark.performance
@pytest.mark.skipif(
    os.environ.get("SKIP_PERFORMANCE_TESTS", "true").lower() == "true",
    reason="Performance tests are skipped by default"
)
def test_bulk_calculations_performance(self):
    ...
```

### Option 2: Move to Separate Benchmark Suite
Create `benchmarks/` directory with dedicated performance tests using `pytest-benchmark`:

```python
def test_cost_calculator_throughput(benchmark):
    calculator = AICostCalculator()
    result = benchmark(calculator.calculate_openai_cost, ...)
    # No assertions on timing - let pytest-benchmark handle tracking
```

### Option 3: Convert to Monitoring Metrics
Replace tests with runtime metrics collection:

```python
# In production code
import time
from prometheus_client import Histogram

calculation_duration = Histogram(
    'ai_cost_calculation_duration_seconds',
    'Time spent calculating AI costs'
)

@calculation_duration.time()
def calculate_openai_cost(self, ...):
    # Actual calculation logic
```

## Affected Files
- `receipt_label/tests/unit/test_cost_calculator.py` - Performance tests for cost calculations
- `receipt_label/tests/integration/test_ai_usage_performance_integration.py` - Throughput and latency tests
- `receipt_label/README.md` - Performance metrics documentation

## Next Steps
1. Decide on approach (optional tests, benchmarks, or monitoring)
2. Update affected test files
3. Update CI configuration to handle performance tests appropriately
4. Document the new approach in README
