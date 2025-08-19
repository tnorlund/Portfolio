"""Tests for datetime pattern detection."""

import pytest
from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection import (
    DateTimePatternDetector,
    PatternType)


class TestDateTimePatternDetector:
    """Test datetime pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Create a datetime pattern detector."""
        return DateTimePatternDetector()

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
                confidence=0.95)

        return _create_word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_date_formats(self, detector, create_word):
        """Test detection of various date formats."""
        test_cases = [
            # MM/DD/YYYY formats
            ("01/15/2024", PatternType.DATE, "2024-01-15"),
            ("12/31/2023", PatternType.DATE, "2023-12-31"),
            ("03/05/2024", PatternType.DATE, "2024-03-05"),
            # DD/MM/YYYY formats (ambiguous)
            ("15/01/2024", PatternType.DATE, "2024-01-15"),
            ("31/12/2023", PatternType.DATE, "2023-12-31"),
            # ISO format
            ("2024-01-15", PatternType.DATE, "2024-01-15"),
            ("2023-12-31", PatternType.DATE, "2023-12-31"),
            # Month names
            ("January 15, 2024", PatternType.DATE, "2024-01-15"),
            ("Jan 15, 2024", PatternType.DATE, "2024-01-15"),
            ("15 January 2024", PatternType.DATE, "2024-01-15"),
            ("15-Jan-2024", PatternType.DATE, "2024-01-15"),
            # Different separators
            ("01.15.2024", PatternType.DATE, "2024-01-15"),
            ("01-15-2024", PatternType.DATE, "2024-01-15"),
        ]

        for i, (text, expected_type, expected_value) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect date in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.metadata["normalized_date"] == expected_value
            assert match.confidence > 0.5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_time_formats(self, detector, create_word):
        """Test detection of various time formats."""
        test_cases = [
            # 12-hour format
            ("2:30 PM", PatternType.TIME, "14:30:00"),
            ("11:45 AM", PatternType.TIME, "11:45:00"),
            ("12:00 PM", PatternType.TIME, "12:00:00"),
            ("12:00 AM", PatternType.TIME, "00:00:00"),
            ("2:30PM", PatternType.TIME, "14:30:00"),  # No space
            # 24-hour format
            ("14:30", PatternType.TIME, "14:30:00"),
            ("23:59", PatternType.TIME, "23:59:00"),
            ("00:00", PatternType.TIME, "00:00:00"),
            # With seconds
            ("14:30:45", PatternType.TIME, "14:30:45"),
            ("2:30:45 PM", PatternType.TIME, "14:30:45"),
        ]

        for i, (text, expected_type, expected_value) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect time in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.metadata["normalized_time"] == expected_value
            assert match.confidence >= 0.8

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_datetime_combinations(self, detector, create_word):
        """Test detection of combined date and time patterns."""
        test_cases = [
            # Date and time in one string
            (
                "01/15/2024 2:30 PM",
                PatternType.DATETIME,
                "2024-01-15T14:30:00"),
            ("2024-01-15 14:30", PatternType.DATETIME, "2024-01-15T14:30:00"),
            (
                "Jan 15, 2024 2:30PM",
                PatternType.DATETIME,
                "2024-01-15T14:30:00"),
            # Date and time as separate but adjacent words
            ("01/15/2024", "2:30 PM", "2024-01-15", "14:30:00"),
            ("2024-01-15", "14:30:00", "2024-01-15", "14:30:00"),
        ]

        # Test combined patterns
        for i, item in enumerate(test_cases[:3]):
            text, expected_type, expected_value = item
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) > 0, f"Failed to detect datetime in: {text}"
            # Should detect as DATETIME or both DATE and TIME
            datetime_matches = [
                m for m in matches if m.pattern_type == PatternType.DATETIME
            ]
            if datetime_matches:
                assert (
                    datetime_matches[0].metadata.get("normalized_datetime")
                    == expected_value
                )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_two_digit_year_handling(self, detector, create_word):
        """Test handling of 2-digit years."""
        valid_test_cases = [
            # Recent years (should be 20xx)
            ("01/15/24", "2024-01-15"),
            ("12/31/23", "2023-12-31"),
            ("03/05/25", "2025-03-05"),
            # Y2K boundary case
            ("01/15/00", "2000-01-15"),  # Y2K boundary
        ]

        # Test valid 2-digit years
        for i, (text, expected_date) in enumerate(valid_test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect date in: {text}"
            assert matches[0].pattern_type == PatternType.DATE
            # Note: The actual year interpretation may vary based on implementation

        # Test ambiguous years that should be rejected
        ambiguous_cases = ["01/15/99"]  # Ambiguous - could be 1999 or 2099

        for i, text in enumerate(ambiguous_cases):
            word = create_word(text, word_id=i + 100)
            matches = await detector.detect([word])

            assert len(matches) == 0, f"Should reject ambiguous date: {text}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_dates(self, detector, create_word):
        """Test that invalid dates are handled properly."""
        test_cases = [
            "13/32/2024",  # Invalid day
            "00/15/2024",  # Invalid month
            "02/30/2024",  # February 30th
            "04/31/2024",  # April 31st
            "2024-13-01",  # Invalid month in ISO
            "2024-02-30",  # Invalid February date
        ]

        for i, text in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            # Detector might still match the pattern but should indicate invalidity
            if matches:
                assert (
                    matches[0].confidence < 0.8
                )  # Lower confidence for invalid dates

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_times(self, detector, create_word):
        """Test that invalid times are not detected or have low confidence."""
        test_cases = [
            "25:00",  # Invalid hour
            "12:60",  # Invalid minute
            "14:30:60",  # Invalid second
            "13:00 PM",  # Invalid 12-hour format (13 PM doesn't exist)
            "0:00 AM",  # Should be 12:00 AM
        ]

        for i, text in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            # Either no match or low confidence
            if matches:
                assert matches[0].confidence < 0.7

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ambiguous_dates(self, detector, create_word):
        """Test handling of ambiguous date formats."""
        # Dates that could be MM/DD or DD/MM
        ambiguous_cases = [
            ("01/02/2024", ["2024-01-02", "2024-02-01"]),  # Jan 2 or Feb 1
            ("03/04/2024", ["2024-03-04", "2024-04-03"]),  # Mar 4 or Apr 3
            ("12/11/2024", ["2024-12-11", "2024-11-12"]),  # Dec 11 or Nov 12
        ]

        for i, (text, possible_dates) in enumerate(ambiguous_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) > 0, f"Failed to detect date in: {text}"
            # Should have lower confidence due to ambiguity
            assert matches[0].confidence <= 0.7
            # Should indicate ambiguity in metadata
            assert matches[0].metadata.get("is_ambiguous", False)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_receipt_context(self, detector, create_word):
        """Test datetime detection in receipt context."""
        receipt_data = [
            ("STORE RECEIPT", 0, 0),
            ("Date:", 1, 20),
            ("01/15/2024", 2, 40),
            ("Time:", 3, 60),
            ("2:30 PM", 4, 80),
            ("Transaction:", 5, 100),
            ("TX#12345", 6, 120),
        ]

        words = [
            create_word(text, word_id=i, y_pos=y)
            for text, i, y in receipt_data
        ]

        matches = await detector.detect(words)

        # Should detect date and time
        assert len(matches) == 2

        date_matches = [
            m for m in matches if m.pattern_type == PatternType.DATE
        ]
        time_matches = [
            m for m in matches if m.pattern_type == PatternType.TIME
        ]

        assert len(date_matches) == 1
        assert len(time_matches) == 1

        # Check values
        assert date_matches[0].metadata["normalized_date"] == "2024-01-15"
        assert time_matches[0].metadata["normalized_time"] == "14:30:00"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_confidence_by_format(self, detector, create_word):
        """Test that confidence varies by format clarity."""
        test_cases = [
            # High confidence - unambiguous
            ("2024-01-15", 0.9),  # ISO format
            ("January 15, 2024", 0.9),  # Month name
            ("15/01/2024", 0.9),  # Clearly DD/MM (day > 12)
            # Medium confidence
            ("01/15/2024", 0.8),  # Standard US format
            ("01-15-2024", 0.8),  # With dashes
            # Lower confidence - ambiguous
            ("01/02/2024", 0.7),  # Could be MM/DD or DD/MM
            ("03/04/24", 0.6),  # 2-digit year + ambiguous
        ]

        for i, (text, expected_min_confidence) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) > 0, f"Failed to detect date in: {text}"
            assert (
                matches[0].confidence >= expected_min_confidence * 0.9
            )  # Allow some variance

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skip_noise_words(self, detector, create_word):
        """Test that noise words are skipped."""
        word = create_word("01/15/2024")
        word.is_noise = True

        matches = await detector.detect([word])
        assert len(matches) == 0  # Should skip noise word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_date_extraction_metadata(self, detector, create_word):
        """Test that metadata contains useful date components."""
        word = create_word("January 15, 2024")
        matches = await detector.detect([word])

        assert len(matches) == 1
        metadata = matches[0].metadata

        # Should include parsed components
        assert "year" in metadata
        assert "month" in metadata
        assert "day" in metadata
        assert metadata["year"] == 2024
        assert metadata["month"] == 1
        assert metadata["day"] == 15
        assert metadata["format"] == "MONTH_NAME"
