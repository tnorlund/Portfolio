"""Tests for parallel pattern orchestration."""

import asyncio
import time
from unittest.mock import Mock, patch

import pytest
from receipt_label.pattern_detection import (
    ParallelPatternOrchestrator,
    PatternType,
)

from receipt_dynamo.entities import ReceiptWord


class TestParallelPatternOrchestrator:
    """Test parallel pattern detection orchestration."""

    @pytest.fixture
    def orchestrator(self):
        """Create an orchestrator with 100ms timeout."""
        return ParallelPatternOrchestrator(timeout=0.1)

    @pytest.fixture
    def sample_receipt_words(self):
        """Create a realistic sample receipt."""
        words_data = [
            # Header
            {"text": "WALMART", "y": 20},
            {"text": "SUPERCENTER", "y": 20},
            # Address
            {"text": "123", "y": 40},
            {"text": "MAIN", "y": 40},
            {"text": "ST", "y": 40},
            # Phone
            {"text": "(555)", "y": 60},
            {"text": "123-4567", "y": 60},
            # Date/Time
            {"text": "01/15/2024", "y": 80},
            {"text": "14:35:22", "y": 80},
            # Items
            {"text": "MILK", "y": 120},
            {"text": "2", "y": 120},
            {"text": "@", "y": 120},
            {"text": "$3.99", "y": 120},
            {"text": "BREAD", "y": 140},
            {"text": "$2.50", "y": 140},
            # Totals
            {"text": "SUBTOTAL", "y": 200},
            {"text": "$10.48", "y": 200},
            {"text": "TAX", "y": 220},
            {"text": "$0.84", "y": 220},
            {"text": "TOTAL", "y": 240},
            {"text": "$11.32", "y": 240},
            # Website
            {"text": "www.walmart.com", "y": 280},
        ]

        words = []
        for i, data in enumerate(words_data):
            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=data["y"] // 20,
                word_id=i,
                text=data["text"],
                bounding_box={
                    "x": i * 60,
                    "y": data["y"],
                    "width": 50,
                    "height": 20,
                },
                top_left={"x": i * 60, "y": data["y"]},
                top_right={"x": i * 60 + 50, "y": data["y"]},
                bottom_left={"x": i * 60, "y": data["y"] + 20},
                bottom_right={"x": i * 60 + 50, "y": data["y"] + 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
            words.append(word)

        return words

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_parallel_detection(
        self, orchestrator, sample_receipt_words
    ):
        """Test that all detectors run in parallel."""
        start_time = time.time()
        results = await orchestrator.detect_all_patterns(sample_receipt_words)
        elapsed_time = time.time() - start_time

        # Should complete within timeout
        assert elapsed_time < 0.15  # 150ms with buffer

        # Should have results from all detectors
        assert "currency" in results
        assert "datetime" in results
        assert "contact" in results
        assert "quantity" in results
        assert "_metadata" in results

        # Check metadata
        assert results["_metadata"]["execution_time_ms"] < 150
        assert results["_metadata"]["word_count"] == len(sample_receipt_words)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pattern_detection_results(
        self, orchestrator, sample_receipt_words
    ):
        """Test that patterns are correctly detected."""
        results = await orchestrator.detect_all_patterns(sample_receipt_words)

        # Currency detection
        currency_matches = results["currency"]
        assert len(currency_matches) > 0
        currency_texts = [m.matched_text for m in currency_matches]
        assert "$3.99" in currency_texts
        assert "$11.32" in currency_texts

        # Date/Time detection
        datetime_matches = results["datetime"]
        assert len(datetime_matches) > 0
        datetime_texts = [m.matched_text for m in datetime_matches]
        assert "01/15/2024" in datetime_texts
        assert "14:35:22" in datetime_texts

        # Contact detection
        contact_matches = results["contact"]
        assert len(contact_matches) > 0
        contact_texts = [m.matched_text for m in contact_matches]
        # Phone number might be detected as single match or split
        assert any("123-4567" in text for text in contact_texts)
        assert "www.walmart.com" in contact_texts

        # Quantity detection
        quantity_matches = results["quantity"]
        assert len(quantity_matches) > 0
        # Should detect "2 @" pattern

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_aggregation(self, orchestrator, sample_receipt_words):
        """Test pattern aggregation functionality."""
        results = await orchestrator.detect_all_patterns(sample_receipt_words)
        aggregated = orchestrator.aggregate_patterns(results)

        # Should have aggregated by pattern type
        assert "GRAND_TOTAL" in aggregated
        assert "TAX" in aggregated
        assert "DATE" in aggregated
        assert "WEBSITE" in aggregated

        # Check aggregation structure
        total_info = aggregated["GRAND_TOTAL"]
        assert "matches" in total_info
        assert "count" in total_info
        assert "high_confidence_count" in total_info
        assert total_info["count"] >= 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_essential_fields_check(
        self, orchestrator, sample_receipt_words
    ):
        """Test essential fields detection."""
        results = await orchestrator.detect_all_patterns(sample_receipt_words)
        essential = orchestrator.get_essential_fields_status(results)

        assert essential["has_date"] is True  # We have 01/15/2024
        assert essential["has_total"] is True  # We have TOTAL $11.32
        assert (
            essential["has_merchant"] is False
        )  # No merchant patterns provided
        assert essential["has_product"] is True  # We have quantity patterns

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_merchant_patterns_integration(
        self, orchestrator, sample_receipt_words
    ):
        """Test integration with merchant patterns from Epic #189."""
        merchant_patterns = {
            "word_patterns": {
                "walmart": "MERCHANT_NAME",
                "milk": "PRODUCT_NAME",
                "bread": "PRODUCT_NAME",
            },
            "confidence_threshold": 0.9,
        }

        results = await orchestrator.detect_all_patterns(
            sample_receipt_words, merchant_patterns
        )

        # Should have merchant results
        assert "merchant" in results
        merchant_matches = results["merchant"]
        assert len(merchant_matches) > 0

        # Check specific matches
        matched_labels = [m.metadata["label"] for m in merchant_matches]
        assert "MERCHANT_NAME" in matched_labels
        assert "PRODUCT_NAME" in matched_labels

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_timeout_handling(self, orchestrator):
        """Test that timeout is properly handled."""
        # Create a slow detector
        slow_detector = Mock()

        async def slow_detect(words):
            await asyncio.sleep(0.2)  # Longer than timeout
            return []

        slow_detector.detect = slow_detect

        # Replace one detector with slow one
        orchestrator._detectors["currency"] = slow_detector

        # Should still complete within timeout
        words = [
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=1,
                word_id=1,
                text="TEST",
                bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 50, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 50, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
        ]

        start_time = time.time()
        results = await orchestrator.detect_all_patterns(words)
        elapsed_time = time.time() - start_time

        assert elapsed_time < 0.15  # Should timeout at 100ms
        assert results["_metadata"]["timeout_occurred"] is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_error_handling(self, orchestrator):
        """Test that errors in one detector don't crash the whole system."""
        # Create a failing detector
        failing_detector = Mock()

        async def failing_detect(words):
            raise ValueError("Test error")

        failing_detector.detect = failing_detect

        # Replace one detector
        orchestrator._detectors["currency"] = failing_detector

        # Should still get results from other detectors
        words = [
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=1,
                word_id=1,
                text="01/15/2024",
                bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 50, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 50, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
        ]

        results = await orchestrator.detect_all_patterns(words)

        # Currency should be empty due to error
        assert results["currency"] == []

        # But datetime should still work
        assert len(results["datetime"]) > 0

    @pytest.mark.unit
    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_performance_benchmark(self, orchestrator):
        """Test the performance benchmarking functionality."""
        results = await orchestrator.benchmark_performance([10, 50, 100])

        # Should have results for each word count
        assert "10_words" in results
        assert "50_words" in results
        assert "100_words" in results

        # Execution time should increase with word count
        assert (
            results["10_words"]["execution_time_ms"]
            < results["100_words"]["execution_time_ms"]
        )

        # All should complete within timeout
        for key, metrics in results.items():
            assert metrics["execution_time_ms"] < 100  # 100ms timeout

    @pytest.mark.unit
    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_performance_target(
        self, orchestrator, sample_receipt_words
    ):
        """Test that we meet the <100ms performance target."""
        # Run multiple times to get average
        times = []
        for _ in range(5):
            start_time = time.time()
            await orchestrator.detect_all_patterns(sample_receipt_words)
            elapsed = (time.time() - start_time) * 1000  # Convert to ms
            times.append(elapsed)

        avg_time = sum(times) / len(times)

        # Should meet performance target
        assert avg_time < 100  # Target is <100ms

        # 95th percentile should also be under target
        times.sort()
        p95 = times[int(len(times) * 0.95)]
        assert p95 < 100
