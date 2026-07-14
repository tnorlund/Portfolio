"""Unit tests for atomic snapshot version identifiers."""

import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from receipt_chroma.s3.snapshot import _generate_snapshot_version_id


@pytest.mark.unit
def test_snapshot_version_ids_are_unique_at_same_instant():
    """Concurrent uploads cannot overwrite the same timestamped prefix."""
    fixed_time = datetime(2026, 7, 14, 12, 34, 56, 123456, timezone.utc)

    with patch("receipt_chroma.s3.snapshot.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_time
        first = _generate_snapshot_version_id()
        second = _generate_snapshot_version_id()

    pattern = r"^20260714_123456_123456_[0-9a-f]{8}$"
    assert re.match(pattern, first)
    assert re.match(pattern, second)
    assert first != second
