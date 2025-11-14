"""
End-to-end tests for receipt_layoutlm inference.

These require real AWS credentials and network access.
"""

import sys

import pytest


def pytest_configure(config):
    if config.getoption("markexpr") and "end_to_end" in config.getoption(
        "markexpr"
    ):
        print("\n" + "=" * 70)
        print("⚠️  WARNING: Running END-TO-END tests for receipt_layoutlm")
        print("These tests will access real AWS services and may incur costs.")
        print("=" * 70 + "\n")


@pytest.fixture(scope="session", autouse=True)
def check_aws_credentials():
    try:
        import boto3

        client = boto3.client("sts", region_name="us-east-1")
        client.get_caller_identity()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"AWS credentials not available: {e}")


@pytest.fixture(scope="session")
def skip_if_not_darwin():
    if sys.platform != "darwin":
        pytest.skip("Inference e2e currently validated on macOS only")
    return True
