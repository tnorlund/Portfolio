"""Unit tests for datetime pattern detection."""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from receipt_label.pattern_detection.datetime_patterns import DateTimePatternDetector
from receipt_label.tests.markers import unit, fast, pattern_detection
from receipt_dynamo.entities import ReceiptWord


@unit
@fast
@pattern_detection
class TestDateTimePatternDetector:
    """Test datetime pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """DateTime detector fixture."""
        return DateTimePatternDetector()

    @pytest.mark.parametrize("text,expected_match,expected_label,min_confidence", [
        # Date formats
        ("12/25/2023", True, "DATE", 0.90),
        ("12-25-2023", True, "DATE", 0.85),
        ("2023-12-25", True, "DATE", 0.95),  # ISO format - highest confidence
        ("Dec 25, 2023", True, "DATE", 0.80),
        ("December 25, 2023", True, "DATE", 0.75),
        ("25/12/2023", True, "DATE", 0.80),  # European format
        
        # Time formats
        ("12:30 PM", True, "TIME", 0.90),
        ("12:30:45", True, "TIME", 0.85),
        ("23:59:59", True, "TIME", 0.95),  # 24-hour format
        ("12:30", True, "TIME", 0.80),
        ("3:45 am", True, "TIME", 0.85),
        ("3:45 AM", True, "TIME", 0.85),
        ("11:59 PM", True, "TIME", 0.90),
        
        # Combined datetime
        ("12/25/2023 2:30 PM", True, "DATETIME", 0.95),
        ("2023-12-25T14:30:00", True, "DATETIME", 0.98),  # ISO datetime
        
        # Edge cases that should match
        ("1/1/2024", True, "DATE", 0.85),
        ("12/31/1999", True, "DATE", 0.85),
        ("00:00:00", True, "TIME", 0.80),
        ("23:59", True, "TIME", 0.80),
        
        # Invalid formats that should NOT match
        ("13/32/2023", False, None, 0.0),  # Invalid day
        ("25:00", False, None, 0.0),       # Invalid hour
        ("12:60", False, None, 0.0),       # Invalid minute
        ("12:30:60", False, None, 0.0),    # Invalid second
        ("not-a-date", False, None, 0.0),
        ("2023", False, None, 0.0),        # Year only
        ("12", False, None, 0.0),          # Number only
        ("", False, None, 0.0),            # Empty
        ("abc/def/ghi", False, None, 0.0), # Non-numeric
    ])
    def test_datetime_pattern_detection(self, detector, text, expected_match, expected_label, min_confidence):
        """Test datetime pattern detection with various formats."""
        word = ReceiptWord(
            image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
            text=text, x1=100, y1=100, x2=200, y2=120
        )
        
        results = detector.detect_patterns([word])
        
        if expected_match:
            assert len(results) > 0, f"Expected match for '{text}' but got no results"
            result = results[0]
            assert result.confidence >= min_confidence, f"Confidence {result.confidence} < {min_confidence} for '{text}'"
            assert expected_label in result.suggested_labels, f"Expected {expected_label} in {result.suggested_labels} for '{text}'"
            assert result.text == text
        else:
            assert len(results) == 0, f"Expected no match for '{text}' but got {len(results)} results"

    def test_timezone_handling(self, detector):
        """Test detection of timezone-aware timestamps."""
        timezone_cases = [
            ("12:30:00Z", "TIME", 0.90),           # UTC
            ("12:30:00+00:00", "TIME", 0.90),      # UTC offset
            ("12:30:00-05:00", "TIME", 0.90),      # EST
            ("12:30:00+08:00", "TIME", 0.90),      # Asian timezone
            ("12:30 PM EST", "TIME", 0.85),        # Named timezone
            ("12:30 PM UTC", "TIME", 0.85),        # Named timezone
            ("3:45 am PST", "TIME", 0.85),         # Named timezone
        ]
        
        for text, expected_label, min_confidence in timezone_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect timezone format: {text}"
            
            result = results[0]
            assert result.confidence >= min_confidence
            assert expected_label in result.suggested_labels

    def test_receipt_context_analysis(self, detector):
        """Test context-aware labeling based on receipt position."""
        # Test words at different receipt positions
        test_cases = [
            # Header area - transaction date/time
            (0.1, "12/25/2023", ["TRANSACTION_DATE"]),
            (0.15, "2:30 PM", ["TRANSACTION_TIME"]),
            
            # Footer area - also transaction date/time  
            (0.9, "12/25/2023", ["TRANSACTION_DATE"]),
            (0.85, "2:30 PM", ["TRANSACTION_TIME"]),
            
            # Middle area - generic date/time
            (0.5, "12/25/2023", ["DATE"]),
            (0.5, "2:30 PM", ["TIME"]),
        ]
        
        for position_percentile, text, expected_labels in test_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=int(position_percentile * 1000), 
                x2=200, y2=int(position_percentile * 1000) + 20
            )
            
            with patch.object(detector, '_calculate_position_percentile', return_value=position_percentile):
                results = detector.detect_patterns([word])
            
            assert len(results) > 0
            result = results[0]
            
            # Should include at least one expected label
            found_expected = any(label in result.suggested_labels for label in expected_labels)
            assert found_expected, f"Expected one of {expected_labels}, got {result.suggested_labels}"

    def test_date_format_confidence_ranking(self, detector):
        """Test that different date formats get appropriate confidence scores."""
        format_confidence_cases = [
            ("2023-12-25", 0.95),        # ISO format - highest
            ("12/25/2023", 0.90),        # US format - high
            ("25/12/2023", 0.85),        # European - medium-high
            ("Dec 25, 2023", 0.80),      # Month name - medium
            ("December 25, 2023", 0.75), # Full month - lower
            ("12-25-23", 0.70),          # 2-digit year - lower
        ]
        
        confidence_results = []
        
        for text, expected_min in format_confidence_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect: {text}"
            
            confidence = results[0].confidence
            confidence_results.append((text, confidence))
            
            assert confidence >= expected_min, f"{text}: confidence {confidence} < {expected_min}"
        
        # Verify ISO format has highest confidence
        iso_confidence = next(conf for text, conf in confidence_results if text == "2023-12-25")
        other_confidences = [conf for text, conf in confidence_results if text != "2023-12-25"]
        
        assert iso_confidence >= max(other_confidences), "ISO format should have highest confidence"

    def test_time_format_validation(self, detector):
        """Test validation of time format edge cases."""
        valid_edge_cases = [
            "00:00:00",    # Midnight
            "23:59:59",    # Last second of day
            "12:00 AM",    # Midnight 12-hour
            "12:00 PM",    # Noon 12-hour
            "1:00",        # Single digit hour
            "01:00",       # Zero-padded hour
        ]
        
        invalid_cases = [
            "24:00",       # Invalid 24-hour
            "13:00 PM",    # Invalid 12-hour with PM
            "00:00 AM",    # Invalid midnight representation
            "12:5 PM",     # Invalid single digit minute
            "12:",         # Incomplete time
            ":30",         # Missing hour
            "12:30:",      # Incomplete seconds
        ]
        
        # Valid cases should be detected
        for text in valid_edge_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Should detect valid time format: {text}"
            assert results[0].confidence >= 0.7, f"Low confidence for valid time: {text}"
        
        # Invalid cases should NOT be detected
        for text in invalid_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) == 0, f"Should not detect invalid time format: {text}"

    def test_batch_processing_efficiency(self, detector, performance_timer):
        """Test efficient batch processing of datetime patterns."""
        # Create batch of mixed datetime content
        batch_words = []
        datetime_texts = [
            "12/25/2023", "2:30 PM", "2023-12-25T14:30:00",
            "Dec 25, 2023", "11:59 PM", "1/1/2024",
            "not-a-date", "random-text", "12345",  # Non-datetime content
        ]
        
        for i, text in enumerate(datetime_texts * 10):  # 90 words total
            batch_words.append(ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                text=text, x1=100, y1=100 + i * 10, x2=200, y2=120 + i * 10
            ))
        
        performance_timer.start()
        results = detector.detect_patterns(batch_words)
        elapsed = performance_timer.stop()
        
        # Should process efficiently
        assert elapsed < 2.0, f"Batch processing took {elapsed:.2f}s, should be <2s"
        
        # Should detect appropriate number of patterns (60 datetime, 30 non-datetime)
        assert len(results) >= 50, f"Should detect most datetime patterns, got {len(results)}"
        assert len(results) <= 70, f"Should not over-detect, got {len(results)}"

    def test_year_range_validation(self, detector):
        """Test validation of reasonable year ranges."""
        year_test_cases = [
            # Valid years
            ("1/1/2000", True, 0.85),
            ("12/31/2030", True, 0.85),
            ("6/15/2023", True, 0.90),
            ("2023-06-15", True, 0.95),
            
            # Edge case years (might be valid but lower confidence)
            ("1/1/1990", True, 0.70),   # Older receipt
            ("1/1/2050", True, 0.70),   # Future date
            
            # Invalid years (should not match)
            ("1/1/1800", False, 0.0),   # Too old
            ("1/1/2100", False, 0.0),   # Too far in future
            ("1/1/99", False, 0.0),     # Ambiguous 2-digit year
        ]
        
        for text, should_match, min_confidence in year_test_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect reasonable year: {text}"
                assert results[0].confidence >= min_confidence
            else:
                assert len(results) == 0, f"Should not detect unreasonable year: {text}"

    def test_multilingual_month_names(self, detector):
        """Test detection of month names in different languages/formats."""
        # Currently focusing on English, but test common abbreviations
        month_cases = [
            # English full names
            ("January 15, 2023", True, 0.75),
            ("February 28, 2023", True, 0.75),
            ("December 25, 2023", True, 0.75),
            
            # English abbreviations  
            ("Jan 15, 2023", True, 0.80),
            ("Feb 28, 2023", True, 0.80),
            ("Dec 25, 2023", True, 0.80),
            
            # Mixed case
            ("jan 15, 2023", True, 0.75),
            ("DEC 25, 2023", True, 0.75),
            
            # Invalid month names
            ("Janruary 15, 2023", False, 0.0),
            ("13th 15, 2023", False, 0.0),
        ]
        
        for text, should_match, min_confidence in month_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect month name format: {text}"
                assert results[0].confidence >= min_confidence
            else:
                assert len(results) == 0, f"Should not detect invalid month: {text}"

    def test_error_handling_malformed_input(self, detector):
        """Test handling of malformed or edge case inputs."""
        edge_case_inputs = [
            None,
            [],
            [None],
            ["not a ReceiptWord"],
            [ReceiptWord(image_id="", receipt_id=0, line_id=0, word_id=0,
                        text=None, x1=0, y1=0, x2=0, y2=0)],  # Null text
        ]
        
        for invalid_input in edge_case_inputs:
            try:
                if invalid_input is None:
                    with pytest.raises(TypeError):
                        detector.detect_patterns(invalid_input)
                else:
                    results = detector.detect_patterns(invalid_input)
                    assert isinstance(results, list), "Should return list for invalid input"
                    assert len(results) == 0, "Should return empty list for invalid input"
            except Exception as e:
                pytest.fail(f"Should handle invalid input gracefully: {invalid_input}, got {e}")

    def test_pattern_type_consistency(self, detector):
        """Test that pattern types are consistent and correctly assigned.""" 
        test_cases = [
            ("12/25/2023", "DATE"),
            ("2:30 PM", "TIME"),
            ("2023-12-25T14:30:00", "DATETIME"),
        ]
        
        for text, expected_type in test_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0
            
            result = results[0]
            assert result.pattern_type == expected_type or expected_type in result.suggested_labels
            assert isinstance(result.suggested_labels, list)
            assert len(result.suggested_labels) > 0