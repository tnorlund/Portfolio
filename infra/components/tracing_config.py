"""Optional hosted tracing configuration shared by deployed agents."""

from typing import Any


def hosted_tracing_environment(config: Any) -> dict[str, Any]:
    """Disable uploads by default; require a key only for explicit opt-in."""
    enabled = config.get_bool("LANGSMITH_TRACING_ENABLED") is True
    environment = {
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
