"""Unit tests for normalization utilities."""

import pytest
from receipt_chroma.embedding.utils.normalize import (
    build_full_address_from_lines,
    build_full_address_from_words,
    normalize_address,
    normalize_phone,
    normalize_url,
)


class TestNormalizePhone:
    """Test phone number normalization."""

    def test_valid_10_digit_phone(self):
        """Test valid 10-digit phone number."""
        assert normalize_phone("1234567890") == "1234567890"
        assert normalize_phone("(123) 456-7890") == "1234567890"
        assert normalize_phone("123-456-7890") == "1234567890"
        assert normalize_phone("123.456.7890") == "1234567890"

    def test_phone_with_country_code(self):
        """Test phone number with country code."""
        assert normalize_phone("11234567890") == "1234567890"
        assert normalize_phone("+1 123-456-7890") == "1234567890"

    def test_phone_extra_digits(self):
        """Test phone number with extra digits."""
        assert normalize_phone("1234567890123") == "4567890123"  # Last 10
        assert normalize_phone("12345678901234") == "5678901234"  # Last 10

    def test_invalid_phone(self):
        """Test invalid phone numbers."""
        assert normalize_phone("123") == ""  # Too short
        # 11 digits that don't start with 1: takes last 10 digits
        assert normalize_phone("12345678901") == "2345678901"
        assert normalize_phone("") == ""
        assert normalize_phone(None) == ""

    def test_reject_trivial_sequences(self):
        """Test rejection of trivial sequences."""
        assert normalize_phone("0000000000") == ""
        assert normalize_phone("1111111111") == ""
        assert normalize_phone("2222222222") == ""


class TestNormalizeAddress:
    """Test address normalization."""

    def test_basic_address(self):
        """Test basic address normalization."""
        assert normalize_address("123 Main St") == "123 MAIN ST"
        assert normalize_address("  123  Main   St  ") == "123 MAIN ST"

    def test_address_suffix_mapping(self):
        """Test address suffix mapping."""
        assert normalize_address("123 Main Street") == "123 MAIN ST"
        assert normalize_address("456 Oak Road") == "456 OAK RD"
        assert normalize_address("789 Park Avenue") == "789 PARK AVE"
        assert normalize_address("321 Elm Boulevard") == "321 ELM BLVD"
        assert normalize_address("654 Pine Drive") == "654 PINE DR"
        assert normalize_address("987 Maple Lane") == "987 MAPLE LN"

    def test_address_with_punctuation(self):
        """Test address with punctuation."""
        assert (
            normalize_address("123 Main St., Suite 100")
            == "123 MAIN ST STE 100"
        )
        assert normalize_address("456 Oak Rd, Apt 5") == "456 OAK RD APT 5"

    def test_empty_address(self):
        """Test empty address."""
        assert normalize_address("") == ""
        assert normalize_address(None) == ""
        assert normalize_address("   ") == ""


class TestNormalizeUrl:
    """Test URL normalization."""

    def test_basic_url(self):
        """Test basic URL normalization."""
        assert normalize_url("https://example.com") == "example.com/"
        assert normalize_url("http://example.com") == "example.com/"
        assert normalize_url("example.com") == "example.com/"

    def test_url_with_www(self):
        """Test URL with www prefix."""
        assert normalize_url("https://www.example.com") == "example.com/"
        assert normalize_url("www.example.com") == "example.com/"

    def test_url_with_path(self):
        """Test URL with path."""
        assert normalize_url("https://example.com/path") == "example.com/path"
        assert (
            normalize_url("https://example.com/path/to/page")
            == "example.com/path/to/page"
        )

    def test_url_with_query_and_fragment(self):
        """Test URL with query and fragment."""
        assert (
            normalize_url("https://example.com/path?key=value")
            == "example.com/path"
        )
        assert (
            normalize_url("https://example.com/path#fragment")
            == "example.com/path"
        )
        assert (
            normalize_url("https://example.com/path?key=value#fragment")
            == "example.com/path"
        )

    def test_url_trailing_slash(self):
        """Test URL trailing slash handling."""
        assert normalize_url("https://example.com/") == "example.com/"
        assert normalize_url("https://example.com/path/") == "example.com/path"
        assert normalize_url("https://example.com//") == "example.com/"

    def test_url_multiple_slashes(self):
        """Test URL with multiple slashes."""
        assert normalize_url("https://example.com//path") == "example.com/path"
        assert (
            normalize_url("https://example.com///path") == "example.com/path"
        )

    def test_email_rejection(self):
        """Test that emails are rejected."""
        assert normalize_url("user@example.com") == ""
        assert normalize_url("test@domain.org") == ""

    def test_empty_url(self):
        """Test empty URL."""
        assert normalize_url("") == ""
        assert normalize_url(None) == ""


class TestBuildFullAddressFromWords:
    """Test building address from words."""

    def test_single_address_word(self):
        """Test with single address word."""

        class MockWord:
            def __init__(self, text: str, extracted_data: dict):
                self.text = text
                self.extracted_data = extracted_data

        word = MockWord(
            "123 Main St", {"type": "address", "value": "123 Main St"}
        )
        result = build_full_address_from_words([word])
        assert result == "123 MAIN ST"

    def test_multiple_address_words(self):
        """Test with multiple address words."""

        class MockWord:
            def __init__(self, text: str, extracted_data: dict):
                self.text = text
                self.extracted_data = extracted_data

        words = [
            MockWord(
                "123 Main St", {"type": "address", "value": "123 Main St"}
            ),
            MockWord(
                "New York", {"type": "address", "value": "New York, NY 10001"}
            ),
        ]
        result = build_full_address_from_words(words)
        assert "123 MAIN ST" in result or "NEW YORK" in result

    def test_no_address_words(self):
        """Test with no address words."""

        class MockWord:
            def __init__(self, text: str):
                self.text = text
                self.extracted_data = {}

        word = MockWord("Hello")
        result = build_full_address_from_words([word])
        assert result == ""

    def test_words_without_extracted_data(self):
        """Test words without extracted_data attribute."""

        class MockWord:
            def __init__(self, text: str):
                self.text = text

        word = MockWord("Hello")
        result = build_full_address_from_words([word])
        assert result == ""


class TestBuildFullAddressFromLines:
    """Test building address from lines."""

    def test_single_line(self):
        """Test with single line."""

        class MockLine:
            def __init__(
                self, line_id: int, text: str, is_noise: bool = False
            ):
                self.line_id = line_id
                self.text = text
                self.is_noise = is_noise

        line = MockLine(1, "123 Main St")
        result = build_full_address_from_lines([line])
        assert result == "123 MAIN ST"

    def test_multiple_lines(self):
        """Test with multiple lines."""

        class MockLine:
            def __init__(
                self, line_id: int, text: str, is_noise: bool = False
            ):
                self.line_id = line_id
                self.text = text
                self.is_noise = is_noise

        lines = [
            MockLine(1, "123 Main St"),
            MockLine(2, "New York, NY 10001"),
        ]
        result = build_full_address_from_lines(lines)
        assert "123 MAIN ST" in result and "NEW YORK" in result

    def test_lines_sorted_by_line_id(self):
        """Test that lines are sorted by line_id."""

        class MockLine:
            def __init__(
                self, line_id: int, text: str, is_noise: bool = False
            ):
                self.line_id = line_id
                self.text = text
                self.is_noise = is_noise

        lines = [
            MockLine(3, "Third"),
            MockLine(1, "First"),
            MockLine(2, "Second"),
        ]
        result = build_full_address_from_lines(lines)
        assert result == "FIRST SECOND THIRD"

    def test_noise_lines_excluded(self):
        """Test that noise lines are excluded."""

        class MockLine:
            def __init__(
                self, line_id: int, text: str, is_noise: bool = False
            ):
                self.line_id = line_id
                self.text = text
                self.is_noise = is_noise

        lines = [
            MockLine(1, "123 Main St", is_noise=False),
            MockLine(2, "Noise", is_noise=True),
            MockLine(3, "New York", is_noise=False),
        ]
        result = build_full_address_from_lines(lines)
        assert "NOISE" not in result
        assert "123 MAIN ST" in result

    def test_empty_lines(self):
        """Test with empty lines."""

        class MockLine:
            def __init__(self, line_id: int, text: str):
                self.line_id = line_id
                self.text = text
                self.is_noise = False

        result = build_full_address_from_lines([])
        assert result == ""
