"""
Configuration for end-to-end tests.

⚠️ WARNING: These tests require real AWS services and credentials!
Do not run these tests in CI or automated environments without proper setup.
"""

import os
import sys

import pytest


def pytest_configure(config):
    """Add warning when running end-to-end tests."""
    if config.getoption("markexpr") and "end_to_end" in config.getoption("markexpr"):
        print("\n" + "=" * 70)
        print("⚠️  WARNING: Running END-TO-END tests")
        print("These tests will:")
        print("  - Connect to REAL AWS services")
        print("  - Require valid AWS credentials")
        print("  - May incur AWS costs")
        print("  - Require internet connectivity")
        print("=" * 70 + "\n")


@pytest.fixture(scope="session", autouse=True)
def check_aws_credentials():
    """Check if AWS credentials are available before running e2e tests."""
    # This will run for all tests in this directory
    # boto3 will handle the actual credential resolution
    try:
        import boto3

        # Try to create a client to verify credentials are available
        client = boto3.client("sts", region_name="us-east-1")
        client.get_caller_identity()
    except Exception as e:
        pytest.skip(f"AWS credentials not available: {e}")


@pytest.fixture(scope="session", autouse=True)
def skip_ocr_on_non_mac():
    """Skip OCR tests on non-macOS platforms."""
    if sys.platform != "darwin":
        # This will be checked in individual tests that need macOS
        pass  # Let individual tests handle the skip
