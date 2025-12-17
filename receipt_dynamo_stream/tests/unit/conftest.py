"""Shared fixtures for receipt_dynamo_stream unit tests."""

from typing import Mapping, Optional

import pytest


class MockMetrics:
    """Mock metrics recorder for testing."""

    def __init__(self) -> None:
        self.counts: list[tuple[str, int, Optional[Mapping[str, str]]]] = []

    def count(
        self,
        name: str,
        value: int,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.counts.append((name, value, dimensions))


@pytest.fixture
def mock_metrics() -> MockMetrics:
    """Provide a MockMetrics instance for testing."""
    return MockMetrics()


__all__ = ["MockMetrics", "mock_metrics"]
