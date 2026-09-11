"""Optional hosted tracing configuration shared by deployed agents."""

from typing import Protocol

from pulumi import Input, Output


class TracingConfig(Protocol):
    """Configuration methods needed to enable optional hosted tracing."""

    def get_bool(self, key: str) -> bool | None:
        """Read an optional boolean setting."""

    def get(self, key: str) -> str | None:
        """Read an optional text setting."""

    def require_secret(self, key: str) -> Output[str]:
        """Read a required secret without revealing its value."""


def hosted_tracing_environment(
    config: TracingConfig,
) -> dict[str, Input[str]]:
    """Disable uploads by default; require a key only for explicit opt-in."""
    enabled = config.get_bool("LANGSMITH_TRACING_ENABLED") is True
    environment: dict[str, Input[str]] = {
        "LANGCHAIN_TRACING_V2": str(enabled).lower(),
        "LANGSMITH_TRACING": str(enabled).lower(),
    }
    if enabled:
        rate = float(config.get("LANGSMITH_TRACING_SAMPLING_RATE") or "0.1")
        if not 0 <= rate <= 1:
            raise ValueError(
                "LANGSMITH_TRACING_SAMPLING_RATE must be between 0 and 1"
            )
        environment["LANGCHAIN_API_KEY"] = config.require_secret(
            "LANGCHAIN_API_KEY"
        )
        environment["LANGSMITH_TRACING_SAMPLING_RATE"] = str(rate)
    return environment
