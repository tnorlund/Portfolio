"""Tests for noise word detection functionality."""

import pytest

from receipt_label.utils.noise_detection import (
    NoiseDetectionConfig,
    is_noise_word,
    is_noise_line,
)


class TestNoiseDetection:
    """Test suite for noise word detection."""

    @pytest.mark.unit
    def test_single_punctuation_detection(self):
        """Test that single punctuation marks are detected as noise."""
        # Common punctuation marks
        punctuation_marks = [
            ".",
            ",",
            ";",
            ":",
            "!",
            "?",
            '"',
            "'",
            "-",
            "(",
            ")",
            "[",
            "]",
            "{",
            "}",
        ]

        for mark in punctuation_marks:
            assert (
                is_noise_word(mark) is True
            ), f"'{mark}' should be detected as noise"

    @pytest.mark.unit
    def test_separator_detection(self):
        """Test that separator characters are detected as noise."""
        separators = ["|", "/", "\\", "~", "_", "=", "+", "*", "&", "%"]

        for sep in separators:
            assert (
                is_noise_word(sep) is True
            ), f"'{sep}' should be detected as noise"

    @pytest.mark.unit
    def test_multi_character_separators(self):
        """Test that multi-character separators are detected as noise."""
        multi_separators = [
            "--",
            "---",
            "----",
            "==",
            "===",
            "====",
            "**",
            "***",
            "****",
            "__",
            "___",
            "____",
            "..",
            "...",
            "....",
            "//",
            "///",
            "////",
            "\\\\",
            "\\\\\\",
            "\\\\\\\\",
            "~~",
            "~~~",
            "~~~~",
        ]

        for sep in multi_separators:
            assert (
                is_noise_word(sep) is True
            ), f"'{sep}' should be detected as noise"

    @pytest.mark.unit
    def test_currency_preservation(self):
        """Test that currency symbols and amounts are NOT marked as noise."""
        currency_examples = [
            "$",
            "€",
            "£",
            "¥",  # Just symbols
            "$5.99",
            "$10",
            "$1,234.56",  # Dollar amounts
            "€10",
            "€1,234.56",  # Euro amounts
            "£15",
            "£1,234",  # Pound amounts
            "¥100",
            "¥1,234",  # Yen amounts
            "5.99$",
            "10€",
            "15£",
            "100¥",  # Symbol after amount
        ]

        for currency in currency_examples:
            assert (
                is_noise_word(currency) is False
            ), f"'{currency}' should NOT be noise"

    @pytest.mark.unit
    def test_meaningful_words(self):
        """Test that meaningful words are NOT marked as noise."""
        meaningful_words = [
            "TOTAL",
            "TAX",
            "SUBTOTAL",  # Receipt keywords
            "QTY",
            "EA",
            "LB",  # Units
            "VISA",
            "CASH",
            "DEBIT",  # Payment methods
            "Item",
            "Product",
            "Price",  # Common words
            "123",
            "456",
            "A1",
            "B2",  # Alphanumeric codes
        ]

        for word in meaningful_words:
            assert (
                is_noise_word(word) is False
            ), f"'{word}' should NOT be noise"

    @pytest.mark.unit
    def test_empty_and_whitespace(self):
        """Test that empty strings and whitespace are detected as noise."""
        assert is_noise_word("") is True
        assert is_noise_word(" ") is True
        assert is_noise_word("  ") is True
        assert is_noise_word("\t") is True
        assert is_noise_word("\n") is True

    @pytest.mark.unit
    def test_ocr_artifacts(self):
        """Test that common OCR artifacts are detected as noise."""
        artifacts = [
            "°",
            "•",
            "§",
            "¶",  # Special characters
            "<<<",
            ">>>",  # Angle brackets
            "^^^",
            "```",  # Other repeated chars
        ]

        for artifact in artifacts:
            assert (
                is_noise_word(artifact) is True
            ), f"'{artifact}' should be detected as noise"

    @pytest.mark.unit
    def test_edge_cases(self):
        """Test edge cases for noise detection."""
        # Mixed punctuation that should be noise
        assert is_noise_word(".-") is True
        assert is_noise_word("...") is True
        assert is_noise_word("!!!") is True

        # Meaningful combinations that should NOT be noise
        assert is_noise_word("1.5") is False  # Decimal number
        assert is_noise_word("U.S.") is False  # Abbreviation
        assert is_noise_word("email@example.com") is False  # Email

    @pytest.mark.unit
    def test_custom_configuration(self):
        """Test noise detection with custom configuration."""
        # Custom config that doesn't preserve currency
        custom_config = NoiseDetectionConfig(preserve_currency=False)

        # Currency should now be marked as noise
        assert is_noise_word("$", custom_config) is True
        assert is_noise_word("€", custom_config) is True

        # But amounts with numbers should still not be noise (they're alphanumeric)
        assert is_noise_word("$5.99", custom_config) is False

    @pytest.mark.unit
    def test_meaningful_short_words(self):
        """Test that meaningful short words are not marked as noise."""
        # Single character meaningful words
        assert is_noise_word("I") is False  # Pronoun
        assert is_noise_word("A") is False  # Article
        assert is_noise_word("1") is False  # Number
        assert is_noise_word("X") is False  # Letter (could be size, etc.)

        # Two character meaningful words
        assert is_noise_word("OR") is False
        assert is_noise_word("TO") is False
        assert is_noise_word("IN") is False
        assert is_noise_word("NO") is False

    @pytest.mark.unit
    def test_special_receipt_patterns(self):
        """Test special patterns commonly found on receipts."""
        # These should be noise
        noise_patterns = [
            "******",  # Credit card masking
            "----",  # Line separators
            "====",  # Double line separators
            "....",  # Ellipsis variants
        ]

        for pattern in noise_patterns:
            assert (
                is_noise_word(pattern) is True
            ), f"'{pattern}' should be noise"

        # These should NOT be noise
        meaningful_patterns = [
            "****1234",  # Masked credit card number
            "1-2-3",  # Date or code format
            "A/C",  # Account abbreviation
            "#123",  # Order number
        ]

        for pattern in meaningful_patterns:
            assert (
                is_noise_word(pattern) is False
            ), f"'{pattern}' should NOT be noise"


class TestNoiseLineDetection:
    """Test suite for noise line detection."""

    @pytest.mark.unit
    def test_separator_lines(self):
        """Test that separator lines are detected as noise."""
        separator_lines = [
            "==================",
            "--------------------",
            "********************",
            "____________________",
            "....................",
            "////////////////////",
            "\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\",
            "~~~~~~~~~~~~~~~~~~~~",
            "||||||||||||||||||||",
            "####################",
            "= = = = = = = = = = =",  # Spaced separators
            "- - - - - - - - - - -",
            "* * * * * * * * * * *",
        ]

        for line in separator_lines:
            assert (
                is_noise_line(line) is True
            ), f"'{line}' should be detected as noise line"

    @pytest.mark.unit
    def test_empty_and_whitespace_lines(self):
        """Test that empty and whitespace-only lines are noise."""
        empty_lines = [
            "",
            " ",
            "  ",
            "   ",
            "\t",
            "\t\t",
            " \t ",
            "\n",
            " \n ",
        ]

        for line in empty_lines:
            assert (
                is_noise_line(line) is True
            ), f"'{repr(line)}' should be detected as noise line"

    @pytest.mark.unit
    def test_lines_with_all_noise_words(self):
        """Test that lines containing only noise words are detected as noise."""
        noise_word_lines = [
            "| | | | |",
            ". . . . .",
            ", , , ,",
            "- - -",
            "/ \\ / \\",
            "( ) [ ]",
            "{ } < >",
        ]

        for line in noise_word_lines:
            assert (
                is_noise_line(line) is True
            ), f"'{line}' should be detected as noise line"

    @pytest.mark.unit
    def test_meaningful_lines(self):
        """Test that meaningful lines are NOT detected as noise."""
        meaningful_lines = [
            "TOTAL: $12.99",
            "Big Mac",
            "Walmart #1234",
            "Thank you for shopping",
            "DATE: 01/15/2024",
            "SUBTOTAL",
            "TAX",
            "Your cashier was: John",
            "Order #ABC123",
            "1 x Apple @ $0.99",
            "VISA ****1234",
            "============ RECEIPT ============",  # Has meaningful word
            "--- CUSTOMER COPY ---",  # Has meaningful words
        ]

        for line in meaningful_lines:
            assert (
                is_noise_line(line) is False
            ), f"'{line}' should NOT be detected as noise line"

    @pytest.mark.unit
    def test_mixed_separator_patterns(self):
        """Test mixed separator patterns that are still noise."""
        mixed_separators = [
            "-*-*-*-*-*-",
            "=+=+=+=+=+",
            "_-_-_-_-_-",
            "*~*~*~*~*~",
            "#-#-#-#-#-",
            "... ... ...",
            "--- === ---",
            "*** --- ***",
        ]

        for line in mixed_separators:
            assert (
                is_noise_line(line) is True
            ), f"'{line}' should be detected as noise line"

    @pytest.mark.unit
    def test_lines_with_partial_meaningful_content(self):
        """Test that lines with at least some meaningful content are not noise."""
        partial_meaningful = [
            "---- TOTAL ----",  # Has meaningful word
            "**** $12.99 ****",  # Has price
            "==== END ====",  # Has meaningful word
            "| Item | Price |",  # Table header
            "... more ...",  # Has meaningful word
            "Page 1 of 2",  # Page indicator
        ]

        for line in partial_meaningful:
            assert (
                is_noise_line(line) is False
            ), f"'{line}' should NOT be detected as noise line"

    @pytest.mark.unit
    def test_custom_configuration_for_lines(self):
        """Test line noise detection with custom configuration."""
        # Custom config that doesn't preserve currency
        custom_config = NoiseDetectionConfig(preserve_currency=False)

        # A line with only currency symbols should be noise with custom config
        assert is_noise_line("$ $ $ $", custom_config) is True

        # But with default config, currency is preserved
        assert is_noise_line("$ $ $ $") is False

    @pytest.mark.unit
    def test_real_receipt_examples(self):
        """Test with real receipt line examples."""
        # Common noise lines from receipts
        noise_examples = [
            "********************************",
            "--------------------------------",
            "................................",
            "                                ",  # Just spaces
            "||||||||||||||||||||||||||||||||",
        ]

        for line in noise_examples:
            assert (
                is_noise_line(line) is True
            ), f"'{line}' should be detected as noise"

        # Common meaningful lines from receipts
        meaningful_examples = [
            "Store #1234 Reg #02 Tran #5678",
            "GROCERY",
            "2 @ $1.99",
            "SUBTOTAL            $19.99",
            "SALES TAX            $1.60",
            "TOTAL               $21.59",
            "CHANGE DUE           $0.00",
            "Items Sold: 5",
        ]

        for line in meaningful_examples:
            assert (
                is_noise_line(line) is False
            ), f"'{line}' should NOT be detected as noise"
