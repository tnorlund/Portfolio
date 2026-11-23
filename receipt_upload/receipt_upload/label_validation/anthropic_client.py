"""Direct Anthropic transport for receipt labels; never selects a fallback."""

import hashlib
import json
import os
import time
from typing import Any

from anthropic import Anthropic, APIError
from pydantic import BaseModel, ValidationError


class AnthropicValidationError(RuntimeError):
    """No decisions may be applied; deferred work must remain retryable."""


class AnthropicLabelClient:
    """Use the Messages JSON schema API with explicit credentials and limits."""

    def __init__(self, schema: type[BaseModel], timeout: int = 120) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.model = os.environ.get("RECEIPT_ANTHROPIC_MODEL", "").strip()
        if not api_key or not self.model:
            raise AnthropicValidationError(
                "Direct validation requires ANTHROPIC_API_KEY and "
                "RECEIPT_ANTHROPIC_MODEL"
            )
        # Do not let SDK environment defaults substitute a subscription token
        # or a gateway. Credits apply only to the direct API.
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise AnthropicValidationError(
                "Subscription/bearer tokens are not supported for validation"
            )
        if os.environ.get("ANTHROPIC_CUSTOM_HEADERS"):
            raise AnthropicValidationError(
                "Custom Anthropic headers are not supported for validation"
            )
        self.schema = schema
        self.max_tokens = int(
            os.environ.get("RECEIPT_ANTHROPIC_MAX_TOKENS", "8192")
        )
        if not 1 <= self.max_tokens <= 16384:
            raise AnthropicValidationError(
                "RECEIPT_ANTHROPIC_MAX_TOKENS must be between 1 and 16384"
            )
        self.client = Anthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",
            timeout=timeout,
            max_retries=2,
        )
        self.last_call: dict[str, Any] = {}

    def invoke(self, prompt: str, label_count: int) -> BaseModel:
        """Reject refusals, truncation, malformed output and partial coverage."""
        schema = self.schema.model_json_schema()
        request = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "service_tier": "standard_only",
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "format": {"type": "json_schema", "schema": schema}
            },
        }
        fingerprint = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        self.last_call = {
            "provider": "anthropic",
            "requested_model": self.model,
            "request_fingerprint": fingerprint,
            "max_tokens": self.max_tokens,
            "status": "error",
        }
        started = time.monotonic()
        try:
            message = self.client.messages.create(**request)
        except APIError as error:
            # SDK errors may contain the request body; never put it in logs.
            self.last_call["error_type"] = type(error).__name__
            raise AnthropicValidationError(
                f"Anthropic request failed ({type(error).__name__})"
            ) from None
        finally:
            self.last_call["latency_ms"] = round(
                (time.monotonic() - started) * 1000
            )
        self.last_call.update(
            model=message.model,
            message_id=message.id,
            request_id=message._request_id,
            stop_reason=message.stop_reason,
            usage=message.usage.model_dump(),
        )
        if message.stop_reason != "end_turn":
            raise AnthropicValidationError(
                f"Anthropic response incomplete ({message.stop_reason})"
            )
        text_blocks = [b.text for b in message.content if b.type == "text"]
        if len(text_blocks) != 1:
            raise AnthropicValidationError("Expected one JSON text block")
        try:
            response = self.schema.model_validate_json(
                text_blocks[0], strict=True
            )
        except ValidationError:
            raise AnthropicValidationError(
                "Anthropic response failed label schema validation"
            ) from None
        indexes = [d.index for d in response.decisions]
        if sorted(indexes) != list(range(label_count)):
            raise AnthropicValidationError(
                "Anthropic response must cover every label exactly once"
            )
        self.last_call["status"] = "success"
        return response

    def get_stats(self) -> dict[str, Any]:
        """Return receipt-free provenance, including billed token usage."""
        return dict(self.last_call)
