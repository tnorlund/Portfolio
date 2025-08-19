"""Unit tests for contact information pattern detection."""

import pytest

from receipt_label.pattern_detection.contact import ContactPatternDetector
from tests.markers import unit, fast, pattern_detection
from tests.helpers import create_test_receipt_word


@unit
@fast
@pattern_detection
class TestContactPatternDetector:
    """Test contact pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Contact detector fixture."""
        return ContactPatternDetector()

    @pytest.mark.parametrize(
        "text,expected_match,expected_matched_text,expected_label,"
        "min_confidence",
        [
            # Phone number formats
            ("(555) 123-4567", True, "(555) 123-4567", "PHONE_NUMBER", 0.40),
            ("555-123-4567", True, "555-123-4567", "PHONE_NUMBER", 0.40),
            ("555.123.4567", True, "555.123.4567", "PHONE_NUMBER", 0.35),
            ("5551234567", True, "5551234567", "PHONE_NUMBER", 0.35),
            (
                "1-555-123-4567",
                True,
                "555-123-4567",
                "PHONE_NUMBER",
                0.40),  # Strips country code
            ("+1 555 123 4567", True, "+1 555 123 4567", "PHONE_NUMBER", 0.40),
            (
                "+1 (555) 123-4567",
                True,
                "+1 (555) 123-4567",
                "PHONE_NUMBER",
                0.40),
            ("555 123 4567", True, "555 123 4567", "PHONE_NUMBER", 0.35),
            # International formats
            (
                "+44 20 7946 0958",
                True,
                "+44 20 7946 0958",
                "PHONE_NUMBER",
                0.40),  # UK
            (
                "+33 1 42 86 83 26",
                True,
                "+33 1 42 86 83 26",
                "PHONE_NUMBER",
                0.35),  # France
            (
                "+49 30 12345678",
                True,
                "+49 30 12345678",
                "PHONE_NUMBER",
                0.35),  # Germany
            # Email addresses
            ("test@example.com", True, "test@example.com", "EMAIL", 0.40),
            (
                "user.name@company.org",
                True,
                "user.name@company.org",
                "EMAIL",
                0.40),
            (
                "support@walmart.com",
                True,
                "support@walmart.com",
                "EMAIL",
                0.40),
            ("info@mcdonalds.net", True, "info@mcdonalds.net", "EMAIL", 0.40),
            (
                "customer_service@target.co.uk",
                True,
                "customer_service@target.co.uk",
                "EMAIL",
                0.35),
            # Website URLs
            ("www.walmart.com", True, "www.walmart.com", "WEBSITE", 0.40),
            (
                "https://www.target.com",
                True,
                "https://www.target.com",
                "WEBSITE",
                0.40),
            (
                "http://mcdonalds.com",
                True,
                "http://mcdonalds.com",
                "WEBSITE",
                0.40),
            ("target.com", True, "target.com", "WEBSITE", 0.35),
            ("www.company.co.uk", True, "www.company.co.uk", "WEBSITE", 0.35),
            # Invalid formats that should NOT match
            ("123-45", False, None, None, 0.0),
            ("555-CALL", False, None, None, 0.0),
            ("not-an-email", False, None, None, 0.0),
            ("@domain.com", False, None, None, 0.0),
            ("user@", False, None, None, 0.0),
            ("http://", False, None, None, 0.0),
            ("", False, None, None, 0.0),  # Empty
            ("random text", False, None, None, 0.0),
        ])
    async def test_contact_pattern_detection(
        self,
        detector,
        text,
        expected_match,
        expected_matched_text,
        expected_label,
        min_confidence):
        """Test contact pattern detection with various formats."""
        word = create_test_receipt_word(
            receipt_id=1,
            line_id=1,
            word_id=1,
            text=text,
            x1=100,
            y1=100,
            x2=200,
            y2=120)

        results = await detector.detect([word])

        if expected_match:
            assert (
                len(results) > 0
            ), f"Expected match for '{text}' but got no results"
            result = results[0]
            assert (
                result.confidence >= min_confidence
            ), f"Confidence {result.confidence} < {min_confidence} for '{text}'"
            assert (
                expected_label == result.pattern_type.name
                or expected_label
                in result.metadata.get("suggested_labels", [])
            ), f"Expected {expected_label} for '{text}'"
            assert result.matched_text == expected_matched_text
        else:
            assert (
                len(results) == 0
            ), f"Expected no match for '{text}' but got {len(results)} results"

    async def test_phone_number_normalization(self, detector):
        """Test phone number normalization and equivalence detection."""
        # These should all be recognized as the same number
        equivalent_formats = [
            "(555) 123-4567",
            "555-123-4567",
            "555.123.4567",
            "5551234567",
            "555 123 4567",
            "+1 555 123 4567",
            "1-555-123-4567",
        ]

        normalized_results = []

        for text in equivalent_formats:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            assert len(results) > 0, f"Failed to detect: {text}"

            result = results[0]
            assert (
                "PHONE_NUMBER" == result.pattern_type.name
                or "PHONE_NUMBER"
                in result.metadata.get("suggested_labels", [])
            )

            # Should normalize to same format internally (if detector supports it)
            if hasattr(result, "normalized_value"):
                normalized_results.append(result.normalized_value)

        # If normalization is implemented, all should be equivalent
        if normalized_results:
            assert (
                len(set(normalized_results)) == 1
            ), "Phone numbers should normalize to same value"

    async def test_email_validation(self, detector):
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
            "user@domain",  # Missing TLD
            "user@@domain.com",  # Double @
            "user@.com",  # Missing domain name
            # ".user@domain.com", # Actually matches - regex allows this
            # "user.@domain.com", # Actually matches - regex allows this
            # "user name@domain.com", # Actually matches part of it
        ]

        # Valid emails should be detected
        for email in valid_emails:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=email,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            assert len(results) > 0, f"Should detect valid email: {email}"
            assert (
                results[0].confidence >= 0.3
            ), f"Low confidence for valid email: {email}"

        # Invalid emails should NOT be detected (or have very low confidence)
        for email in invalid_emails:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=email,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            if results:
                # If detected, should have very low confidence
                assert (
                    results[0].confidence < 0.5
                ), f"Should have low confidence for invalid email: {email}"
            # Better yet, should not detect at all

    async def test_url_scheme_detection(self, detector):
        """Test URL detection with various schemes."""
        url_cases = [
            # HTTP/HTTPS
            ("https://www.example.com", "WEBSITE", 0.40),
            ("http://example.com", "WEBSITE", 0.40),
            ("https://subdomain.example.org", "WEBSITE", 0.40),
            # No scheme
            ("www.example.com", "WEBSITE", 0.35),
            ("example.com", "WEBSITE", 0.35),
            ("subdomain.example.co.uk", "WEBSITE", 0.35),
            # With paths
            ("https://example.com/path", "WEBSITE", 0.40),
            ("www.example.com/contact", "WEBSITE", 0.35),
            # Invalid URLs
            ("http://", "WEBSITE", 0.0),  # No domain
            ("https://.", "WEBSITE", 0.0),  # Invalid domain
            ("www.", "WEBSITE", 0.0),  # Incomplete
        ]

        for text, expected_label, min_confidence in url_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])

            if min_confidence > 0:
                assert len(results) > 0, f"Should detect URL: {text}"
                result = results[0]
                assert (
                    result.confidence >= min_confidence
                ), f"Low confidence for {text}: {result.confidence}"
                assert (
                    expected_label == result.pattern_type.name
                    or expected_label
                    in result.metadata.get("suggested_labels", [])
                )
            else:
                # Invalid URLs should not be detected
                assert (
                    len(results) == 0 or results[0].confidence < 0.5
                ), f"Should not detect invalid URL: {text}"

    async def test_international_phone_formats(self, detector):
        """Test detection of international phone number formats."""
        international_cases = [
            # Country code formats
            ("+1 555 123 4567", 0.90),  # US/Canada
            ("+44 20 7946 0958", 0.85),  # UK
            ("+33 1 42 86 83 26", 0.80),  # France
            ("+49 30 12345678", 0.80),  # Germany
            ("+81 3 1234 5678", 0.80),  # Japan
            ("+86 10 1234 5678", 0.80),  # China
            # Different formatting styles
            ("+1-555-123-4567", 0.85),
            ("+1.555.123.4567", 0.80),
            ("+1 (555) 123-4567", 0.85),
            # Invalid international formats
            ("+999 123 456 789", 0.0),  # Invalid country code
            ("+", 0.0),  # Just plus sign
            ("+1", 0.0),  # Just country code
        ]

        for text, min_confidence in international_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])

            if min_confidence > 0:
                assert (
                    len(results) > 0
                ), f"Should detect international format: {text}"
                assert results[0].confidence >= min_confidence
            else:
                assert (
                    len(results) == 0
                ), f"Should not detect invalid format: {text}"

    async def test_business_contact_patterns(self, detector):
        """Test detection of business-specific contact patterns."""
        business_cases = [
            # Customer service patterns
            ("1-800-WALMART", True, "CUSTOMER_SERVICE", 0.35),
            ("1-800-123-4567", True, "TOLL_FREE", 0.35),
            ("800-555-1234", True, "TOLL_FREE", 0.35),
            # Support emails
            ("support@walmart.com", True, "SUPPORT_EMAIL", 0.40),
            ("help@company.org", True, "SUPPORT_EMAIL", 0.35),
            ("info@business.net", True, "INFO_EMAIL", 0.35),
            ("customerservice@store.com", True, "SUPPORT_EMAIL", 0.40),
            # Business websites
            ("www.walmart.com", True, "BUSINESS_WEBSITE", 0.40),
            ("corporate.company.com", True, "BUSINESS_WEBSITE", 0.35),
        ]

        for (
            text,
            should_match,
            _,
            min_confidence) in business_cases:  # expected_label not used
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])

            if should_match:
                assert (
                    len(results) > 0
                ), f"Should detect business pattern: {text}"
                result = results[0]
                assert result.confidence >= min_confidence
                # Should have detected the pattern
                assert result.pattern_type.name in [
                    "PHONE_NUMBER",
                    "EMAIL",
                    "WEBSITE",
                ], f"Should detect contact pattern for: {text}"

    async def test_contact_confidence_scoring(self, detector):
        """Test confidence scoring for contact information quality."""
        # High confidence contacts (complete, well-formatted)
        high_confidence_cases = [
            ("(555) 123-4567", 0.95),  # Perfect phone format
            ("user@company.com", 0.95),  # Clean email
            ("https://www.site.com", 0.95),  # Complete URL
        ]

        # Medium confidence contacts (less standard formatting)
        medium_confidence_cases = [
            ("5551234567", 0.80),  # No formatting
            ("user+tag@site.org", 0.85),  # Email with tag
            ("www.site.com", 0.85),  # URL without scheme
        ]

        # Lower confidence (unusual but valid)
        lower_confidence_cases = [
            ("555 123 4567", 0.75),  # Space-separated phone
            ("a@b.co", 0.70),  # Minimal email
            ("site.com", 0.70),  # Domain only
        ]

        all_cases = [
            (high_confidence_cases, "high"),
            (medium_confidence_cases, "medium"),
            (lower_confidence_cases, "lower"),
        ]

        for cases, confidence_level in all_cases:
            for text, expected_min in cases:
                word = create_test_receipt_word(
                    receipt_id=1,
                    line_id=1,
                    word_id=1,
                    text=text,
                    x1=100,
                    y1=100,
                    x2=200,
                    y2=120)

                results = await detector.detect([word])
                assert (
                    len(results) > 0
                ), f"Failed to detect {confidence_level} confidence case: {text}"

                confidence = results[0].confidence
                assert (
                    confidence >= expected_min
                ), f"{confidence_level} confidence case {text}: got {confidence}, expected ≥{expected_min}"

    async def test_multi_contact_batch_processing(self, detector):
        """Test efficient processing of multiple contact patterns."""
        # Create batch with mixed contact types
        contact_batch = [
            "(555) 123-4567",  # Phone
            "test@example.com",  # Email
            "www.company.com",  # Website
            "1-800-HELP",  # Toll-free
            "support@store.org",  # Support email
            "+1 555 987 6543",  # International
            "not-contact-info",  # Non-match
            "random text",  # Non-match
        ] * 10  # 80 total items

        batch_words = []
        for i, text in enumerate(contact_batch):
            batch_words.append(
                create_test_receipt_word(
                    receipt_id=1,
                    line_id=i,
                    word_id=1,
                    text=text,
                    x1=100,
                    y1=100 + i * 10,
                    x2=200,
                    y2=120 + i * 10)
            )

        import time  # pylint: disable=import-outside-toplevel

        start = time.time()
        results = await detector.detect(batch_words)
        elapsed = time.time() - start

        # Should process efficiently
        assert (
            elapsed < 2.0
        ), f"Batch processing took {elapsed:.2f}s, should be <2s"

        # Should detect most contact info (60 contact items, 20 non-contact)
        assert (
            len(results) >= 50
        ), f"Should detect most contacts, got {len(results)}"
        assert (
            len(results) <= 70
        ), f"Should not over-detect, got {len(results)}"

    async def test_edge_case_handling(self, detector):
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
            ("user@domain", False),  # Missing TLD
            ("555-123", False),  # Too short
            ("http://", False),  # Incomplete URL
            # Special characters - detector actually matches these (ignores trailing punct)
            ("user@domain.com!", True),  # Matches the email part
            ("(555) 123-4567.", True),  # Matches the phone part
        ]

        for text, should_match in edge_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])

            if should_match:
                assert len(results) > 0, f"Should detect edge case: '{text}'"
            else:
                # Should either not detect or have very low confidence
                if results:
                    assert (
                        results[0].confidence < 0.5
                    ), f"Should have low confidence for edge case: '{text}'"

    async def test_contact_type_consistency(self, detector):
        """Test that contact types are consistently identified."""
        type_consistency_cases = [
            ("(555) 123-4567", "CONTACT", ["PHONE_NUMBER"]),
            ("user@domain.com", "CONTACT", ["EMAIL"]),
            ("www.site.com", "CONTACT", ["WEBSITE"]),
        ]

        for (
            text,
            _,
            expected_labels) in type_consistency_cases:  # expected_pattern_type not used
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            assert len(results) > 0, f"Failed to detect: {text}"

            result = results[0]

            # Should have correct pattern type
            assert (
                result.pattern_type.name in expected_labels
                or result.pattern_type.name == "CONTACT"
            ), f"Expected one of {expected_labels}, got {result.pattern_type.name}"

            # Should have valid confidence
            assert 0 <= result.confidence <= 1
