from datetime import datetime, timedelta

import pytest

from receipt_label.utils.date import (
    extract_datetime,
    format_datetime,
    get_date_range,
    get_time_difference,
    is_valid_date,
    is_valid_time,
    parse_datetime)


@pytest.mark.unit
class TestDateUtils:
    def test_parse_datetime(self):
        """Test datetime parsing."""
        # Test various date formats
        assert parse_datetime("2024-03-15") is not None
        assert parse_datetime("03/15/2024") is not None
        assert parse_datetime("15/03/2024") is not None
        assert parse_datetime("March 15, 2024") is not None

        # Test with time
        dt = parse_datetime("2024-03-15", "14:30")
        assert dt is not None
        assert dt.hour == 14
        assert dt.minute == 30

        # Test with AM/PM time
        dt = parse_datetime("2024-03-15", "2:30 PM")
        assert dt is not None
        assert dt.hour == 14
        assert dt.minute == 30

        # Test invalid inputs
        assert parse_datetime("") is None
        assert parse_datetime("invalid") is None
        assert (
            parse_datetime("2024-03-15", "invalid") is not None
        )  # Should return date only

    def test_extract_datetime(self):
        """Test datetime extraction from text."""
        # Test date only
        result = extract_datetime("Purchase date: 2024-03-15")
        assert result is not None
        assert "2024-03-15" in result["value"]
        assert result["confidence"] == 0.8  # Lower confidence without time

        # Test date and time
        result = extract_datetime("Transaction: 2024-03-15 14:30")
        assert result is not None
        assert "2024-03-15" in result["value"]
        assert "14:30" in result["value"]
        assert result["confidence"] == 0.9  # Higher confidence with time

        # Test with AM/PM
        result = extract_datetime("Receipt: March 15, 2024 2:30 PM")
        assert result is not None
        assert "14:30" in result["value"]

        # Test invalid input
        assert extract_datetime("No date here") is None
        assert extract_datetime("") is None

    def test_format_datetime(self):
        """Test datetime formatting."""
        dt = datetime(2024, 3, 15, 14, 30, 45)

        # Test date only
        assert format_datetime(dt, include_time=False) == "2024-03-15"

        # Test date and time without seconds
        assert (
            format_datetime(dt, include_time=True, include_seconds=False)
            == "2024-03-15 14:30"
        )

        # Test date and time with seconds
        assert (
            format_datetime(dt, include_time=True, include_seconds=True)
            == "2024-03-15 14:30:45"
        )

    def test_is_valid_date(self):
        """Test date validation."""
        # Test valid dates
        assert is_valid_date("2024-03-15")
        assert is_valid_date("03/15/2024")
        assert is_valid_date("15/03/2024")
        assert is_valid_date("March 15, 2024")

        # Test invalid dates
        assert not is_valid_date("")
        assert not is_valid_date("invalid")
        assert not is_valid_date("2024-13-15")  # Invalid month
        assert not is_valid_date("2024-03-32")  # Invalid day

    def test_is_valid_time(self):
        """Test time validation."""
        # Test valid times
        assert is_valid_time("14:30")
        assert is_valid_time("14:30:45")
        assert is_valid_time("2:30 PM")
        assert is_valid_time("02:30 PM")

        # Test invalid times
        assert not is_valid_time("")
        assert not is_valid_time("invalid")
        assert not is_valid_time("25:00")  # Invalid hour
        assert not is_valid_time("14:60")  # Invalid minute

    def test_get_date_range(self):
        """Test date range generation."""
        start = datetime(2024, 3, 15)
        end = datetime(2024, 3, 18)

        dates = get_date_range(start, end)
        assert len(dates) == 4  # 15th, 16th, 17th, 18th
        assert dates[0] == start
        assert dates[-1] == end

        # Test single day
        dates = get_date_range(start, start)
        assert len(dates) == 1
        assert dates[0] == start

    def test_get_time_difference(self):
        """Test time difference calculation."""
        time1 = datetime(2024, 3, 15, 14, 30, 0)

        # Test 1 hour difference
        time2 = datetime(2024, 3, 15, 15, 30, 0)
        hours, minutes, seconds = get_time_difference(time1, time2)
        assert hours == 1
        assert minutes == 0
        assert seconds == 0

        # Test complex difference
        time2 = datetime(2024, 3, 15, 16, 45, 30)
        hours, minutes, seconds = get_time_difference(time1, time2)
        assert hours == 2
        assert minutes == 15
        assert seconds == 30

        # Test zero difference
        hours, minutes, seconds = get_time_difference(time1, time1)
        assert hours == 0
        assert minutes == 0
        assert seconds == 0
