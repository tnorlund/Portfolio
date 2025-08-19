"""
Performance testing utilities for machine-agnostic tests.

This module provides utilities for creating performance tests that work
consistently across different environments (local machines, CI/CD, etc.).
"""

import statistics
import time
from typing import Callable, Dict, List, Optional, Tuple


class PerformanceBaseline:
    """Establishes baseline performance metrics for relative comparisons."""

    def __init__(
        self, warmup_iterations: int = 10, measurement_iterations: int = 100
    ):
        self.warmup_iterations = warmup_iterations
        self.measurement_iterations = measurement_iterations
        self._baseline_metrics: Optional[Dict[str, float]] = None

    def measure_baseline(
        self, operation: Callable[[], None]
    ) -> Dict[str, float]:
        """
        Measure baseline performance of a simple operation.

        Args:
            operation: A simple operation to establish baseline performance

        Returns:
            Dictionary with baseline metrics (mean, median, p95, stdev)
        """
        # Warmup phase
        for _ in range(self.warmup_iterations):
            operation()

        # Measurement phase
        measurements = []
        for _ in range(self.measurement_iterations):
            start = time.perf_counter()
            operation()
            measurements.append(time.perf_counter() - start)

        self._baseline_metrics = {
            "mean": statistics.mean(measurements),
            "median": statistics.median(measurements),
            "p95": statistics.quantiles(measurements, n=20)[18],
            "stdev": statistics.stdev(measurements),
            "min": min(measurements),
            "max": max(measurements),
        }

        return self._baseline_metrics

    def measure_relative_performance(
        self, operation: Callable[[], None], iterations: Optional[int] = None
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        Measure performance relative to baseline.

        Args:
            operation: The operation to measure
            iterations: Number of iterations (defaults to measurement_iterations)

        Returns:
            Tuple of (metrics, ratios) where ratios compare to baseline
        """
        if self._baseline_metrics is None:
            raise ValueError(
                "Must establish baseline first with measure_baseline()"
            )

        if iterations is None:
            iterations = self.measurement_iterations

        # Warmup
        for _ in range(min(self.warmup_iterations, iterations // 10)):
            operation()

        # Measure
        measurements = []
        for _ in range(iterations):
            start = time.perf_counter()
            operation()
            measurements.append(time.perf_counter() - start)

        metrics = {
            "mean": statistics.mean(measurements),
            "median": statistics.median(measurements),
            "p95": (
                statistics.quantiles(measurements, n=20)[18]
                if len(measurements) >= 20
                else max(measurements)
            ),
            "stdev": (
                statistics.stdev(measurements) if len(measurements) > 1 else 0
            ),
            "min": min(measurements),
            "max": max(measurements),
        }

        # Calculate ratios relative to baseline
        ratios = {
            "mean_ratio": metrics["mean"] / self._baseline_metrics["mean"],
            "median_ratio": metrics["median"]
            / self._baseline_metrics["median"],
            "p95_ratio": metrics["p95"] / self._baseline_metrics["p95"],
        }

        return metrics, ratios


class EnvironmentProfile:
    """Profile the current environment's performance characteristics."""

    @staticmethod
    def get_cpu_benchmark_score() -> float:
        """
        Get a simple CPU benchmark score for the current environment.
        Higher scores indicate faster CPUs.

        Returns:
            Benchmark score (operations per second)
        """
        start = time.perf_counter()

        # Simple CPU-bound operations
        result = 0
        for i in range(1_000_000):
            result += sum(range(10))
            result = hash(str(result))

        elapsed = time.perf_counter() - start
        return 1_000_000 / elapsed

    @staticmethod
    def get_memory_benchmark_score() -> float:
        """
        Get a simple memory allocation benchmark score.

        Returns:
            Benchmark score (allocations per second)
        """
        start = time.perf_counter()

        # Memory allocation operations
        data = []
        for i in range(10_000):
            data.append([0] * 1000)
            if i % 100 == 0:
                data.clear()

        elapsed = time.perf_counter() - start
        return 10_000 / elapsed

    @classmethod
    def get_performance_class(cls) -> str:
        """
        Classify the current environment's performance level.

        Returns:
            Performance class: 'high', 'medium', or 'low'
        """
        cpu_score = cls.get_cpu_benchmark_score()

        # These thresholds are calibrated based on typical environments
        # High: Modern development machines (M1/M2 Macs, recent Intel i7/i9)
        # Medium: Older machines, busy CI environments
        # Low: Constrained CI runners, Docker containers with limited resources

        if cpu_score > 2_000_000:
            return "high"
        elif cpu_score > 500_000:
            return "medium"
        else:
            return "low"

    @classmethod
    def get_scaling_factor(cls) -> float:
        """
        Get a scaling factor for performance expectations.

        Returns:
            Scaling factor (1.0 for medium performance, higher for better)
        """
        cpu_score = cls.get_cpu_benchmark_score()
        # Normalize to medium performance baseline
        return cpu_score / 1_000_000


class AdaptiveThresholds:
    """Provides environment-aware performance thresholds."""

    # Base thresholds calibrated for medium-performance environments
    BASE_THRESHOLDS = {
        "overhead_ratio": 2.0,  # Operation should be < 2x slower than baseline
        "latency_ms": 10.0,  # Base latency expectation
        "throughput_ops": 100,  # Base throughput expectation
        "memory_mb": 200,  # Base memory usage expectation (increased for realistic testing)
    }

    @classmethod
    def get_threshold(cls, metric: str, strict: bool = False) -> float:
        """
        Get an environment-adjusted threshold for a metric.

        Args:
            metric: The metric name (e.g., 'overhead_ratio', 'latency_ms')
            strict: If True, use stricter thresholds for high-perf environments

        Returns:
            Adjusted threshold value
        """
        base_value = cls.BASE_THRESHOLDS.get(metric, 1.0)
        perf_class = EnvironmentProfile.get_performance_class()
        scaling_factor = EnvironmentProfile.get_scaling_factor()

        if metric == "overhead_ratio":
            # Overhead ratios are more lenient in slower environments
            if perf_class == "low":
                return base_value * 2.0
            elif perf_class == "high" and strict:
                return base_value * 0.5
            return base_value

        elif metric == "latency_ms":
            # Latency expectations scale inversely with performance
            return base_value / scaling_factor

        elif metric == "throughput_ops":
            # Throughput scales directly with performance
            return base_value * scaling_factor

        elif metric == "memory_mb":
            # Memory thresholds are mostly environment-independent
            # but CI environments might have tighter constraints
            if perf_class == "low":
                return base_value * 0.8
            return base_value

        return base_value


def measure_operation_overhead(
    baseline_op: Callable[[], None],
    test_op: Callable[[], None],
    iterations: int = 1000) -> Dict[str, float]:
    """
    Measure the overhead of an operation compared to a baseline.

    Args:
        baseline_op: Simple baseline operation
        test_op: Operation to test
        iterations: Number of iterations

    Returns:
        Dictionary with overhead metrics
    """
    baseline = PerformanceBaseline(
        warmup_iterations=100, measurement_iterations=iterations
    )

    # Measure baseline
    baseline_metrics = baseline.measure_baseline(baseline_op)

    # Measure test operation
    test_metrics, ratios = baseline.measure_relative_performance(test_op)

    return {
        "baseline_mean_ms": baseline_metrics["mean"] * 1000,
        "test_mean_ms": test_metrics["mean"] * 1000,
        "overhead_ratio": ratios["mean_ratio"],
        "overhead_ms": (test_metrics["mean"] - baseline_metrics["mean"])
        * 1000,
        "relative_stdev": (
            test_metrics["stdev"] / test_metrics["mean"]
            if test_metrics["mean"] > 0
            else 0
        ),
    }


def assert_performance_within_bounds(
    metrics: Dict[str, float],
    max_overhead_ratio: Optional[float] = None,
    max_latency_ms: Optional[float] = None,
    min_throughput: Optional[float] = None,
    custom_message: str = "") -> None:
    """
    Assert that performance metrics are within acceptable bounds.

    Args:
        metrics: Performance metrics dictionary
        max_overhead_ratio: Maximum acceptable overhead ratio
        max_latency_ms: Maximum acceptable latency in milliseconds
        min_throughput: Minimum acceptable throughput
        custom_message: Additional context for assertion failures
    """
    thresholds = AdaptiveThresholds()
    perf_class = EnvironmentProfile.get_performance_class()

    failures = []

    if max_overhead_ratio is not None:
        actual_ratio = metrics.get("overhead_ratio", float("inf"))
        threshold = max_overhead_ratio or thresholds.get_threshold(
            "overhead_ratio"
        )
        if actual_ratio > threshold:
            failures.append(
                f"Overhead ratio {actual_ratio:.2f} exceeds threshold {threshold:.2f}"
            )

    if max_latency_ms is not None:
        actual_latency = metrics.get("test_mean_ms", float("inf"))
        threshold = max_latency_ms or thresholds.get_threshold("latency_ms")
        if actual_latency > threshold:
            failures.append(
                f"Latency {actual_latency:.2f}ms exceeds threshold {threshold:.2f}ms"
            )

    if min_throughput is not None:
        actual_throughput = metrics.get("throughput", 0)
        threshold = min_throughput or thresholds.get_threshold(
            "throughput_ops"
        )
        if actual_throughput < threshold:
            failures.append(
                f"Throughput {actual_throughput:.0f} ops/s below threshold {threshold:.0f} ops/s"
            )

    if failures:
        message = f"Performance assertion failed (environment: {perf_class})"
        if custom_message:
            message += f" - {custom_message}"
        message += "\n" + "\n".join(failures)
        message += f"\nFull metrics: {metrics}"
        raise AssertionError(message)
