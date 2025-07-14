"""Performance tests for pattern detection to verify <100ms target."""

import asyncio
import statistics
import time

import pytest
from receipt_label.pattern_detection import ParallelPatternOrchestrator

from receipt_dynamo.entities import ReceiptWord


@pytest.mark.performance
class TestPerformanceTarget:
    """Test that pattern detection meets performance targets."""

    def create_receipt_words(self, count: int) -> list:
        """Create sample receipt words for performance testing."""
        words = []

        # Add realistic patterns throughout
        patterns = [
            "$19.99",
            "€10.50",
            "TAX $1.50",
            "TOTAL $25.00",  # Currency
            "01/15/2024",
            "2024-01-15",
            "Jan 15, 2024",
            "14:30:00",  # DateTime
            "(555) 123-4567",
            "email@example.com",
            "www.store.com",  # Contact
            "2 @ $5.99",
            "3 x $4.50",
            "Qty: 5",
            "2 items",  # Quantity
            "PRODUCT",
            "DESCRIPTION",
            "ITEM",
            "SKU12345",  # Regular words
        ]

        for i in range(count):
            # Cycle through patterns
            text = patterns[i % len(patterns)]

            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=i // 5,  # 5 words per line
                word_id=i,
                text=text,
                bounding_box={
                    "x": (i % 5) * 60,
                    "y": (i // 5) * 20,
                    "width": 50,
                    "height": 20,
                },
                top_left={"x": (i % 5) * 60, "y": (i // 5) * 20},
                top_right={"x": (i % 5) * 60 + 50, "y": (i // 5) * 20},
                bottom_left={"x": (i % 5) * 60, "y": (i // 5) * 20 + 20},
                bottom_right={"x": (i % 5) * 60 + 50, "y": (i // 5) * 20 + 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
            words.append(word)

        return words

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_small_receipt_performance(self):
        """Test performance with small receipt (20 words)."""
        orchestrator = ParallelPatternOrchestrator(timeout=0.1)
        words = self.create_receipt_words(20)

        # Warm up
        await orchestrator.detect_all_patterns(words)

        # Measure multiple runs
        times = []
        for _ in range(10):
            start = time.time()
            results = await orchestrator.detect_all_patterns(words)
            elapsed = (time.time() - start) * 1000  # ms
            times.append(elapsed)

            # Verify results
            assert not results["_metadata"]["timeout_occurred"]

        # Calculate statistics
        avg_time = statistics.mean(times)
        p95_time = statistics.quantiles(times, n=20)[18]  # 95th percentile

        print(f"\nSmall receipt (20 words):")
        print(f"  Average: {avg_time:.2f}ms")
        print(f"  95th percentile: {p95_time:.2f}ms")
        print(f"  Min: {min(times):.2f}ms")
        print(f"  Max: {max(times):.2f}ms")

        # Must meet performance target
        assert avg_time < 100  # Average under 100ms
        assert p95_time < 100  # 95th percentile under 100ms

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_medium_receipt_performance(self):
        """Test performance with medium receipt (50 words)."""
        orchestrator = ParallelPatternOrchestrator(timeout=0.1)
        words = self.create_receipt_words(50)

        # Warm up
        await orchestrator.detect_all_patterns(words)

        # Measure multiple runs
        times = []
        for _ in range(10):
            start = time.time()
            results = await orchestrator.detect_all_patterns(words)
            elapsed = (time.time() - start) * 1000  # ms
            times.append(elapsed)

            # Verify results
            assert not results["_metadata"]["timeout_occurred"]

        # Calculate statistics
        avg_time = statistics.mean(times)
        p95_time = statistics.quantiles(times, n=20)[18]

        print(f"\nMedium receipt (50 words):")
        print(f"  Average: {avg_time:.2f}ms")
        print(f"  95th percentile: {p95_time:.2f}ms")
        print(f"  Min: {min(times):.2f}ms")
        print(f"  Max: {max(times):.2f}ms")

        # Must meet performance target
        assert avg_time < 100
        assert p95_time < 100

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_large_receipt_performance(self):
        """Test performance with large receipt (100 words)."""
        orchestrator = ParallelPatternOrchestrator(timeout=0.1)
        words = self.create_receipt_words(100)

        # Warm up
        await orchestrator.detect_all_patterns(words)

        # Measure multiple runs
        times = []
        for _ in range(10):
            start = time.time()
            results = await orchestrator.detect_all_patterns(words)
            elapsed = (time.time() - start) * 1000  # ms
            times.append(elapsed)

            # Verify results
            assert not results["_metadata"]["timeout_occurred"]

        # Calculate statistics
        avg_time = statistics.mean(times)
        p95_time = statistics.quantiles(times, n=20)[18]

        print(f"\nLarge receipt (100 words):")
        print(f"  Average: {avg_time:.2f}ms")
        print(f"  95th percentile: {p95_time:.2f}ms")
        print(f"  Min: {min(times):.2f}ms")
        print(f"  Max: {max(times):.2f}ms")

        # Must meet performance target
        assert avg_time < 100
        assert p95_time < 100

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_parallel_vs_sequential(self):
        """Compare parallel vs sequential execution."""
        # Use larger word count where parallelization benefits outweigh overhead
        words = self.create_receipt_words(500)

        # Test parallel execution
        orchestrator = ParallelPatternOrchestrator(timeout=0.1)

        # Run multiple times to get more stable measurements
        parallel_times = []
        sequential_times = []

        for _ in range(3):
            parallel_start = time.time()
            await orchestrator.detect_all_patterns(words)
            parallel_time = (time.time() - parallel_start) * 1000
            parallel_times.append(parallel_time)

            # Test sequential execution (simulate)
            from receipt_label.pattern_detection import (
                ContactPatternDetector,
                CurrencyPatternDetector,
                DateTimePatternDetector,
                QuantityPatternDetector,
            )

            detectors = [
                CurrencyPatternDetector(),
                DateTimePatternDetector(),
                ContactPatternDetector(),
                QuantityPatternDetector(),
            ]

            sequential_start = time.time()
            for detector in detectors:
                await detector.detect(words)
            sequential_time = (time.time() - sequential_start) * 1000
            sequential_times.append(sequential_time)

        avg_parallel = sum(parallel_times) / len(parallel_times)
        avg_sequential = sum(sequential_times) / len(sequential_times)

        print(f"\nParallel vs Sequential (500 words, 3 runs avg):")
        print(f"  Parallel: {avg_parallel:.2f}ms")
        print(f"  Sequential: {avg_sequential:.2f}ms")
        print(f"  Speedup: {avg_sequential / avg_parallel:.2f}x")

        # Parallel should be reasonably competitive or faster
        # Allow up to 20% overhead for small datasets due to async/parallelization costs
        assert avg_parallel <= avg_sequential * 1.2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_benchmark_utility(self):
        """Test the built-in benchmark utility."""
        orchestrator = ParallelPatternOrchestrator(timeout=0.1)

        results = await orchestrator.benchmark_performance([10, 50, 100])

        print("\nBenchmark results:")
        for word_count, metrics in results.items():
            print(f"  {word_count}: {metrics['execution_time_ms']:.2f}ms")

        # All should complete within timeout
        for metrics in results.values():
            assert metrics["execution_time_ms"] < 100
            assert not metrics["timeout_occurred"]
