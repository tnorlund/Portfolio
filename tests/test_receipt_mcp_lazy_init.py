"""Tests for lazy client initialization on both MCP server implementations.

The two servers (stdio ``scripts/receipt_mcp_server.py`` and the Lambda
``infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py``) must
start and serve every Dynamo-backed tool without an OpenAI key; only the
vector-search tools (VECTOR_TOOLS) build the embedding function, and they
do so lazily at CALL time.

No network: the Dynamo client and the embedding factory are stubbed, and
the factory modules are replaced in sys.modules where construction is
exercised.
"""

import asyncio
import json
import sys
import types

import pytest
from test_receipt_mcp_section_tools import SERVER_FILES, _load_module

EXPECTED_VECTOR_TOOLS = {"search_receipts", "search_product_lines"}

CONFIG = {"dynamodb_table_name": "ReceiptsTable-test"}


class _StubDynamoClient:
    """Minimal Dynamo client for the list_merchants happy path."""

    def list_receipt_places(self, limit, last_evaluated_key=None):
        place = types.SimpleNamespace(merchant_name="Costco Wholesale")
        return [place], None


class _StubVectorClient:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, vector, index, top_k, filters=None):
        del vector
        self.calls.append((index, top_k, filters))
        return self.results


def _install_client_factories(monkeypatch):
    """Replace the receipt_agent factory imports with fakes.

    The server imports these lazily inside get_dynamo_client /
    get_embed_fn, so sys.modules injection is enough.
    """
    factory_mod = types.ModuleType("receipt_agent.clients.factory")
    factory_mod.create_dynamo_client = (
        lambda table_name: types.SimpleNamespace(table_name=table_name)
    )
    factory_mod.create_embed_fn = lambda: (
        lambda texts: [[1.0, 0.0] for _ in texts]
    )

    agent_mod = types.ModuleType("receipt_agent")
    clients_mod = types.ModuleType("receipt_agent.clients")
    agent_mod.clients = clients_mod
    clients_mod.factory = factory_mod

    monkeypatch.setitem(sys.modules, "receipt_agent", agent_mod)
    monkeypatch.setitem(sys.modules, "receipt_agent.clients", clients_mod)
    monkeypatch.setitem(
        sys.modules, "receipt_agent.clients.factory", factory_mod
    )


def _tool_result(module, name, arguments):
    content = asyncio.run(module.call_tool(name, arguments))
    assert content, "call_tool returned no content"
    return json.loads(content[0].text)


# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_vector_tools_set_matches_expected(label):
    module = _load_module(label, SERVER_FILES[label])
    assert set(module.VECTOR_TOOLS) == EXPECTED_VECTOR_TOOLS

    # Every VECTOR_TOOL is a real registered tool.
    tool_names = {t.name for t in asyncio.run(module.list_tools())}
    assert EXPECTED_VECTOR_TOOLS <= tool_names


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_retired_list_all_receipts_tool_is_gone(label):
    module = _load_module(label, SERVER_FILES[label])
    tool_names = {t.name for t in asyncio.run(module.list_tools())}
    assert "list_all_receipts" not in tool_names


def test_both_servers_agree_on_vector_tools():
    stdio = _load_module("stdio", SERVER_FILES["stdio"])
    lam = _load_module("lambda", SERVER_FILES["lambda"])
    assert set(stdio.VECTOR_TOOLS) == set(lam.VECTOR_TOOLS)


# ---------------------------------------------------------------------------
# Startup / init
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_dynamo_client_initializes_and_caches(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch)
    module._config = dict(CONFIG)

    client = module.get_dynamo_client()
    assert client.table_name == "ReceiptsTable-test"
    assert module.get_dynamo_client() is client


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_embed_fn_is_lazy_and_cached(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch)
    module._config = dict(CONFIG)

    assert module._embed_fn is None
    embed_fn = module.get_embed_fn()
    assert embed_fn(["a"]) == [[1.0, 0.0]]
    assert module.get_embed_fn() is embed_fn


# ---------------------------------------------------------------------------
# call_tool routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_dynamo_tool_never_builds_embedder(label):
    """A Dynamo-backed tool must succeed without touching OpenAI."""
    module = _load_module(label, SERVER_FILES[label])
    module.get_dynamo_client = lambda: _StubDynamoClient()

    def _boom():
        raise AssertionError(
            "get_embed_fn must not be called for a Dynamo tool"
        )

    module.get_embed_fn = _boom

    result = _tool_result(module, "list_merchants", {})
    assert "error" not in result
    assert result["merchants"][0]["merchant"] == "Costco Wholesale"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_vector_tool_builds_embedder_and_uses_seam(label):
    module = _load_module(label, SERVER_FILES[label])
    module.get_dynamo_client = lambda: types.SimpleNamespace()
    calls = []

    def _embed():
        calls.append("embed")
        return lambda texts: [[1.0, 0.0] for _ in texts]

    module.get_embed_fn = _embed
    stub = _StubVectorClient(
        results=[
            types.SimpleNamespace(
                key="",
                distance=0.2,
                metadata={
                    "image_id": "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8",
                    "receipt_id": 1,
                    "text": "ORGANIC COFFEE 12.99",
                    "merchant_name": "Sprouts",
                },
            )
        ]
    )
    module.get_vector_search_client = lambda: stub

    result = _tool_result(module, "search_receipts", {"query": "coffee"})

    assert calls == ["embed"]
    assert "error" not in result
    assert result["search_type"] == "semantic"
    assert stub.calls and stub.calls[0][0] == "line-embeddings"
