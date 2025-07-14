import os

import pytest


def pytest_runtest_setup(item):
    """Setup for each test - skip performance tests if environment variable is set."""
    if item.get_closest_marker("performance"):
        if os.environ.get("SKIP_PERFORMANCE_TESTS", "").lower() == "true":
            pytest.skip(
                "Skipping performance tests (SKIP_PERFORMANCE_TESTS=true)"
            )
