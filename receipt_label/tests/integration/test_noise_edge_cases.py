"""Edge case validation tests for noise word detection."""

import pytest
from receipt_dynamo.entities import ReceiptWord

from receipt_label.utils.noise_detection import (
    NoiseDetectionConfig,
    is_noise_word,
)


class TestNoiseEdgeCases:
    """Test edge cases and validation for noise word detection."""

    @pytest.mark.integration
    def test_receipt_specific_patterns(self):
        """Test patterns commonly found on different receipt types."""
        # Grocery store patterns
        grocery_patterns = [
            ("QTY", False),  # Quantity indicator
            ("EA", False),  # Each
            ("LB", False),  # Pounds
            ("@", True),  # At symbol (separator)
            ("F", False),  # Food/tax indicator
            ("N", False),  # Non-food indicator
            ("T", False),  # Taxable indicator
            ("*", True),  # Single asterisk
            ("****", True),  # Multiple asterisks
            ("SKU#", False),  # SKU prefix
            ("12345", False),  # SKU number
        ]

        for text, expected in grocery_patterns:
            result = is_noise_word(text)
            assert (
                result == expected
            ), f"Grocery pattern '{text}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_restaurant_receipt_patterns(self):
        """Test patterns specific to restaurant receipts."""
        restaurant_patterns = [
            ("TABLE", False),  # Table number prefix
            ("SERVER", False),  # Server name prefix
            ("GUEST", False),  # Guest count
            ("15%", False),  # Tip percentage
            ("18%", False),  # Tip percentage
            ("20%", False),  # Tip percentage
            ("TIP", False),  # Tip label
            ("======", True),  # Double line separator
            ("------", True),  # Single line separator
            ("Subtotal:", False),  # With colon attached
            ("Tax:", False),  # With colon attached
            ("Total:", False),  # With colon attached
        ]

        for text, expected in restaurant_patterns:
            result = is_noise_word(text)
            assert (
                result == expected
            ), f"Restaurant pattern '{text}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_gas_station_patterns(self):
        """Test patterns specific to gas station receipts."""
        gas_patterns = [
            ("PUMP", False),  # Pump number
            ("GALLONS", False),  # Unit
            ("GAL", False),  # Abbreviated unit
            ("PPG", False),  # Price per gallon
            ("REG", False),  # Regular gas
            ("PLUS", False),  # Plus gas
            ("PREMIUM", False),  # Premium gas
            ("DIESEL", False),  # Diesel
            ("#", True),  # Hash symbol alone
            ("###", True),  # Multiple hashes
            ("$3.459", False),  # Gas price format
            ("12.345", False),  # Gallon amount
        ]

        for text, expected in gas_patterns:
            result = is_noise_word(text)
            assert (
                result == expected
            ), f"Gas station pattern '{text}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_retail_store_patterns(self):
        """Test patterns specific to retail store receipts."""
        retail_patterns = [
            ("SIZE", False),  # Size indicator
            ("S", False),  # Small
            ("M", False),  # Medium
            ("L", False),  # Large
            ("XL", False),  # Extra large
            ("XXL", False),  # Double extra large
            ("COLOR", False),  # Color indicator
            ("DEPT", False),  # Department
            ("STYLE", False),  # Style number
            ("UPC", False),  # UPC prefix
            ("||||||", True),  # Barcode representation
            ("......", True),  # Dots separator
        ]

        for text, expected in retail_patterns:
            result = is_noise_word(text)
            assert (
                result == expected
            ), f"Retail pattern '{text}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_mixed_alphanumeric_codes(self):
        """Test mixed alphanumeric codes that should NOT be noise."""
        codes = [
            "ABC123",  # Product code
            "SKU001",  # SKU
            "REF#1234",  # Reference number
            "ORDER#5678",  # Order number
            "TRANS#9012",  # Transaction number
            "ID:3456",  # ID with colon
            "CODE-789",  # Code with dash
            "ITEM_001",  # Item with underscore
            "V2.0",  # Version number
            "3M",  # Brand name
            "7UP",  # Product name
            "A1",  # Steak sauce/Grid reference
        ]

        for code in codes:
            assert (
                is_noise_word(code) is False
            ), f"Code '{code}' should NOT be noise"

    @pytest.mark.integration
    def test_unicode_and_special_characters(self):
        """Test handling of unicode and special characters."""
        special_chars = [
            ("€", False),  # Euro symbol (currency)
            ("£", False),  # Pound symbol (currency)
            ("¥", False),  # Yen symbol (currency)
            ("¢", False),  # Cent symbol (currency)
            ("°", True),  # Degree symbol (artifact)
            ("•", True),  # Bullet point (artifact)
            ("§", True),  # Section symbol (artifact)
            ("¶", True),  # Paragraph symbol (artifact)
            ("©", True),  # Copyright (artifact)
            ("®", False),  # Registered trademark (keep for brands)
            ("™", False),  # Trademark (keep for brands)
            ("±", True),  # Plus-minus (artifact)
        ]

        for char, expected in special_chars:
            result = is_noise_word(char)
            assert (
                result == expected
            ), f"Special char '{char}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_whitespace_variations(self):
        """Test various whitespace patterns."""
        whitespace_tests = [
            ("", True),  # Empty string
            (" ", True),  # Single space
            ("  ", True),  # Multiple spaces
            ("\t", True),  # Tab
            ("\n", True),  # Newline
            ("\r\n", True),  # Windows newline
            (" \t\n ", True),  # Mixed whitespace
            ("A B", False),  # Space within text (not just whitespace)
        ]

        for text, expected in whitespace_tests:
            result = is_noise_word(text)
            assert (
                result == expected
            ), f"Whitespace '{repr(text)}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_ocr_common_misreads(self):
        """Test common OCR misread patterns."""
        ocr_misreads = [
            ("l", False),  # Lowercase L (could be meaningful)
            ("I", False),  # Uppercase I (could be meaningful)
            ("1", False),  # Number 1 (meaningful)
            ("0", False),  # Number 0 (meaningful)
            ("O", False),  # Letter O (meaningful)
            ("'", True),  # Single quote alone
            ("''", True),  # Double single quotes
            ('""', True),  # Double quotes
            ("``", True),  # Backticks
            (",,", True),  # Double comma
            ("..", True),  # Double period
        ]

        for text, expected in ocr_misreads:
            result = is_noise_word(text)
            assert (
                result == expected
            ), f"OCR pattern '{text}' should be {'noise' if expected else 'meaningful'}"

    @pytest.mark.integration
    def test_configuration_variations(self):
        """Test different configuration settings."""
        # Test with currency preservation disabled
        config_no_currency = NoiseDetectionConfig(preserve_currency=False)
        assert is_noise_word("$", config_no_currency) is True
        assert is_noise_word("€", config_no_currency) is True
        assert is_noise_word("£", config_no_currency) is True

        # Test with custom patterns
        config_custom = NoiseDetectionConfig(
            punctuation_patterns=[r"^[.!?]$"],  # Only some punctuation
            separator_patterns=[r"^[-]$"],  # Only dash
            artifact_patterns=[],  # No artifacts
        )
        assert is_noise_word(".", config_custom) is True
        assert (
            is_noise_word(",", config_custom) is False
        )  # Not in custom pattern
        assert is_noise_word("-", config_custom) is True
        assert (
            is_noise_word("|", config_custom) is False
        )  # Not in custom pattern

    @pytest.mark.integration
    def test_performance_on_large_receipts(self):
        """Test performance on receipts with many words."""
        import time

        # Generate a large receipt (1000 words)
        large_receipt_words = []
        for i in range(1000):
            if i % 3 == 0:
                text = "."  # Noise
            elif i % 3 == 1:
                text = f"ITEM{i}"  # Meaningful
            else:
                text = "$9.99"  # Meaningful

            large_receipt_words.append(text)

        # Time the detection
        start = time.time()
        results = [is_noise_word(word) for word in large_receipt_words]
        duration = time.time() - start

        # Should process 1000 words in under 50ms
        assert duration < 0.05, f"Processing 1000 words took {duration:.3f}s"

        # Verify detection worked
        noise_count = sum(1 for r in results if r)
        assert 300 <= noise_count <= 350  # About 1/3 should be noise

    @pytest.mark.integration
    def test_real_world_receipt_samples(self):
        """Test against real-world receipt text samples."""
        # Sample from actual receipt
        receipt_lines = [
            ["TARGET", "STORE", "#1234"],
            ["123", "MAIN", "ST"],
            ["ANYTOWN", ",", "CA", "12345"],
            ["(", "555", ")", "123-4567"],
            ["================================"],
            ["GROCERY"],
            ["MILK", "2%", "GAL", "$", "3.99", "F"],
            [
                "BANANAS",
                "2.15",
                "LB",
                "@",
                "$0.59",
                "/",
                "LB",
                "$",
                "1.27",
                "F",
            ],
            ["---", "---", "---", "---", "---"],
            ["SUBTOTAL", "$", "5.26"],
            ["TAX", "$", "0.43"],
            ["TOTAL", "$", "5.69"],
            ["****", "****", "****", "1234"],
            ["APPROVED"],
            [".", ".", ".", ".", "."],
        ]

        # Process each line
        noise_stats = {"total": 0, "noise": 0}
        for line in receipt_lines:
            for word in line:
                noise_stats["total"] += 1
                if is_noise_word(word):
                    noise_stats["noise"] += 1

        # Calculate noise percentage
        noise_percentage = (noise_stats["noise"] / noise_stats["total"]) * 100

        # Should be in expected range
        assert (
            25 <= noise_percentage <= 45
        ), f"Noise percentage {noise_percentage:.1f}% outside expected range"
