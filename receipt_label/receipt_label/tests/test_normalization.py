import pytest

from receipt_label.merchant_validation.utils.normalization import (
    format_canonical_merchant_name,
    normalize_address,
    normalize_phone,
    normalize_text,
    preprocess_for_comparison,
)


@pytest.mark.unit
class TestNormalizeAddress:
    """Test cases for address normalization."""

    def test_normalize_address_basic(self):
        """Test basic address normalization."""
        assert normalize_address("123 Main St.") == "123 main street"
        assert normalize_address("456 OAK AVE.") == "456 oak avenue"
        assert normalize_address("789 Elm Blvd.") == "789 elm boulevard"

    def test_normalize_address_directions(self):
        """Test direction abbreviation expansion."""
        assert normalize_address("123 N. Main St.") == "123 north main street"
        assert normalize_address("456 S Main") == "456 south main"
        assert normalize_address("789 E. West St.") == "789 east west street"
        assert normalize_address("321 NW Park") == "321 northwest park"
        assert normalize_address("654 SE Corner") == "654 southeast corner"

    def test_normalize_address_units(self):
        """Test unit abbreviation expansion."""
        assert (
            normalize_address("123 Main St. Apt. 5")
            == "123 main street apartment 5"
        )
        assert normalize_address("456 Oak Ste. 100") == "456 oak suite 100"
        assert normalize_address("789 Elm Fl. 3") == "789 elm floor 3"

    def test_normalize_address_special_chars(self):
        """Test special character handling."""
        assert (
            normalize_address("123 Main St., Suite #5")
            == "123 main street suite 5"
        )
        assert normalize_address("456 Oak-Avenue") == "456 oak avenue"
        assert (
            normalize_address("789 Elm (Building A)") == "789 elm building a"
        )

    def test_normalize_address_whitespace(self):
        """Test whitespace normalization."""
        assert normalize_address("  123   Main   St.  ") == "123 main street"
        assert normalize_address("456\tOak\nAve.") == "456 oak avenue"
        assert normalize_address("789     Elm     ") == "789 elm"

    def test_normalize_address_edge_cases(self):
        """Test edge cases."""
        assert normalize_address("") == ""
        assert normalize_address(None) == ""
        assert normalize_address("   ") == ""
        # Test that periods without abbreviations are removed
        assert normalize_address("123. Main. Street.") == "123 main street"
        # Test multiple abbreviations in one address
        assert (
            normalize_address("123 N. Main St. Apt. 5 NE.")
            == "123 north main street apartment 5 northeast"
        )


@pytest.mark.unit
class TestNormalizePhone:
    """Test cases for phone number normalization."""

    def test_normalize_phone_basic(self):
        """Test basic phone normalization."""
        assert normalize_phone("(555) 123-4567") == "5551234567"
        assert normalize_phone("555-123-4567") == "5551234567"
        assert normalize_phone("555.123.4567") == "5551234567"
        assert normalize_phone("5551234567") == "5551234567"

    def test_normalize_phone_with_country_code(self):
        """Test phone normalization with country codes."""
        assert normalize_phone("+1-555-123-4567") == "15551234567"
        assert normalize_phone("1 (555) 123-4567") == "15551234567"
        assert normalize_phone("+1.555.123.4567") == "15551234567"

    def test_normalize_phone_with_extensions(self):
        """Test phone normalization with extensions."""
        assert normalize_phone("555-123-4567 ext. 123") == "5551234567123"
        assert normalize_phone("(555) 123-4567 x456") == "5551234567456"
        assert normalize_phone("555.123.4567 #789") == "5551234567789"

    def test_normalize_phone_special_formats(self):
        """Test special phone formats."""
        assert normalize_phone("555 123 4567") == "5551234567"
        assert normalize_phone("555/123/4567") == "5551234567"
        assert (
            normalize_phone("(555)123-4567") == "5551234567"
        )  # No space after area code

    def test_normalize_phone_edge_cases(self):
        """Test edge cases."""
        assert normalize_phone("") == ""
        assert normalize_phone(None) == ""
        assert normalize_phone("   ") == ""
        assert normalize_phone("no numbers here") == ""
        assert normalize_phone("Call: 555-1234") == "5551234"


@pytest.mark.unit
class TestNormalizeText:
    """Test cases for generic text normalization."""

    def test_normalize_text_basic(self):
        """Test basic text normalization."""
        assert normalize_text("Hello World") == "hello world"
        assert normalize_text("UPPERCASE TEXT") == "uppercase text"
        assert normalize_text("MiXeD cAsE") == "mixed case"

    def test_normalize_text_special_chars(self):
        """Test special character removal."""
        assert normalize_text("Hello@World!") == "hello world"
        assert normalize_text("Price: $19.99") == "price 19 99"
        assert (
            normalize_text("Email: test@example.com")
            == "email test example com"
        )
        assert normalize_text("100% quality") == "100 quality"

    def test_normalize_text_whitespace(self):
        """Test whitespace handling."""
        assert normalize_text("  multiple   spaces  ") == "multiple spaces"
        assert normalize_text("tabs\there\ttoo") == "tabs here too"
        assert normalize_text("new\nlines\nas\nwell") == "new lines as well"

    def test_normalize_text_unicode(self):
        """Test unicode character handling."""
        assert normalize_text("café") == "caf"  # Accented characters removed
        assert normalize_text("naïve") == "na ve"
        assert normalize_text("€100") == "100"

    def test_normalize_text_edge_cases(self):
        """Test edge cases."""
        assert normalize_text("") == ""
        assert normalize_text(None) == ""
        assert normalize_text("   ") == ""
        assert normalize_text("123") == "123"
        assert normalize_text("!@#$%^&*()") == ""


@pytest.mark.unit
class TestPreprocessForComparison:
    """Test cases for comparison preprocessing."""

    def test_preprocess_basic(self):
        """Test basic preprocessing."""
        assert preprocess_for_comparison("Hello World") == "hello world"
        assert preprocess_for_comparison("  TRIM ME  ") == "trim me"

    def test_preprocess_whitespace(self):
        """Test whitespace normalization."""
        assert (
            preprocess_for_comparison("multiple   spaces") == "multiple spaces"
        )
        assert preprocess_for_comparison("\ttabs\there\t") == "tabs here"
        assert preprocess_for_comparison("new\nlines") == "new lines"

    def test_preprocess_preserves_special_chars(self):
        """Test that special characters are preserved (unlike normalize_text)."""
        assert (
            preprocess_for_comparison("test@example.com") == "test@example.com"
        )
        assert preprocess_for_comparison("$19.99") == "$19.99"
        assert preprocess_for_comparison("100%") == "100%"

    def test_preprocess_edge_cases(self):
        """Test edge cases."""
        assert preprocess_for_comparison("") == ""
        assert preprocess_for_comparison(None) == ""
        assert preprocess_for_comparison("   ") == ""


@pytest.mark.unit
class TestFormatCanonicalMerchantName:
    """Test cases for canonical merchant name formatting."""

    def test_format_basic(self):
        """Test basic formatting."""
        assert format_canonical_merchant_name("starbucks") == "Starbucks"
        assert format_canonical_merchant_name("WALMART") == "Walmart"
        assert format_canonical_merchant_name("target store") == "Target Store"

    def test_format_removes_dashes(self):
        """Test dash removal with surrounding spaces."""
        assert format_canonical_merchant_name("7 - eleven") == "7 Eleven"
        assert format_canonical_merchant_name("wal - mart") == "Wal Mart"
        assert (
            format_canonical_merchant_name("cvs-pharmacy") == "Cvs-Pharmacy"
        )  # No spaces around dash

    def test_format_whitespace(self):
        """Test whitespace handling."""
        assert format_canonical_merchant_name("  starbucks  ") == "Starbucks"
        assert format_canonical_merchant_name("mc   donald's") == "Mc Donald's"
        assert format_canonical_merchant_name("burger\tking") == "Burger King"

    def test_format_preserves_apostrophes(self):
        """Test that apostrophes are preserved."""
        assert format_canonical_merchant_name("mcdonald's") == "Mcdonald's"
        assert format_canonical_merchant_name("wendy's") == "Wendy's"
        assert format_canonical_merchant_name("trader joe's") == "Trader Joe's"

    def test_format_edge_cases(self):
        """Test edge cases."""
        assert format_canonical_merchant_name("") == ""
        assert format_canonical_merchant_name(None) == ""
        assert format_canonical_merchant_name("   ") == ""
        # Test multiple dashes
        assert format_canonical_merchant_name("a - b - c") == "A B C"
        # Test title case edge cases
        assert format_canonical_merchant_name("ABC STORE") == "Abc Store"
        assert (
            format_canonical_merchant_name("mcdonald's usa")
            == "Mcdonald's Usa"
        )


@pytest.mark.unit
class TestNormalizationIntegration:
    """Integration tests for normalization functions."""

    def test_address_normalization_real_examples(self):
        """Test with real-world address examples."""
        # Common US addresses
        assert (
            normalize_address("123 N. Main St., Apt. 5B")
            == "123 north main street apartment 5b"
        )
        assert (
            normalize_address("456 SW Oak Blvd., Ste. 200")
            == "456 southwest oak boulevard suite 200"
        )
        assert (
            normalize_address("789 E. 1st Ave. Fl. 3")
            == "789 east 1st avenue floor 3"
        )

        # Edge cases from real data
        assert normalize_address("One Market Plaza") == "one market plaza"
        assert (
            normalize_address("1600 Pennsylvania Ave NW")
            == "1600 pennsylvania avenue northwest"
        )

    def test_phone_normalization_international(self):
        """Test international phone number formats."""
        # UK format
        assert normalize_phone("+44 20 7123 4567") == "442071234567"
        # Canadian format (same as US)
        assert normalize_phone("+1-416-555-0123") == "14165550123"
        # Format with spaces and dashes
        assert normalize_phone("+1 555-123-4567") == "15551234567"

    def test_merchant_name_common_chains(self):
        """Test formatting common merchant names."""
        assert (
            format_canonical_merchant_name("STARBUCKS COFFEE")
            == "Starbucks Coffee"
        )
        assert (
            format_canonical_merchant_name("walmart supercenter")
            == "Walmart Supercenter"
        )
        assert format_canonical_merchant_name("CVS/pharmacy") == "Cvs/Pharmacy"
        assert format_canonical_merchant_name("7-ELEVEN") == "7-Eleven"
        assert (
            format_canonical_merchant_name("McDonald's Restaurant")
            == "Mcdonald's Restaurant"
        )
