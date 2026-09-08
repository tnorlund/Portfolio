"""Tests for configuration failures exposed by client factories."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from receipt_agent.clients.factory import create_embed_fn
from receipt_agent.exceptions import ReceiptAgentConfigurationError


def test_embedding_factory_raises_configuration_error_for_missing_key() -> (
    None
):
    settings = SimpleNamespace(
        embedding_model="text-embedding-3-small",
        openai_api_key=SecretStr(""),
    )

    with pytest.raises(ReceiptAgentConfigurationError) as exc_info:
        create_embed_fn(settings=settings)

    assert str(exc_info.value) == (
        "OpenAI API key required for embeddings. "
        "Set RECEIPT_AGENT_OPENAI_API_KEY"
    )
