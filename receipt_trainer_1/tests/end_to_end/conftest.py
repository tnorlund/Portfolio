"""Fixtures for end-to-end tests."""

import os
import boto3
import pytest
from typing import Dict, Any
from receipt_trainer.utils.pulumi import get_auto_scaling_config


@pytest.fixture
def aws_credentials():
    """Fixture for AWS credentials.

    This fixture returns the default boto3 session, ensuring that
    AWS credentials are available for the test.
    """
    # Verify AWS credentials are available
    session = boto3.Session()
    try:
        # Test if we can make a simple AWS API call
        sts = session.client("sts")
        sts.get_caller_identity()
    except Exception as e:
        pytest.skip(f"AWS credentials not available: {e}")

    return session


@pytest.fixture
def pulumi_config() -> Dict[str, Any]:
    """Fixture for Pulumi configuration.

    This fixture returns a dictionary of Pulumi stack outputs for the current stack.
    """
    try:
        stack_name = os.environ.get("PULUMI_STACK", "dev")
        return get_auto_scaling_config(stack_name)
    except Exception as e:
        pytest.skip(f"Pulumi config not available: {e}")
        return {}
