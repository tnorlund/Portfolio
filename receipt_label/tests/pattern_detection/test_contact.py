"""Tests for contact pattern detection."""

import pytest
from receipt_label.pattern_detection import (
    ContactPatternDetector,
    PatternType,
)

from receipt_dynamo.entities import ReceiptWord


class TestContactPatternDetector:
    """Test contact pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Create a contact pattern detector."""
        return ContactPatternDetector()

    @pytest.fixture
    def create_word(self):
        """Factory function to create test words."""

        def _create_word(text, word_id=1, y_pos=100):
            return ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=y_pos // 20,
                word_id=word_id,
                text=text,
                bounding_box={
                    "x": 0,
                    "y": y_pos,
                    "width": 50,
                    "height": 20,
                },
                top_left={"x": 0, "y": y_pos},
                top_right={"x": 50, "y": y_pos},
                bottom_left={"x": 0, "y": y_pos + 20},
                bottom_right={"x": 50, "y": y_pos + 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )

        return _create_word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_phone_number_formats(self, detector, create_word):
        """Test detection of various phone number formats."""
        test_cases = [
            # US/Canada formats
            ("(555) 123-4567", PatternType.PHONE_NUMBER, "555-123-4567"),
            ("555-123-4567", PatternType.PHONE_NUMBER, "555-123-4567"),
            ("555.123.4567", PatternType.PHONE_NUMBER, "555-123-4567"),
            ("5551234567", PatternType.PHONE_NUMBER, "555-123-4567"),
            ("1-800-FLOWERS", PatternType.PHONE_NUMBER, "1-800-FLOWERS"),
            # International formats
            ("+1-555-123-4567", PatternType.PHONE_NUMBER, "+1-555-123-4567"),
            ("+44 20 1234 5678", PatternType.PHONE_NUMBER, "+44 20 1234 5678"),
            ("+86-10-12345678", PatternType.PHONE_NUMBER, "+86-10-12345678"),
            # With labels
            ("Tel: 555-123-4567", PatternType.PHONE_NUMBER, "555-123-4567"),
            (
                "Phone: (555) 123-4567",
                PatternType.PHONE_NUMBER,
                "555-123-4567",
            ),
            ("Call 555-123-4567", PatternType.PHONE_NUMBER, "555-123-4567"),
        ]

        for i, (text, expected_type, expected_value) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect phone in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.metadata["normalized"] == expected_value
            assert match.confidence >= 0.8

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_email_detection(self, detector, create_word):
        """Test email address detection."""
        valid_emails = [
            "user@example.com",
            "john.doe@company.co.uk",
            "contact+info@business.org",
            "support123@mail-server.net",
            "admin@sub.domain.com",
        ]

        for i, email in enumerate(valid_emails):
            word = create_word(email, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect email: {email}"
            match = matches[0]
            assert match.pattern_type == PatternType.EMAIL
            assert match.extracted_value == email.lower()
            assert match.confidence >= 0.9

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_emails(self, detector, create_word):
        """Test that invalid emails are not detected."""
        invalid_emails = [
            "not.an.email",
            "@example.com",
            "user@",
            "user@@example.com",
            "user@.com",
            "user@example",
        ]

        for i, text in enumerate(invalid_emails):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])
            assert (
                len(matches) == 0
            ), f"Should not detect invalid email: {text}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_website_detection(self, detector, create_word):
        """Test website URL detection."""
        test_cases = [
            # With protocol
            ("http://example.com", "example.com"),
            ("https://www.example.com", "example.com"),
            ("https://shop.example.co.uk", "example.co.uk"),
            # Without protocol
            ("www.example.com", "example.com"),
            ("shop.example.com", "example.com"),
            # Short URLs
            ("bit.ly/abc123", "bit.ly"),
            ("tinyurl.com/xyz789", "tinyurl.com"),
            # With paths
            ("example.com/contact", "example.com"),
            ("www.example.com/store/item", "example.com"),
        ]

        for i, (url, expected_domain) in enumerate(test_cases):
            word = create_word(url, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect website: {url}"
            match = matches[0]
            assert match.pattern_type == PatternType.WEBSITE
            assert match.metadata["domain"] == expected_domain
            assert match.confidence >= 0.8

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_patterns_in_receipt(self, detector, create_word):
        """Test detection of multiple contact patterns in a receipt."""
        receipt_data = [
            ("ACME Store", 0, 0),
            ("123 Main Street", 1, 20),
            ("Phone: (555) 123-4567", 2, 40),
            ("Email: info@acmestore.com", 3, 60),
            ("Web: www.acmestore.com", 4, 80),
            ("Customer Service:", 5, 100),
            ("1-800-ACME-123", 6, 120),
            ("Visit us online!", 7, 140),
        ]

        words = [
            create_word(text, word_id=i, y_pos=y)
            for text, i, y in receipt_data
        ]

        matches = await detector.detect(words)

        # Should detect phone, email, website, and toll-free number
        assert len(matches) == 4

        # Check each pattern type is detected
        pattern_types = {m.pattern_type for m in matches}
        assert PatternType.PHONE_NUMBER in pattern_types
        assert PatternType.EMAIL in pattern_types
        assert PatternType.WEBSITE in pattern_types

        # Verify specific detections
        phone_matches = [
            m for m in matches if m.pattern_type == PatternType.PHONE_NUMBER
        ]
        assert len(phone_matches) == 2  # Regular and toll-free

        email_matches = [
            m for m in matches if m.pattern_type == PatternType.EMAIL
        ]
        assert len(email_matches) == 1
        assert email_matches[0].extracted_value == "info@acmestore.com"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_phone_edge_cases(self, detector, create_word):
        """Test edge cases for phone number detection."""
        test_cases = [
            # Too short
            ("555-1234", False),  # Local number without area code
            # Too long
            ("555-123-4567-890", False),
            # Invalid patterns
            ("555-ABC-DEFG", False),
            ("555-12-34567", False),
            # Valid edge cases
            ("555-555-5555", True),  # All same digits
            ("000-000-0000", True),  # All zeros
        ]

        for i, (text, should_match) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            if should_match:
                assert len(matches) == 1, f"Should detect: {text}"
            else:
                assert len(matches) == 0, f"Should not detect: {text}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_international_phone_formats(self, detector, create_word):
        """Test international phone number formats."""
        test_cases = [
            # UK
            ("+44 20 7123 4567", True),
            ("+44 (0)20 7123 4567", True),
            # Germany
            ("+49 30 12345678", True),
            ("+49-30-12345678", True),
            # Japan
            ("+81-3-1234-5678", True),
            ("+81 3 1234 5678", True),
            # Invalid international
            ("+1234567890", False),  # No country code structure
            ("+", False),  # Just plus sign
        ]

        for i, (text, should_match) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            if should_match:
                assert len(matches) == 1, f"Should detect: {text}"
                assert matches[0].metadata.get("is_international", False)
            else:
                assert len(matches) == 0, f"Should not detect: {text}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_confidence_scoring(self, detector, create_word):
        """Test confidence scoring for different patterns."""
        test_cases = [
            # High confidence - standard formats
            ("(555) 123-4567", 0.95),
            ("user@example.com", 0.95),
            ("https://www.example.com", 0.95),
            # Medium confidence - less standard
            ("5551234567", 0.85),
            ("www.example.com", 0.9),
            # Lower confidence - ambiguous
            ("555.123.4567", 0.85),
        ]

        for i, (text, min_confidence) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Should detect: {text}"
            assert (
                matches[0].confidence >= min_confidence
            ), f"Confidence too low for: {text}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skip_noise_words(self, detector, create_word):
        """Test that noise words are skipped."""
        word = create_word("contact@example.com")
        word.is_noise = True

        matches = await detector.detect([word])
        assert len(matches) == 0  # Should skip noise word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_domain_extraction(self, detector, create_word):
        """Test domain extraction from various URL formats."""
        test_cases = [
            ("https://www.amazon.com/products/item123", "amazon.com"),
            ("http://store.apple.com/us", "apple.com"),
            ("shop.netflix.com/merchandise", "netflix.com"),
            ("m.facebook.com/page", "facebook.com"),
            ("en.wikipedia.org/wiki/Article", "wikipedia.org"),
        ]

        for i, (url, expected_domain) in enumerate(test_cases):
            word = create_word(url, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1
            assert matches[0].metadata["domain"] == expected_domain
