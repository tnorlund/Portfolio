"""Startup cannot silently choose an AWS environment."""

from unittest.mock import Mock

import pytest

from planner.errors import ValidationError
from planner.runtime import configured_planner


@pytest.mark.parametrize(
    "env,endpoint",
    [
        (None, None),
        ("prod", None),
        ("local", None),
        ("local", "https://dynamodb.us-east-1.amazonaws.com"),
        ("local", "http://example.com:8317"),
        ("dev", None),
    ],
)
def test_unsafe_or_implicit_config_fails_before_aws(
    monkeypatch, env, endpoint
):
    monkeypatch.delenv("PLANNER_ALLOW_DEV", raising=False)
    monkeypatch.setenv("PLANNER_TABLE", "PlannerTest")
    for key, value in [("PLANNER_ENV", env), ("PLANNER_ENDPOINT", endpoint)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    client = Mock()
    monkeypatch.setattr("planner.runtime.boto3.client", client)
    with pytest.raises(ValidationError):
        configured_planner()
    client.assert_not_called()
