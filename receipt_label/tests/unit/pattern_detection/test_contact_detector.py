"""Unit tests for contact information pattern detection."""

import pytest
from unittest.mock import Mock, patch

from receipt_label.pattern_detection.contact import ContactPatternDetector
from receipt_label.tests.markers import unit, fast, pattern_detection
from receipt_dynamo.entities import ReceiptWord


@unit
@fast
@pattern_detection
class TestContactPatternDetector:
    """Test contact pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Contact detector fixture.""" 
        return ContactPatternDetector()

    @pytest.mark.parametrize("text,expected_match,expected_label,min_confidence", [
        # Phone number formats
        ("(555) 123-4567", True, "PHONE_NUMBER", 0.95),
        ("555-123-4567", True, "PHONE_NUMBER", 0.90),
        ("555.123.4567", True, "PHONE_NUMBER", 0.85),
        ("5551234567", True, "PHONE_NUMBER", 0.80),
        ("1-555-123-4567", True, "PHONE_NUMBER", 0.95),
        ("+1 555 123 4567", True, "PHONE_NUMBER", 0.90),
        ("+1 (555) 123-4567", True, "PHONE_NUMBER", 0.95),
        ("555 123 4567", True, "PHONE_NUMBER", 0.85),
        
        # International formats
        ("+44 20 7946 0958", True, "PHONE_NUMBER", 0.90),  # UK
        ("+33 1 42 86 83 26", True, "PHONE_NUMBER", 0.85), # France  
        ("+49 30 12345678", True, "PHONE_NUMBER", 0.85),   # Germany
        
        # Email addresses
        ("test@example.com", True, "EMAIL", 0.95),
        ("user.name@company.org", True, "EMAIL", 0.90),
        ("support@walmart.com", True, "EMAIL", 0.95),
        ("info@mcdonalds.net", True, "EMAIL", 0.90),
        ("customer_service@target.co.uk", True, "EMAIL", 0.85),
        
        # Website URLs
        ("www.walmart.com", True, "WEBSITE", 0.90),
        ("https://www.target.com", True, "WEBSITE", 0.95),
        ("http://mcdonalds.com", True, "WEBSITE", 0.90),
        ("target.com", True, "WEBSITE", 0.80),
        ("www.company.co.uk", True, "WEBSITE", 0.85),
        
        # Invalid formats that should NOT match
        ("123-45", False, None, 0.0),          # Too short
        ("555-CALL", False, None, 0.0),        # Letters in phone
        ("not-an-email", False, None, 0.0),    # No @ symbol
        ("@domain.com", False, None, 0.0),     # Missing user part
        ("user@", False, None, 0.0),           # Missing domain
        ("http://", False, None, 0.0),         # Incomplete URL
        ("", False, None, 0.0),                # Empty
        ("random text", False, None, 0.0),     # Not contact info
    ])
    def test_contact_pattern_detection(self, detector, text, expected_match, expected_label, min_confidence):
        """Test contact pattern detection with various formats."""
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

    def test_phone_number_normalization(self, detector):
        """Test phone number normalization and equivalence detection."""
        # These should all be recognized as the same number
        equivalent_formats = [
            "(555) 123-4567",
            "555-123-4567", 
            "555.123.4567",
            "5551234567",
            "555 123 4567",
            "+1 555 123 4567",
            "1-555-123-4567"
        ]
        
        normalized_results = []
        
        for text in equivalent_formats:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect: {text}"
            
            result = results[0]
            assert "PHONE_NUMBER" in result.suggested_labels
            
            # Should normalize to same format internally (if detector supports it)
            if hasattr(result, 'normalized_value'):
                normalized_results.append(result.normalized_value)
        
        # If normalization is implemented, all should be equivalent
        if normalized_results:
            assert len(set(normalized_results)) == 1, "Phone numbers should normalize to same value"

    def test_email_validation(self, detector):
        """Test email address validation edge cases."""
        valid_emails = [
            "simple@example.com",
            "user.name@domain.org",
            "user+tag@company.net", 
            "user_underscore@test.co.uk",
            "123@numbers.com",
            "a@b.co",  # Minimal valid email
        ]
        
        invalid_emails = [
            "user@domain",      # Missing TLD
            "user@@domain.com", # Double @
            "user@.com",        # Missing domain name
            ".user@domain.com", # Starting with dot
            "user.@domain.com", # Ending with dot
            "user name@domain.com", # Space in user part
        ]
        
        # Valid emails should be detected
        for email in valid_emails:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=email, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Should detect valid email: {email}"
            assert results[0].confidence >= 0.8, f"Low confidence for valid email: {email}"
        
        # Invalid emails should NOT be detected (or have very low confidence)
        for email in invalid_emails:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=email, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            if results:
                # If detected, should have very low confidence
                assert results[0].confidence < 0.5, f"Should have low confidence for invalid email: {email}"
            # Better yet, should not detect at all

    def test_url_scheme_detection(self, detector):
        """Test URL detection with various schemes."""
        url_cases = [
            # HTTP/HTTPS
            ("https://www.example.com", "WEBSITE", 0.95),
            ("http://example.com", "WEBSITE", 0.90),
            ("https://subdomain.example.org", "WEBSITE", 0.90),
            
            # No scheme
            ("www.example.com", "WEBSITE", 0.85),
            ("example.com", "WEBSITE", 0.80),
            ("subdomain.example.co.uk", "WEBSITE", 0.80),
            
            # With paths
            ("https://example.com/path", "WEBSITE", 0.90),
            ("www.example.com/contact", "WEBSITE", 0.85),
            
            # Invalid URLs
            ("http://", "WEBSITE", 0.0),        # No domain
            ("https://.", "WEBSITE", 0.0),      # Invalid domain
            ("www.", "WEBSITE", 0.0),           # Incomplete
        ]
        
        for text, expected_label, min_confidence in url_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if min_confidence > 0:
                assert len(results) > 0, f"Should detect URL: {text}"
                result = results[0]
                assert result.confidence >= min_confidence, f"Low confidence for {text}: {result.confidence}"
                assert expected_label in result.suggested_labels
            else:
                # Invalid URLs should not be detected
                assert len(results) == 0 or results[0].confidence < 0.5, f"Should not detect invalid URL: {text}"

    def test_contact_context_analysis(self, detector):
        """Test context-aware contact information labeling."""
        # Test different contexts for the same phone number
        context_cases = [
            # Header area - likely store phone
            (0.1, "(555) 123-4567", ["STORE_PHONE", "PHONE_NUMBER"]),
            
            # Footer area - could be customer service
            (0.9, "(555) 123-4567", ["CUSTOMER_SERVICE_PHONE", "PHONE_NUMBER"]),
            
            # Middle area - generic phone
            (0.5, "(555) 123-4567", ["PHONE_NUMBER"]),
        ]
        
        for position_percentile, text, expected_labels in context_cases:
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

    def test_international_phone_formats(self, detector):
        """Test detection of international phone number formats."""
        international_cases = [
            # Country code formats
            ("+1 555 123 4567", 0.90),      # US/Canada
            ("+44 20 7946 0958", 0.85),     # UK
            ("+33 1 42 86 83 26", 0.80),    # France
            ("+49 30 12345678", 0.80),      # Germany
            ("+81 3 1234 5678", 0.80),      # Japan
            ("+86 10 1234 5678", 0.80),     # China
            
            # Different formatting styles
            ("+1-555-123-4567", 0.90),
            ("+1.555.123.4567", 0.85),
            ("+1 (555) 123-4567", 0.95),
            
            # Invalid international formats
            ("+999 123 456 789", 0.0),      # Invalid country code
            ("+", 0.0),                     # Just plus sign
            ("+1", 0.0),                    # Just country code
        ]
        
        for text, min_confidence in international_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if min_confidence > 0:
                assert len(results) > 0, f"Should detect international format: {text}"
                assert results[0].confidence >= min_confidence
            else:
                assert len(results) == 0, f"Should not detect invalid format: {text}"

    def test_business_contact_patterns(self, detector):
        """Test detection of business-specific contact patterns."""
        business_cases = [
            # Customer service patterns
            ("1-800-WALMART", True, "CUSTOMER_SERVICE", 0.80),
            ("1-800-123-4567", True, "TOLL_FREE", 0.85),
            ("800-555-1234", True, "TOLL_FREE", 0.85),
            
            # Support emails
            ("support@walmart.com", True, "SUPPORT_EMAIL", 0.90),
            ("help@company.org", True, "SUPPORT_EMAIL", 0.85),
            ("info@business.net", True, "INFO_EMAIL", 0.80),
            ("customerservice@store.com", True, "SUPPORT_EMAIL", 0.90),
            
            # Business websites
            ("www.walmart.com", True, "BUSINESS_WEBSITE", 0.90),
            ("corporate.company.com", True, "BUSINESS_WEBSITE", 0.85),
        ]
        
        for text, should_match, expected_label, min_confidence in business_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect business pattern: {text}"
                result = results[0]
                assert result.confidence >= min_confidence
                # Should have generic label at minimum
                contact_labels = ["PHONE_NUMBER", "EMAIL", "WEBSITE", expected_label]
                found_contact = any(label in result.suggested_labels for label in contact_labels)
                assert found_contact, f"Should have contact label for: {text}"

    def test_contact_confidence_scoring(self, detector):
        """Test confidence scoring for contact information quality."""
        # High confidence contacts (complete, well-formatted)
        high_confidence_cases = [
            ("(555) 123-4567", 0.95),       # Perfect phone format
            ("user@company.com", 0.95),     # Clean email
            ("https://www.site.com", 0.95), # Complete URL
        ]
        
        # Medium confidence contacts (less standard formatting)
        medium_confidence_cases = [
            ("5551234567", 0.80),           # No formatting
            ("user+tag@site.org", 0.85),   # Email with tag
            ("www.site.com", 0.85),         # URL without scheme
        ]
        
        # Lower confidence (unusual but valid)
        lower_confidence_cases = [
            ("555 123 4567", 0.75),         # Space-separated phone
            ("a@b.co", 0.70),               # Minimal email
            ("site.com", 0.70),             # Domain only
        ]
        
        all_cases = [
            (high_confidence_cases, "high"),
            (medium_confidence_cases, "medium"), 
            (lower_confidence_cases, "lower")
        ]
        
        for cases, confidence_level in all_cases:
            for text, expected_min in cases:
                word = ReceiptWord(
                    image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                    text=text, x1=100, y1=100, x2=200, y2=120
                )
                
                results = detector.detect_patterns([word])
                assert len(results) > 0, f"Failed to detect {confidence_level} confidence case: {text}"
                
                confidence = results[0].confidence
                assert confidence >= expected_min, f"{confidence_level} confidence case {text}: got {confidence}, expected ≥{expected_min}"

    def test_multi_contact_batch_processing(self, detector, performance_timer):
        """Test efficient processing of multiple contact patterns."""
        # Create batch with mixed contact types
        contact_batch = [
            "(555) 123-4567",     # Phone
            "test@example.com",   # Email  
            "www.company.com",    # Website
            "1-800-HELP",         # Toll-free
            "support@store.org",  # Support email
            "+1 555 987 6543",    # International
            "not-contact-info",   # Non-match
            "random text",        # Non-match
        ] * 10  # 80 total items
        
        batch_words = []
        for i, text in enumerate(contact_batch):
            batch_words.append(ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                text=text, x1=100, y1=100 + i * 10, x2=200, y2=120 + i * 10
            ))
        
        performance_timer.start()
        results = detector.detect_patterns(batch_words)
        elapsed = performance_timer.stop()
        
        # Should process efficiently
        assert elapsed < 2.0, f"Batch processing took {elapsed:.2f}s, should be <2s"
        
        # Should detect most contact info (60 contact items, 20 non-contact)
        assert len(results) >= 50, f"Should detect most contacts, got {len(results)}"
        assert len(results) <= 70, f"Should not over-detect, got {len(results)}"

    def test_edge_case_handling(self, detector):
        """Test handling of edge cases and malformed input."""
        edge_cases = [
            # Empty or minimal input
            ("", False),
            (" ", False),
            ("a", False),
            
            # Partial matches that should not qualify
            ("@", False),
            ("www.", False), 
            ("555-", False),
            ("+1", False),
            
            # Almost valid but not quite
            ("user@domain", False),      # Missing TLD
            ("555-123", False),          # Too short
            ("http://", False),          # Incomplete URL
            
            # Special characters
            ("user@domain.com!", False), # Extra punctuation
            ("(555) 123-4567.", False),  # Trailing period
        ]
        
        for text, should_match in edge_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect edge case: '{text}'"
            else:
                # Should either not detect or have very low confidence
                if results:
                    assert results[0].confidence < 0.5, f"Should have low confidence for edge case: '{text}'"

    def test_contact_type_consistency(self, detector):
        """Test that contact types are consistently identified."""
        type_consistency_cases = [
            ("(555) 123-4567", "CONTACT", ["PHONE_NUMBER"]),
            ("user@domain.com", "CONTACT", ["EMAIL"]),
            ("www.site.com", "CONTACT", ["WEBSITE"]),
        ]
        
        for text, expected_pattern_type, expected_labels in type_consistency_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect: {text}"
            
            result = results[0]
            
            # Should have correct pattern type
            assert result.pattern_type == expected_pattern_type or any(label in result.suggested_labels for label in expected_labels)
            
            # Should have valid structure
            assert isinstance(result.suggested_labels, list)
            assert len(result.suggested_labels) > 0
            assert 0 <= result.confidence <= 1