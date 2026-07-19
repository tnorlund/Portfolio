"""Tests for MCP OAuth token-lifetime configuration."""

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).parents[1] / "mcp_auth_gateway.py"
_SPEC = importlib.util.spec_from_file_location(
    "mcp_auth_gateway", _MODULE_PATH
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_token_validity = _MODULE._token_validity


class _Config:
    def __init__(self, values: dict[str, int]) -> None:
        self._values = values

    def get_int(self, key: str) -> int | None:
        return self._values.get(key)


def test_token_validity_uses_secure_defaults() -> None:
    assert _token_validity(_Config({})) == (30, 1)


def test_token_validity_accepts_dev_convenience_values() -> None:
    config = _Config(
        {
            "mcpOAuthRefreshTokenValidityDays": 365,
            "mcpOAuthAccessTokenValidityHours": 24,
        }
    )

    assert _token_validity(config) == (365, 24)


@pytest.mark.parametrize(
    "values",
    [
        {"mcpOAuthRefreshTokenValidityDays": 0},
        {"mcpOAuthRefreshTokenValidityDays": 3651},
        {"mcpOAuthAccessTokenValidityHours": 0},
        {"mcpOAuthAccessTokenValidityHours": 25},
    ],
)
def test_token_validity_rejects_out_of_range_values(
    values: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        _token_validity(_Config(values))
