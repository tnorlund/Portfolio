"""Provider opt-in is independent of embedding and general agent config."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location(
    "receipt_validation_config",
    Path(__file__).with_name("receipt_validation_config.py"),
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Config:
    def __init__(self, **values: Any) -> None:
        self.values = values
        self.secret_reads: list[str] = []

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def require(self, key: str) -> Any:
        return self.values[key]

    def require_secret(self, key: str) -> Any:
        self.secret_reads.append(key)
        return self.require(key)


def test_default_does_not_require_or_inject_anthropic_secret() -> None:
    config = Config()
    assert module.receipt_validation_environment(config) == {
        "RECEIPT_VALIDATION_PROVIDER": "openrouter"
    }
    assert not config.secret_reads


def test_opt_in_preserves_secret_output_and_requires_explicit_model() -> None:
    secret = object()
    config = Config(
        RECEIPT_VALIDATION_PROVIDER="anthropic",
        RECEIPT_ANTHROPIC_MODEL="claude-sonnet-5-5",
        ANTHROPIC_API_KEY=secret,
    )
    result = module.receipt_validation_environment(config)
    assert result["ANTHROPIC_API_KEY"] is secret
    assert result["RECEIPT_ANTHROPIC_MAX_TOKENS"] == "8192"
    assert "OPENAI_API_KEY" not in result
    assert "OPENROUTER_MODEL" not in result
    assert config.secret_reads == ["ANTHROPIC_API_KEY"]


@pytest.mark.parametrize(
    "missing", ["ANTHROPIC_API_KEY", "RECEIPT_ANTHROPIC_MODEL"]
)
def test_missing_opt_in_config_fails(missing: str) -> None:
    values = {
        "RECEIPT_VALIDATION_PROVIDER": "anthropic",
        "RECEIPT_ANTHROPIC_MODEL": "claude-sonnet-5-5",
        "ANTHROPIC_API_KEY": object(),
    }
    del values[missing]
    with pytest.raises(KeyError):
        module.receipt_validation_environment(Config(**values))


def test_unknown_provider_fails() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        module.receipt_validation_environment(
            Config(RECEIPT_VALIDATION_PROVIDER="typo")
        )
