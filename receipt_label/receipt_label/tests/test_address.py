import pytest
from receipt_label.utils.address import (
    compare_addresses,
    format_address,
    normalize_address,
    parse_address,
)


@pytest.mark.unit
class TestAddressUtils:
    def test_normalize_address(self):
        """Test address normalization."""
        # Test basic normalization
        assert normalize_address("123 Main St.") == "123 main street"
        assert normalize_address("456 W. Oak Ave") == "456 west oak avenue"

        # Test abbreviation handling
        assert normalize_address("789 N. Blvd") == "789 north boulevard"
        assert (
            normalize_address("321 SE Circle Dr.")
            == "321 southeast circle drive"
        )

        # Test whitespace handling
        assert normalize_address("  100   Pine    St  ") == "100 pine street"

        # Test empty input
        assert normalize_address("") == ""
        assert normalize_address(None) == ""

    def test_parse_address(self):
        """Test address parsing."""
        # Test full address
        full_address = "123 Main St, Suite 4, Boston, MA 02108, USA"
        components = parse_address(full_address)
        assert components["street_number"] == "123"
        assert components["street_name"] == "Main"
        assert components["street_type"] == "St"
        assert components["unit"] == "4"
        assert components["city"] == "Boston"
        assert components["state"] == "MA"
        assert components["zip_code"] == "02108"
        assert components["country"] == "USA"

        # Test address without unit
        simple_address = "456 Oak Ave, Chicago, IL 60601"
        components = parse_address(simple_address)
        assert components["street_number"] == "456"
        assert components["street_name"] == "Oak"
        assert components["street_type"] == "Ave"
        assert components["unit"] is None
        assert components["city"] == "Chicago"
        assert components["state"] == "IL"
        assert components["zip_code"] == "60601"

        # Test empty input
        assert parse_address("") == {}
        assert parse_address(None) == {}

    def test_format_address(self):
        """Test address formatting."""
        # Test full components
        components = {
            "street_number": "123",
            "street_name": "Main",
            "street_type": "Street",
            "unit": "4",
            "city": "Boston",
            "state": "MA",
            "zip_code": "02108",
            "country": "USA",
        }
        expected = "123 Main Street Suite 4, Boston, MA 02108, USA"
        assert format_address(components) == expected

        # Test minimal components
        minimal = {
            "street_number": "456",
            "street_name": "Oak",
            "street_type": "Ave",
            "city": "Chicago",
            "state": "IL",
        }
        assert format_address(minimal) == "456 Oak Ave, Chicago, IL"

        # Test empty components
        assert format_address({}) == ""

    def test_compare_addresses(self):
        """Test address comparison."""
        # Test exact match
        addr1 = "123 Main St, Boston, MA 02108"
        addr2 = "123 Main Street, Boston, MA 02108"
        assert compare_addresses(addr1, addr2) == 1.0

        # Test similar addresses
        addr3 = "123 Main Street Suite 4, Boston, MA 02108"
        assert 0.8 <= compare_addresses(addr1, addr3) < 1.0

        # Test different addresses
        addr4 = "456 Oak Ave, Chicago, IL 60601"
        assert compare_addresses(addr1, addr4) < 0.5

        # Test empty addresses
        assert compare_addresses("", "") == 0.0
        assert compare_addresses(None, None) == 0.0
        assert compare_addresses(addr1, "") == 0.0
