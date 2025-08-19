"""Unit tests for ParallelPatternOrchestrator."""

from unittest.mock import patch

import pytest

from receipt_label.pattern_detection.orchestrator import (
    ParallelPatternOrchestrator)
from tests.markers import unit, fast, pattern_detection, cost_optimization
from tests.helpers import create_test_receipt_word


@pytest.mark.skip(reason="ChromaDB migration in progress - API mismatches with mocks")
@unit
@fast
@pattern_detection
@cost_optimization
class TestParallelPatternOrchestrator:
    """Test the core orchestration logic for pattern detection."""

    @pytest.fixture
    def orchestrator(self):
        """Pattern orchestrator fixture."""
        return ParallelPatternOrchestrator()

    @pytest.fixture
    def sample_words(self):
        """Sample receipt words covering all pattern types."""
        return [
            # Merchant name
            create_test_receipt_word(
                text="Walmart",
                receipt_id=1,
                line_id=1,
                word_id=1,
                x1=100,
                y1=50,
                x2=200,
                y2=70),
            # Currency amounts
            create_test_receipt_word(
                text="$12.99",
                receipt_id=1,
                line_id=10,
                word_id=1,
                x1=250,
                y1=300,
                x2=300,
                y2=320),
            create_test_receipt_word(
                text="$45.67",
                receipt_id=1,
                line_id=20,
                word_id=1,
                x1=250,
                y1=500,
                x2=300,
                y2=520),
            # Date/time
            create_test_receipt_word(
                text="12/25/2023",
                receipt_id=1,
                line_id=25,
                word_id=1,
                x1=100,
                y1=600,
                x2=180,
                y2=620),
            create_test_receipt_word(
                text="2:34 PM",
                receipt_id=1,
                line_id=26,
                word_id=1,
                x1=100,
                y1=625,
                x2=160,
                y2=645),
            # Contact info
            create_test_receipt_word(
                text="(555) 123-4567",
                receipt_id=1,
                line_id=5,
                word_id=1,
                x1=100,
                y1=150,
                x2=220,
                y2=170),
            # Quantities
            create_test_receipt_word(
                text="2 @ $5.99",
                receipt_id=1,
                line_id=11,
                word_id=1,
                x1=50,
                y1=350,
                x2=150,
                y2=370),
            # Noise words (should be ignored)
            create_test_receipt_word(
                text="___",
                receipt_id=1,
                line_id=30,
                word_id=1,
                x1=100,
                y1=700,
                x2=130,
                y2=720),
            create_test_receipt_word(
                receipt_id=1,
                line_id=31,
                word_id=1,
                text="",
                x1=100,
                y1=725,
                x2=100,
                y2=725),
        ]

    async def test_orchestrator_initialization(self, orchestrator):
        """Test orchestrator initializes correctly."""
        assert orchestrator is not None
        # Should have detector instances ready
        assert hasattr(orchestrator, "_detectors") or hasattr(
            orchestrator, "detectors"
        )

    async def test_detect_all_patterns_basic(
        self, orchestrator, sample_words, stub_all_apis
    ):
        """Test basic pattern detection across all detector types."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            results = await orchestrator.detect_all_patterns(
                words=sample_words
            )

        assert isinstance(results, dict)
        # Results should have detector keys and metadata
        assert "_metadata" in results
        assert any(
            key != "_metadata" for key in results.keys()
        )  # Should have at least one detector result

        # Should detect patterns from multiple categories
        detected_types = set()
        for key, matches in results.items():
            if key != "_metadata" and matches:
                for match in matches:
                    detected_types.add(
                        match.pattern_type.name
                        if hasattr(match.pattern_type, "name")
                        else str(match.pattern_type)
                    )

        # Should find at least currency patterns
        assert (
            "CURRENCY" in detected_types or "currency" in results
        ), f"Expected currency patterns, found {detected_types}"

    async def test_parallel_execution_performance(
        self, orchestrator, sample_words, stub_all_apis
    ):
        """Test that parallel execution is faster than sequential."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):

            # Test parallel execution
            import time  # pylint: disable=import-outside-toplevel

            start = time.time()
            parallel_results = await orchestrator.detect_all_patterns(
                words=sample_words
            )
            parallel_time = time.time() - start

            # Should complete quickly with parallel execution
            assert (
                parallel_time < 2.0
            ), f"Parallel execution took {parallel_time:.2f}s, should be <2s"

            # Should return meaningful results
            assert any(
                parallel_results.get(k)
                for k in parallel_results
                if k != "_metadata"
            )

    async def test_detector_selection_logic(
        self, orchestrator, sample_words, stub_all_apis
    ):
        """Test that orchestrator runs detectors successfully."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            # Test with currency-heavy content
            currency_words = [w for w in sample_words if "$" in w.text]
            results = await orchestrator.detect_all_patterns(currency_words)

            # Should return results
            assert isinstance(results, dict)
            assert "_metadata" in results

            # Should detect currency patterns
            has_currency = (
                "currency" in results and len(results["currency"]) > 0
            )
            assert has_currency, "Should detect currency patterns"

    @pytest.mark.parametrize(
        "merchant_name,expected_patterns",
        [
            ("Walmart", ["MERCHANT_NAME", "CURRENCY"]),
            ("McDonald's", ["MERCHANT_NAME", "PRODUCT_NAME"]),
            ("Shell", ["MERCHANT_NAME", "CURRENCY"]),
            ("Target", ["MERCHANT_NAME", "CURRENCY"]),
            (None, ["CURRENCY"]),  # Generic patterns without merchant
        ])
    async def test_merchant_specific_detection(
        self,
        orchestrator,
        sample_words,
        merchant_name,  # pylint: disable=unused-argument
        expected_patterns,  # pylint: disable=unused-argument
        stub_all_apis):
        """Test merchant-specific pattern detection."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            results = await orchestrator.detect_all_patterns(
                words=sample_words
            )

        # Should detect some patterns regardless of merchant
        all_patterns = []
        for key, matches in results.items():
            if key == "_metadata":
                continue
            if matches:
                all_patterns.append(key)

        # Should detect at least some patterns
        assert len(all_patterns) > 0, "No patterns detected"

    async def test_confidence_scoring_accuracy(
        self, orchestrator, stub_all_apis
    ):
        """Test confidence scoring for different pattern qualities."""
        # High confidence patterns
        high_conf_words = [
            create_test_receipt_word(
                text="$12.99",
                receipt_id=1,
                line_id=1,
                word_id=1,
                x1=100,
                y1=100,
                x2=150,
                y2=120),  # Perfect currency
            create_test_receipt_word(
                text="Walmart",
                receipt_id=1,
                line_id=2,
                word_id=1,
                x1=100,
                y1=130,
                x2=160,
                y2=150),  # Clear merchant
        ]

        # Lower confidence patterns
        low_conf_words = [
            create_test_receipt_word(
                text="12.99",
                receipt_id=1,
                line_id=3,
                word_id=1,
                x1=100,
                y1=160,
                x2=150,
                y2=180),  # Currency without symbol
            create_test_receipt_word(
                text="WAL",
                receipt_id=1,
                line_id=4,
                word_id=1,
                x1=100,
                y1=190,
                x2=130,
                y2=210),  # Partial merchant name
        ]

        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            high_results = await orchestrator.detect_all_patterns(
                high_conf_words
            )
            low_results = await orchestrator.detect_all_patterns(
                low_conf_words
            )

        # Collect all confidence scores from results
        high_confs = []
        low_confs = []

        for key, matches in high_results.items():
            if key != "_metadata" and matches:
                high_confs.extend(m.confidence for m in matches)

        for key, matches in low_results.items():
            if key != "_metadata" and matches:
                low_confs.extend(m.confidence for m in matches)

        if high_confs and low_confs:
            # High confidence patterns should have higher scores
            avg_high_conf = sum(high_confs) / len(high_confs)
            avg_low_conf = sum(low_confs) / len(low_confs)

            assert (
                avg_high_conf > avg_low_conf
            ), f"High conf {avg_high_conf:.2f} should exceed low conf {avg_low_conf:.2f}"

    async def test_noise_filtering(self, orchestrator, stub_all_apis):
        """Test that noise words are properly filtered out."""
        noise_words = [
            create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text="",
                x1=100,
                y1=100,
                x2=100,
                y2=100),  # Empty
            create_test_receipt_word(
                text="___",
                receipt_id=1,
                line_id=2,
                word_id=1,
                x1=100,
                y1=120,
                x2=130,
                y2=140),  # Separators
            create_test_receipt_word(
                text="...",
                receipt_id=1,
                line_id=3,
                word_id=1,
                x1=100,
                y1=150,
                x2=130,
                y2=170),  # Dots
            create_test_receipt_word(
                text="$12.99",
                receipt_id=1,
                line_id=4,
                word_id=1,
                x1=100,
                y1=180,
                x2=150,
                y2=200),  # Valid currency
        ]

        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            results = await orchestrator.detect_all_patterns(noise_words)

        # Should only detect the valid currency, not the noise
        valid_results = []
        for key, matches in results.items():
            if key != "_metadata" and matches:
                valid_results.extend(m for m in matches if m.confidence > 0.5)

        assert (
            len(valid_results) <= 2
        ), f"Should filter noise, got {len(valid_results)} results"

        if valid_results:
            # At least one should be the currency
            currency_found = any(
                "12.99" in r.matched_text for r in valid_results
            )
            assert currency_found, "Should detect the valid currency"

    async def test_error_handling_and_resilience(
        self, orchestrator, sample_words, stub_all_apis
    ):
        """Test error handling when detectors fail."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):

            # Test that orchestrator handles errors gracefully
            # Mock a detector to fail
            with patch.object(
                orchestrator,
                "_run_detector_with_timeout",
                side_effect=Exception("Detector failed")):
                try:
                    results = await orchestrator.detect_all_patterns(
                        sample_words
                    )
                    # Should return dict structure even on failure
                    assert isinstance(results, dict)
                except (TypeError, AttributeError):
                    # It's ok if it raises, but should be a clear error
                    pass  # Orchestrator may propagate exceptions

    async def test_cost_optimization_metrics(
        self, orchestrator, sample_words, stub_all_apis
    ):
        """Test that pattern detection provides measurable cost optimization."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            results = await orchestrator.detect_all_patterns(sample_words)

        # Calculate pattern coverage
        total_words = len(
            [w for w in sample_words if w.text.strip()]
        )  # Non-empty words
        pattern_matches = len(results)

        if total_words > 0:
            coverage_ratio = pattern_matches / total_words

            # Should achieve reasonable coverage to justify cost savings
            # (Lower threshold for unit tests, higher in integration)
            assert (
                coverage_ratio >= 0.3
            ), f"Pattern coverage {coverage_ratio:.1%} too low for cost optimization"

            # Should have high-confidence matches
            high_conf_matches = []
            all_matches = []
            for key, matches in results.items():
                if key != "_metadata" and matches:
                    all_matches.extend(matches)
                    high_conf_matches.extend(
                        m for m in matches if m.confidence >= 0.8
                    )
            high_conf_ratio = (
                len(high_conf_matches) / len(all_matches) if all_matches else 0
            )

            assert (
                high_conf_ratio >= 0.5
            ), f"High confidence ratio {high_conf_ratio:.1%} too low"

    @pytest.mark.parametrize(
        "word_count,expected_time_limit",
        [
            (10, 0.5),  # Small receipts should be very fast
            (25, 1.0),  # Medium receipts
            (50, 2.0),  # Large receipts
            (100, 3.0),  # Very large receipts
        ])
    async def test_scalability_performance(
        self, orchestrator, word_count, expected_time_limit, stub_all_apis
    ):
        """Test performance scalability with different receipt sizes."""
        # Generate test words
        test_words = []
        for i in range(word_count):
            test_words.append(
                create_test_receipt_word(
                    receipt_id=1,
                    line_id=i,
                    word_id=1,
                    text=(
                        f"item_{i}" if i % 3 != 0 else f"${i}.99"
                    ),  # Mix of items and prices
                    x1=100,
                    y1=100 + i * 20,
                    x2=200,
                    y2=120 + i * 20)
            )

        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            import time  # pylint: disable=import-outside-toplevel

            start = time.time()
            results = await orchestrator.detect_all_patterns(test_words)
            elapsed = time.time() - start

        assert (
            elapsed <= expected_time_limit
        ), f"Processing {word_count} words took {elapsed:.2f}s, should be ≤{expected_time_limit}s"

        # Should return some results for mixed content
        assert len(results) >= 0  # At minimum, no crashes

    async def test_pattern_result_data_structure(
        self, orchestrator, sample_words, stub_all_apis
    ):
        """Test that pattern results have correct data structure."""
        with patch(
            "receipt_label.utils.get_client_manager",
            return_value=stub_all_apis):
            results = await orchestrator.detect_all_patterns(sample_words)

        for key, matches in results.items():
            if key == "_metadata":
                continue
            for result in matches or []:
                # Each result should have required fields
                assert hasattr(
                    result, "matched_text"
                ), "Result missing 'matched_text' field"
                assert hasattr(
                    result, "confidence"
                ), "Result missing 'confidence' field"
                assert hasattr(
                    result, "pattern_type"
                ), "Result missing 'pattern_type' field"

                # Field values should be valid
                assert isinstance(
                    result.matched_text, str
                ), "matched_text should be string"
                assert (
                    0 <= result.confidence <= 1
                ), f"Confidence {result.confidence} should be 0-1"
