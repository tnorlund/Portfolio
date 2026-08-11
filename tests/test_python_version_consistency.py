"""Tests for the repository-wide Python runtime baseline."""

from scripts.check_python_version_consistency import check_repository


def test_python_version_declarations_are_consistent() -> None:
    """Every active package and deployment target should use Python 3.13."""
    assert check_repository() == []
