"""Tests for configuration failures exposed by client factories."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
from receipt_agent.exceptions import ReceiptAgentConfigurationError


def test_chroma_factory_raises_configuration_error_with_options() -> None:
    settings = SimpleNamespace(
        chroma_persist_directory=None,
        chroma_http_url=None,
    )

    with (
        patch.dict(
            "os.environ",
            {},
            clear=True,
        ),
        patch(
            "receipt_agent.clients.factory.ChromaClient",
            MagicMock(),
        ),
        pytest.raises(ReceiptAgentConfigurationError) as exc_info,
    ):
        create_chroma_client(settings=settings)

    assert str(exc_info.value) == (
        "No ChromaDB backend configured. Set CHROMA_CLOUD_API_KEY, "
        "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY, "
        "RECEIPT_AGENT_CHROMA_LINES_DIRECTORY + "
        "RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"
    )
    assert isinstance(exc_info.value, ValueError)


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
