"""End-to-end tests for complete receipt processing workflow."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from receipt_label.tests.markers import end_to_end, pattern_detection, cost_optimization
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel, ReceiptMetadata


@end_to_end
@pattern_detection  
@cost_optimization
class TestReceiptProcessingWorkflow:
    """Test complete receipt processing from OCR to labeled output."""

    @pytest.fixture
    def walmart_receipt_words(self):
        """Complete Walmart receipt word data."""
        return [
            # Header
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=1, word_id=1, 
                       text="Walmart", x1=100, y1=50, x2=200, y2=70),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                       text="Supercenter", x1=100, y1=75, x2=180, y2=95),
            
            # Address
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=3, word_id=1,
                       text="123", x1=100, y1=100, x2=130, y2=120),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=3, word_id=2,
                       text="Main", x1=135, y1=100, x2=170, y2=120),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=3, word_id=3,
                       text="St", x1=175, y1=100, x2=195, y2=120),
            
            # Phone
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=4, word_id=1,
                       text="(555)", x1=100, y1=125, x2=140, y2=145),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=4, word_id=2,
                       text="123-4567", x1=145, y1=125, x2=210, y2=145),
            
            # Items
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=10, word_id=1,
                       text="BANANAS", x1=50, y1=300, x2=120, y2=320),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=10, word_id=2,
                       text="$2.99", x1=250, y1=300, x2=300, y2=320),
            
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=11, word_id=1,
                       text="MILK", x1=50, y1=325, x2=90, y2=345),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=11, word_id=2,
                       text="GALLON", x1=95, y1=325, x2=150, y2=345),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=11, word_id=3,
                       text="$3.49", x1=250, y1=325, x2=300, y2=345),
            
            # Totals section
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=20, word_id=1,
                       text="SUBTOTAL", x1=150, y1=500, x2=220, y2=520),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=20, word_id=2,
                       text="$6.48", x1=250, y1=500, x2=300, y2=520),
            
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=21, word_id=1,
                       text="TAX", x1=180, y1=525, x2=210, y2=545),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=21, word_id=2,
                       text="$0.52", x1=250, y1=525, x2=300, y2=545),
            
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=22, word_id=1,
                       text="TOTAL", x1=180, y1=550, x2=220, y2=570),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=22, word_id=2,
                       text="$7.00", x1=250, y1=550, x2=300, y2=570),
            
            # Footer
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=25, word_id=1,
                       text="12/25/2023", x1=100, y1=600, x2=180, y2=620),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=26, word_id=1,
                       text="2:34", x1=100, y1=625, x2=140, y2=645),
            ReceiptWord(image_id="IMG001", receipt_id=1, line_id=26, word_id=2,
                       text="PM", x1=145, y1=625, x2=165, y2=645),
        ]

    @pytest.fixture
    def walmart_receipt_metadata(self):
        """Walmart receipt metadata."""
        return ReceiptMetadata(
            image_id="IMG001",
            receipt_id=1,
            canonical_merchant_name="Walmart",
            canonical_address="123 Main St, City, State 12345",
            phone_number="555-123-4567",
            timestamp_processed=datetime.now(timezone.utc)
        )

    @pytest.fixture
    def expected_pattern_labels(self):
        """Expected labels from pattern detection."""
        return {
            "Walmart": ["MERCHANT_NAME"],
            "$2.99": ["CURRENCY", "UNIT_PRICE"],
            "$3.49": ["CURRENCY", "UNIT_PRICE"], 
            "$6.48": ["CURRENCY", "SUBTOTAL"],
            "$0.52": ["CURRENCY", "TAX_AMOUNT"],
            "$7.00": ["CURRENCY", "GRAND_TOTAL"],
            "BANANAS": ["PRODUCT_NAME"],
            "MILK GALLON": ["PRODUCT_NAME"],  # Multi-word
            "(555) 123-4567": ["PHONE_NUMBER"],
            "123 Main St": ["ADDRESS"],  # Multi-word
            "12/25/2023": ["DATE"],
            "2:34 PM": ["TIME"]
        }

    def test_complete_walmart_receipt_processing(
        self, 
        walmart_receipt_words, 
        walmart_receipt_metadata,
        expected_pattern_labels,
        stub_all_apis,
        performance_timer
    ):
        """Test complete processing of Walmart receipt."""
        from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
        
        performance_timer.start()
        
        # Step 1: Pattern Detection
        orchestrator = ParallelPatternOrchestrator()
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            pattern_results = orchestrator.detect_all_patterns(
                words=walmart_receipt_words,
                merchant_name="Walmart"
            )
        
        pattern_time = performance_timer.stop()
        
        # Step 2: Verify Pattern Detection Results
        detected_labels = {}
        for result in pattern_results:
            text = result.text
            labels = result.suggested_labels
            if text not in detected_labels:
                detected_labels[text] = []
            detected_labels[text].extend(labels)
        
        # Should detect key merchant info
        assert "MERCHANT_NAME" in detected_labels.get("Walmart", [])
        
        # Should detect currency amounts with context
        assert "GRAND_TOTAL" in detected_labels.get("$7.00", [])
        assert "SUBTOTAL" in detected_labels.get("$6.48", [])
        assert "TAX_AMOUNT" in detected_labels.get("$0.52", [])
        
        # Should detect contact information
        phone_variations = ["(555) 123-4567", "(555)", "123-4567"]
        found_phone = any("PHONE_NUMBER" in detected_labels.get(variation, []) 
                         for variation in phone_variations)
        assert found_phone, "Phone number not detected in any variation"
        
        # Should detect temporal information
        assert "DATE" in detected_labels.get("12/25/2023", [])
        time_variations = ["2:34 PM", "2:34", "PM"]
        found_time = any("TIME" in detected_labels.get(variation, [])
                        for variation in time_variations)
        assert found_time, "Time not detected in any variation"
        
        # Step 3: Performance Validation
        assert pattern_time < 1.0, f"Pattern detection took {pattern_time:.2f}s, should be <1s"
        
        # Step 4: Cost Optimization Validation
        total_words = len(walmart_receipt_words)
        pattern_labeled_words = len([word for word in walmart_receipt_words 
                                   if word.text in detected_labels])
        
        coverage_ratio = pattern_labeled_words / total_words
        assert coverage_ratio >= 0.6, f"Pattern coverage {coverage_ratio:.1%} too low, should be ≥60%"
        
        # Essential fields must be covered by patterns
        essential_fields = ["MERCHANT_NAME", "GRAND_TOTAL", "DATE"]
        covered_essentials = []
        for labels_list in detected_labels.values():
            covered_essentials.extend(labels_list)
        
        for essential in essential_fields:
            assert essential in covered_essentials, f"Essential field {essential} not detected"
        
        print(f"✅ Pattern detection completed in {pattern_time:.3f}s")
        print(f"✅ Pattern coverage: {coverage_ratio:.1%} of words")
        print(f"✅ Essential fields covered: {essential_fields}")

    def test_cost_optimization_effectiveness(
        self,
        walmart_receipt_words,
        walmart_receipt_metadata,
        stub_all_apis
    ):
        """Test that pattern detection achieves target cost savings."""
        from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
        from receipt_label.utils.cost_calculator import estimate_gpt_cost
        
        orchestrator = ParallelPatternOrchestrator()
        
        # Measure what would happen without patterns (all words to GPT)
        baseline_cost = estimate_gpt_cost(
            word_count=len(walmart_receipt_words),
            avg_words_per_prompt=50
        )
        
        # Run pattern detection
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            pattern_results = orchestrator.detect_all_patterns(
                words=walmart_receipt_words,
                merchant_name="Walmart"
            )
        
        # Calculate words that still need GPT
        pattern_covered_words = set()
        for result in pattern_results:
            # Find the word that matched this pattern
            for word in walmart_receipt_words:
                if word.text == result.text:
                    pattern_covered_words.add((word.line_id, word.word_id))
        
        uncovered_words = len(walmart_receipt_words) - len(pattern_covered_words)
        optimized_cost = estimate_gpt_cost(
            word_count=uncovered_words,
            avg_words_per_prompt=50
        )
        
        # Calculate savings
        cost_reduction = (baseline_cost - optimized_cost) / baseline_cost
        
        # Should achieve significant cost reduction (target: 84%)
        assert cost_reduction >= 0.70, f"Cost reduction {cost_reduction:.1%} below 70% target"
        
        print(f"💰 Baseline cost: ${baseline_cost:.4f}")
        print(f"💰 Optimized cost: ${optimized_cost:.4f}") 
        print(f"💰 Cost reduction: {cost_reduction:.1%}")

    @pytest.mark.parametrize("receipt_type,merchant_patterns", [
        ("walmart", ["Walmart", "SUBTOTAL", "TOTAL"]),
        ("mcdonalds", ["McDonald's", "Big Mac", "QTY"]),
        ("gas_station", ["Shell", "GALLONS", "PRICE/GAL"])
    ])
    def test_merchant_specific_processing(
        self,
        receipt_type,
        merchant_patterns,
        stub_all_apis
    ):
        """Test merchant-specific pattern detection."""
        from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
        
        # Create merchant-specific test data
        if receipt_type == "mcdonalds":
            words = [
                ReceiptWord(image_id="IMG002", receipt_id=2, line_id=1, word_id=1,
                           text="McDonald's", x1=100, y1=50, x2=200, y2=70),
                ReceiptWord(image_id="IMG002", receipt_id=2, line_id=5, word_id=1,
                           text="Big Mac", x1=50, y1=200, x2=120, y2=220),
                ReceiptWord(image_id="IMG002", receipt_id=2, line_id=5, word_id=2,
                           text="$5.49", x1=250, y1=200, x2=300, y2=220)
            ]
            merchant_name = "McDonalds"
        else:
            # Default to basic pattern for other types
            words = [
                ReceiptWord(image_id="IMG003", receipt_id=3, line_id=1, word_id=1,
                           text=merchant_patterns[0], x1=100, y1=50, x2=200, y2=70)
            ]
            merchant_name = receipt_type.title()
        
        orchestrator = ParallelPatternOrchestrator()
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = orchestrator.detect_all_patterns(
                words=words,
                merchant_name=merchant_name
            )
        
        # Should detect merchant-specific patterns
        detected_texts = [r.text for r in results]
        
        # At least the merchant name should be detected
        assert any(pattern in detected_texts for pattern in merchant_patterns[:1])

    def test_error_recovery_and_graceful_degradation(
        self,
        walmart_receipt_words,
        stub_all_apis
    ):
        """Test system behavior under various error conditions."""
        from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
        
        orchestrator = ParallelPatternOrchestrator()
        
        # Test 1: Network errors should not crash
        with patch('receipt_label.utils.get_client_manager', side_effect=Exception("Network error")):
            try:
                results = orchestrator.detect_all_patterns(
                    words=walmart_receipt_words[:5],  # Smaller batch for faster test
                    merchant_name="Walmart"
                )
                # Should return empty results rather than crash
                assert isinstance(results, list)
            except Exception as e:
                pytest.fail(f"Should handle network errors gracefully, but got: {e}")
        
        # Test 2: Malformed input should be handled
        malformed_words = [None, "not a word object", 12345]
        
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            results = orchestrator.detect_all_patterns(
                words=malformed_words,
                merchant_name="Walmart"
            )
            # Should return empty results for malformed input
            assert results == []
        
        # Test 3: Partial detector failures should not affect others
        with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
            with patch.object(orchestrator, '_run_currency_detection', side_effect=Exception("Currency detector failed")):
                results = orchestrator.detect_all_patterns(
                    words=walmart_receipt_words[:3],
                    merchant_name="Walmart"
                )
                # Should still get results from other detectors
                # (exact results depend on implementation, but shouldn't crash)
                assert isinstance(results, list)

    def test_end_to_end_performance_benchmark(
        self,
        walmart_receipt_words,
        stub_all_apis,
        performance_timer
    ):
        """Benchmark end-to-end processing performance."""
        from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
        
        # Test with various receipt sizes
        receipt_sizes = [10, 25, 50, 100]
        results = {}
        
        orchestrator = ParallelPatternOrchestrator()
        
        for size in receipt_sizes:
            # Create receipt of specified size
            test_words = (walmart_receipt_words * (size // len(walmart_receipt_words) + 1))[:size]
            
            performance_timer.start()
            
            with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
                pattern_results = orchestrator.detect_all_patterns(
                    words=test_words,
                    merchant_name="Walmart"
                )
            
            elapsed = performance_timer.stop()
            results[size] = {
                "time": elapsed,
                "patterns_found": len(pattern_results),
                "throughput": size / elapsed if elapsed > 0 else float('inf')
            }
        
        # Verify performance scales reasonably
        for size, metrics in results.items():
            # Should process at least 50 words per second
            assert metrics["throughput"] >= 50, f"Throughput {metrics['throughput']:.1f} words/sec too slow for {size} words"
            
            # Should complete within reasonable time limits
            max_time = size * 0.02  # 20ms per word maximum
            assert metrics["time"] <= max_time, f"Processing {size} words took {metrics['time']:.3f}s, should be ≤{max_time:.3f}s"
        
        print("📊 Performance Benchmark Results:")
        for size, metrics in results.items():
            print(f"  {size:3d} words: {metrics['time']:.3f}s ({metrics['throughput']:.1f} words/sec)")

    def test_multi_receipt_batch_processing(self, stub_all_apis):
        """Test processing multiple receipts in batch for efficiency."""
        from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
        
        # Create multiple different receipts
        receipts = [
            {
                "merchant": "Walmart",
                "words": [
                    ReceiptWord(image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                               text="Walmart", x1=100, y1=50, x2=200, y2=70),
                    ReceiptWord(image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                               text="$12.99", x1=250, y1=100, x2=300, y2=120)
                ]
            },
            {
                "merchant": "Target", 
                "words": [
                    ReceiptWord(image_id="IMG002", receipt_id=2, line_id=1, word_id=1,
                               text="Target", x1=100, y1=50, x2=200, y2=70),
                    ReceiptWord(image_id="IMG002", receipt_id=2, line_id=2, word_id=1,
                               text="$8.49", x1=250, y1=100, x2=300, y2=120)
                ]
            },
            {
                "merchant": "McDonalds",
                "words": [
                    ReceiptWord(image_id="IMG003", receipt_id=3, line_id=1, word_id=1,
                               text="McDonald's", x1=100, y1=50, x2=200, y2=70),
                    ReceiptWord(image_id="IMG003", receipt_id=3, line_id=2, word_id=1,
                               text="Big Mac", x1=50, y1=100, x2=120, y2=120),
                    ReceiptWord(image_id="IMG003", receipt_id=3, line_id=2, word_id=2,
                               text="$5.49", x1=250, y1=100, x2=300, y2=120)
                ]
            }
        ]
        
        orchestrator = ParallelPatternOrchestrator()
        all_results = {}
        
        # Process each receipt
        for i, receipt in enumerate(receipts):
            with patch('receipt_label.utils.get_client_manager', return_value=stub_all_apis):
                results = orchestrator.detect_all_patterns(
                    words=receipt["words"],
                    merchant_name=receipt["merchant"]
                )
                all_results[receipt["merchant"]] = results
        
        # Verify each receipt was processed correctly
        assert len(all_results) == 3
        
        # Each receipt should have detected its merchant name
        for merchant, results in all_results.items():
            merchant_names = [merchant, merchant.upper(), merchant.lower()]
            found_merchant = any(
                any(name in r.text for name in merchant_names)
                for r in results
            )
            assert found_merchant, f"Failed to detect merchant name for {merchant}"
        
        # Should detect currency in all receipts
        for merchant, results in all_results.items():
            found_currency = any("CURRENCY" in r.suggested_labels for r in results)
            assert found_currency, f"Failed to detect currency in {merchant} receipt"