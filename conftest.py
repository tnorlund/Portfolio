"""
Global pytest configuration and optimizations.
This file is automatically loaded by pytest for all test runs.
"""

import pytest
import warnings
import logging
from _pytest.config import Config
from _pytest.nodes import Item


def pytest_configure(config: Config) -> None:
    """Configure pytest with optimizations."""
    
    # Add custom markers if not already defined
    markers = [
        "unit: Unit tests (fast, isolated)",
        "integration: Integration tests (may require external resources)",
        "end_to_end: End-to-end tests (requires AWS resources)",
        "slow: Slow tests that should be skipped in quick runs",
        "flaky: Tests that may fail intermittently",
    ]
    
    for marker in markers:
        config.addinivalue_line("markers", marker)
    
    # Suppress common warnings in tests
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=".*urllib3.*")
    
    # Configure logging to reduce noise
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Modify test collection for optimization."""
    
    # Automatically mark tests based on their path
    for item in items:
        # Mark tests in specific directories
        if "end_to_end" in str(item.fspath):
            item.add_marker(pytest.mark.end_to_end)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        
        # Mark slow tests based on name patterns
        if any(pattern in item.name for pattern in ["test_large", "test_heavy", "test_stress"]):
            item.add_marker(pytest.mark.slow)
    
    # Handle --quick option
    if config.getoption("--quick"):
        # Only run tests marked as unit and not slow
        selected_items = []
        deselected_items = []
        
        for item in items:
            if item.get_closest_marker("unit") and not item.get_closest_marker("slow"):
                selected_items.append(item)
            else:
                deselected_items.append(item)
        
        config.hook.pytest_deselected(items=deselected_items)
        items[:] = selected_items


def pytest_runtest_setup(item: Item) -> None:
    """Setup for each test with optimizations."""
    
    # Skip tests that require specific environments
    if item.get_closest_marker("requires_aws"):
        if not item.config.getoption("--run-aws-tests", default=False):
            pytest.skip("Skipping AWS tests (use --run-aws-tests to run)")


# Configure pytest-xdist for optimal performance
def pytest_configure_node(node) -> None:
    """Configure xdist worker nodes."""
    # Each worker gets its own test database/resources to avoid conflicts
    node.workerinput["workerid"] = node.gateway.id


# Add custom command line options
def pytest_addoption(parser) -> None:
    """Add custom command line options."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests"
    )
    parser.addoption(
        "--run-aws-tests",
        action="store_true",
        default=False,
        help="Run tests that require AWS credentials"
    )
    parser.addoption(
        "--quick",
        action="store_true",
        default=False,
        help="Run only quick unit tests"
    )


