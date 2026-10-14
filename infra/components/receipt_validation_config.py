"""Opt-in receipt-validator wiring; does not change general LLM/embedding use."""

from typing import Any


def receipt_validation_environment(config: Any) -> dict[str, Any]:
    """Resolve only declared config; missing direct-provider settings fail."""
    provider = (
        config.get("RECEIPT_VALIDATION_PROVIDER") or "openrouter"
    ).strip()
    if provider not in {"openrouter", "anthropic"}:
        raise ValueError("Unsupported RECEIPT_VALIDATION_PROVIDER")
    environment: dict[str, Any] = {"RECEIPT_VALIDATION_PROVIDER": provider}
    if provider == "anthropic":
        model = config.require("RECEIPT_ANTHROPIC_MODEL").strip()
        if not model:
            raise ValueError("RECEIPT_ANTHROPIC_MODEL must not be empty")
        max_tokens = int(config.get("RECEIPT_ANTHROPIC_MAX_TOKENS") or "8192")
        if not 1 <= max_tokens <= 16384:
            raise ValueError("Anthropic output limit must be 1..16384")
        environment.update(
            ANTHROPIC_API_KEY=config.require_secret("ANTHROPIC_API_KEY"),
            RECEIPT_ANTHROPIC_MODEL=model,
            RECEIPT_ANTHROPIC_MAX_TOKENS=str(max_tokens),
        )
    return environment
