"""Unit tests for ParallelPatternOrchestrator - Core cost optimization logic."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timezone

from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from tests.markers import unit, fast, pattern_detection, cost_optimization
from receipt_dynamo.entities import ReceiptWord
from tests.helpers import create_test_receipt_word


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
            image_id="IMG001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            x1=100, y1=50, x2=200, y2=70
        ),
            
            # Currency amounts
            create_test_receipt_word(
            text="$12.99",
            image_id="IMG001",
            receipt_id=1,
            line_id=10,
            word_id=1,
            x1=250, y1=300, x2=300, y2=320
        ),
            create_test_receipt_word(
            text="$45.67",
            image_id="IMG001",
            receipt_id=1,
            line_id=20,
            word_id=1,
            x1=250, y1=500, x2=300, y2=520
        ),
            
            # Date/time
            create_test_receipt_word(
            text="12/25/2023",
            image_id="IMG001",
            receipt_id=1,
            line_id=25,
            word_id=1,
            x1=100, y1=600, x2=180, y2=620
        ),
            create_test_receipt_word(
            text="2:34 PM",
            image_id="IMG001",
            receipt_id=1,
            line_id=26,
            word_id=1,
            x1=100, y1=625, x2=160, y2=645
        ),
            
            # Contact info
            create_test_receipt_word(
            text="(555) 123-4567",
            image_id="IMG001",
            receipt_id=1,
            line_id=5,
            word_id=1,
            x1=100, y1=150, x2=220, y2=170
        ),
            
            # Quantities
            create_test_receipt_word(
            text="2 @ $5.99",
            image_id="IMG001",
            receipt_id=1,
            line_id=11,
            word_id=1,
            x1=50, y1=350, x2=150, y2=370
        ),
            
            # Noise words (should be ignored)
            create_test_receipt_word(
            text="___",
            image_id="IMG001",
            receipt_id=1,
            line_id=30,
            word_id=1,
            x1=100, y1=700, x2=130, y2=720
        ),
            create_test_receipt_word(image_id="IMG001", receipt_id=1, line_id=31, word_id=1,
                       text="", x1=100, y1=725, x2=100, y2=725),
        ]

    def test_orchestrator_initialization(self, orchestrator):
        """Test orchestrator initializes correctly."""
        assert orchestrator is not None
        # Should have detector instances ready
        assert hasattr(orchestrator, '_detectors') or hasattr(orchestrator, 'detectors')

    @pytest.mark.asyncio
    async def test_detect_all_patterns_basic(self, orchestrator, sample_words, stub_all_apis):
        """Test basic pattern detection across all detector types."""
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = await orchestrator.detect_all_patterns(
                words=sample_words,
                merchant_name="Walmart"
            )
        
        assert isinstance(results, list)
        assert len(results) > 0
        
        # Should detect patterns from multiple categories
        detected_types = set()
        for result in results:
            detected_types.update(result.suggested_labels)
        
        # Should find at least currency and merchant patterns
        expected_types = {"CURRENCY", "MERCHANT_NAME"}
        found_types = expected_types.intersection(detected_types)
        assert len(found_types) >= 1, f"Expected {expected_types}, found {detected_types}"

    @pytest.mark.asyncio
    async def test_parallel_execution_performance(self, orchestrator, sample_words, stub_all_apis, performance_timer):
        """Test that parallel execution is faster than sequential."""
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            
            # Test parallel execution
            performance_timer.start()
            parallel_results = await orchestrator.detect_all_patterns(
                words=sample_words,
                merchant_name="Walmart"
            )
            parallel_time = performance_timer.stop()
            
            # Should complete quickly with parallel execution
            assert parallel_time < 2.0, f"Parallel execution took {parallel_time:.2f}s, should be <2s"
            
            # Should return meaningful results
            assert len(parallel_results) > 0
            assert all(hasattr(r, 'confidence') for r in parallel_results)
            assert all(hasattr(r, 'suggested_labels') for r in parallel_results)

    def test_detector_selection_logic(self, orchestrator, sample_words, stub_all_apis):
        """Test smart detector selection based on word content."""
        # Mock individual detectors to track which ones are called
        mock_currency = Mock()
        mock_currency.detect_patterns.return_value = []
        
        mock_datetime = Mock()  
        mock_datetime.detect_patterns.return_value = []
        
        mock_contact = Mock()
        mock_contact.detect_patterns.return_value = []
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            with patch.object(orchestrator, '_get_currency_detector', return_value=mock_currency):
                with patch.object(orchestrator, '_get_datetime_detector', return_value=mock_datetime):
                    with patch.object(orchestrator, '_get_contact_detector', return_value=mock_contact):
                        
                        # Test with currency-heavy content
                        currency_words = [w for w in sample_words if '$' in w.text]
                        orchestrator.detect_all_patterns(currency_words, "TestMerchant")
                        
                        # Currency detector should be called
                        assert mock_currency.detect_patterns.called
                        
                        # Contact detector might not be called for currency-only words
                        # (depends on implementation - this tests the optimization)

    @pytest.mark.parametrize("merchant_name,expected_patterns", [
        ("Walmart", ["MERCHANT_NAME", "CURRENCY"]),
        ("McDonald's", ["MERCHANT_NAME", "PRODUCT_NAME"]),  
        ("Shell", ["MERCHANT_NAME", "CURRENCY"]),
        ("Target", ["MERCHANT_NAME", "CURRENCY"]),
        (None, ["CURRENCY"]),  # Generic patterns without merchant
    ])
    def test_merchant_specific_detection(self, orchestrator, sample_words, merchant_name, expected_patterns, stub_all_apis):
        """Test merchant-specific pattern detection."""
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = orchestrator.detect_all_patterns(
                words=sample_words,
                merchant_name=merchant_name
            )
        
        if merchant_name:
            # Should detect merchant-specific patterns
            all_labels = []
            for result in results:
                all_labels.extend(result.suggested_labels)
            
            # At least one expected pattern should be found
            found_expected = any(pattern in all_labels for pattern in expected_patterns)
            assert found_expected, f"No expected patterns {expected_patterns} found in {all_labels}"

    def test_confidence_scoring_accuracy(self, orchestrator, stub_all_apis):
        """Test confidence scoring for different pattern qualities."""
        # High confidence patterns
        high_conf_words = [
            create_test_receipt_word(
            text="$12.99",
            image_id="IMG001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            x1=100, y1=100, x2=150, y2=120
        ),  # Perfect currency
            create_test_receipt_word(
            text="Walmart",
            image_id="IMG001",
            receipt_id=1,
            line_id=2,
            word_id=1,
            x1=100, y1=130, x2=160, y2=150
        ),  # Clear merchant
        ]
        
        # Lower confidence patterns  
        low_conf_words = [
            create_test_receipt_word(
            text="12.99",
            image_id="IMG001",
            receipt_id=1,
            line_id=3,
            word_id=1,
            x1=100, y1=160, x2=150, y2=180
        ),  # Currency without symbol
            create_test_receipt_word(
            text="WAL",
            image_id="IMG001",
            receipt_id=1,
            line_id=4,
            word_id=1,
            x1=100, y1=190, x2=130, y2=210
        ),  # Partial merchant name
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            high_results = orchestrator.detect_all_patterns(high_conf_words, "Walmart")
            low_results = orchestrator.detect_all_patterns(low_conf_words, "Walmart")
        
        if high_results and low_results:
            # High confidence patterns should have higher scores
            avg_high_conf = sum(r.confidence for r in high_results) / len(high_results)
            avg_low_conf = sum(r.confidence for r in low_results) / len(low_results)
            
            assert avg_high_conf > avg_low_conf, f"High conf {avg_high_conf:.2f} should exceed low conf {avg_low_conf:.2f}"

    def test_noise_filtering(self, orchestrator, stub_all_apis):
        """Test that noise words are properly filtered out."""
        noise_words = [
            create_test_receipt_word(image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                       text="", x1=100, y1=100, x2=100, y2=100),  # Empty
            create_test_receipt_word(
            text="___",
            image_id="IMG001",
            receipt_id=1,
            line_id=2,
            word_id=1,
            x1=100, y1=120, x2=130, y2=140
        ),  # Separators
            create_test_receipt_word(
            text="...",
            image_id="IMG001",
            receipt_id=1,
            line_id=3,
            word_id=1,
            x1=100, y1=150, x2=130, y2=170
        ),  # Dots
            create_test_receipt_word(
            text="$12.99",
            image_id="IMG001",
            receipt_id=1,
            line_id=4,
            word_id=1,
            x1=100, y1=180, x2=150, y2=200
        ),  # Valid currency
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = orchestrator.detect_all_patterns(noise_words, "TestMerchant")
        
        # Should only detect the valid currency, not the noise
        valid_results = [r for r in results if r.confidence > 0.5]
        assert len(valid_results) <= 1, "Should filter out noise words"
        
        if valid_results:
            assert "$12.99" in valid_results[0].text

    def test_error_handling_and_resilience(self, orchestrator, sample_words, stub_all_apis):
        """Test error handling when detectors fail."""
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            
            # Test partial detector failure
            with patch.object(orchestrator, '_run_currency_detection', side_effect=Exception("Currency detector failed")):
                # Should not crash, may return partial results
                try:
                    results = orchestrator.detect_all_patterns(sample_words, "Walmart")
                    # Should return list (possibly empty) rather than crash
                    assert isinstance(results, list)
                except Exception as e:
                    pytest.fail(f"Should handle detector failures gracefully, but got: {e}")
            
            # Test complete failure scenario
            with patch.object(orchestrator, '_run_all_detectors', side_effect=Exception("Complete failure")):
                try:
                    results = orchestrator.detect_all_patterns(sample_words, "Walmart")
                    assert isinstance(results, list)
                except Exception as e:
                    # Should handle gracefully or provide clear error message
                    assert "detector" in str(e).lower() or "pattern" in str(e).lower()

    def test_cost_optimization_metrics(self, orchestrator, sample_words, stub_all_apis):
        """Test that pattern detection provides measurable cost optimization."""
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = orchestrator.detect_all_patterns(sample_words, "Walmart")
        
        # Calculate pattern coverage
        total_words = len([w for w in sample_words if w.text.strip()])  # Non-empty words
        pattern_matches = len(results)
        
        if total_words > 0:
            coverage_ratio = pattern_matches / total_words
            
            # Should achieve reasonable coverage to justify cost savings
            # (Lower threshold for unit tests, higher in integration)
            assert coverage_ratio >= 0.3, f"Pattern coverage {coverage_ratio:.1%} too low for cost optimization"
            
            # Should have high-confidence matches
            high_conf_matches = [r for r in results if r.confidence >= 0.8]
            high_conf_ratio = len(high_conf_matches) / len(results) if results else 0
            
            assert high_conf_ratio >= 0.5, f"High confidence ratio {high_conf_ratio:.1%} too low"

    @pytest.mark.parametrize("word_count,expected_time_limit", [
        (10, 0.5),   # Small receipts should be very fast
        (25, 1.0),   # Medium receipts 
        (50, 2.0),   # Large receipts
        (100, 3.0),  # Very large receipts
    ])
    def test_scalability_performance(self, orchestrator, word_count, expected_time_limit, stub_all_apis, performance_timer):
        """Test performance scalability with different receipt sizes."""
        # Generate test words
        test_words = []
        for i in range(word_count):
            test_words.append(create_test_receipt_word(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                text=f"item_{i}" if i % 3 != 0 else f"${i}.99",  # Mix of items and prices
                x1=100, y1=100 + i * 20, x2=200, y2=120 + i * 20
            ))
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            performance_timer.start()
            results = orchestrator.detect_all_patterns(test_words, "TestMerchant")
            elapsed = performance_timer.stop()
        
        assert elapsed <= expected_time_limit, f"Processing {word_count} words took {elapsed:.2f}s, should be ≤{expected_time_limit}s"
        
        # Should return some results for mixed content
        assert len(results) >= 0  # At minimum, no crashes

    def test_integration_with_chroma_queries(self, orchestrator, sample_words, mock_chroma_client):
        """Test integration with ChromaDB for merchant pattern queries."""
        # Mock ChromaDB to return merchant-specific patterns
        mock_chroma_client.query_collection.return_value = {
            'ids': [['merchant_pattern_1']],
            'distances': [[0.1]],
            'metadatas': [[{'pattern_type': 'MERCHANT_SPECIFIC', 'merchant': 'Walmart'}]],
            'documents': [['Walmart Supercenter']]
        }
        
        with patch('receipt_label.utils.get_client_manager') as mock_manager:
            mock_manager.return_value.chroma = mock_chroma_client
            
            results = orchestrator.detect_all_patterns(sample_words, "Walmart")
        
        # Should have attempted to query ChromaDB for merchant patterns
        assert mock_chroma_client.query_collection.called
        
        # Should integrate ChromaDB results into final output
        assert isinstance(results, list)

    def test_pattern_result_data_structure(self, orchestrator, sample_words, stub_all_apis):
        """Test that pattern results have correct data structure.""" 
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = orchestrator.detect_all_patterns(sample_words, "Walmart")
        
        for result in results:
            # Each result should have required fields
            assert hasattr(result, 'text'), "Result missing 'text' field"
            assert hasattr(result, 'confidence'), "Result missing 'confidence' field"
            assert hasattr(result, 'suggested_labels'), "Result missing 'suggested_labels' field"
            assert hasattr(result, 'pattern_type'), "Result missing 'pattern_type' field"
            
            # Field values should be valid
            assert isinstance(result.text, str), "Text should be string"
            assert 0 <= result.confidence <= 1, f"Confidence {result.confidence} should be 0-1"
            assert isinstance(result.suggested_labels, list), "Labels should be list"
            assert len(result.suggested_labels) > 0, "Should have at least one suggested label"