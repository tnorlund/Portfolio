"""Tests for lazy Chroma initialization on both MCP server implementations.

The two servers (stdio `scripts/receipt_mcp_server.py` and the Lambda
`infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`) must start and
serve every Dynamo-backed tool with NO Chroma Cloud credentials; only the
vector-search tools (CHROMA_TOOLS) may fail, and they must fail at CALL time
with a structured `chroma_not_configured` error instead of crashing the
server. When Chroma IS configured, behavior is unchanged.

No network: Dynamo/Chroma clients are stubbed, and the factory modules are
replaced in sys.modules where construction is exercised.
"""

import asyncio
import json
import sys
import types

import pytest

from test_receipt_mcp_section_tools import SERVER_FILES, _load_module

EXPECTED_CHROMA_TOOLS = {
    "search_receipts",
    "list_all_receipts",
    "search_product_lines",
    "validate_word_similarity",
}

CONFIG_WITHOUT_CHROMA = {
    "dynamodb_table_name": "ReceiptsTable-test",
}

CONFIG_WITH_CHROMA = {
    "dynamodb_table_name": "ReceiptsTable-test",
    "chroma_cloud_enabled": "true",
    "chroma_cloud_api_key": "ck-test-key",
    "chroma_cloud_tenant": "tenant-1",
    "chroma_cloud_database": "receipt_test",
}


class _StubDynamoClient:
    """Minimal Dynamo client for the list_merchants happy path."""

    def list_receipt_places(self, limit, last_evaluated_key=None):
        place = types.SimpleNamespace(merchant_name="Costco Wholesale")
        return [place], None


class _FakeChromaClient:
    """Records constructor kwargs; never touches the network."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _install_client_factories(monkeypatch, module):
    """Replace receipt_chroma / receipt_agent factory imports with fakes.

    The server imports these lazily inside get_dynamo_client /
    get_chroma_clients, so sys.modules injection is enough.
    """
    chroma_mod = types.ModuleType("receipt_chroma")
    chroma_mod.ChromaClient = _FakeChromaClient

    factory_mod = types.ModuleType("receipt_agent.clients.factory")
    factory_mod.create_dynamo_client = lambda table_name: types.SimpleNamespace(
        table_name=table_name
    )
    factory_mod.create_embed_fn = lambda: (lambda texts: [[0.0] for _ in texts])

    agent_mod = types.ModuleType("receipt_agent")
    clients_mod = types.ModuleType("receipt_agent.clients")
    agent_mod.clients = clients_mod
    clients_mod.factory = factory_mod

    monkeypatch.setitem(sys.modules, "receipt_chroma", chroma_mod)
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
def test_chroma_tools_set_matches_expected(label):
    module = _load_module(label, SERVER_FILES[label])
    assert set(module.CHROMA_TOOLS) == EXPECTED_CHROMA_TOOLS

    # Every CHROMA_TOOL is a real registered tool.
    tool_names = {t.name for t in asyncio.run(module.list_tools())}
    assert EXPECTED_CHROMA_TOOLS <= tool_names


def test_both_servers_agree_on_chroma_tools():
    stdio = _load_module("stdio", SERVER_FILES["stdio"])
    lam = _load_module("lambda", SERVER_FILES["lambda"])
    assert set(stdio.CHROMA_TOOLS) == set(lam.CHROMA_TOOLS)


# ---------------------------------------------------------------------------
# Startup / init without Chroma env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_dynamo_client_initializes_without_chroma_config(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch, module)
    module._config = dict(CONFIG_WITHOUT_CHROMA)

    client = module.get_dynamo_client()
    assert client.table_name == "ReceiptsTable-test"
    # Cached for subsequent calls.
    assert module.get_dynamo_client() is client


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_get_chroma_clients_raises_structured_error(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch, module)
    module._config = dict(CONFIG_WITHOUT_CHROMA)

    with pytest.raises(module.ChromaNotConfiguredError) as excinfo:
        module.get_chroma_clients()
    assert "Chroma Cloud not configured" in str(excinfo.value)
    assert "CHROMA_CLOUD_API_KEY" in str(excinfo.value)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_chroma_is_configured_truth_table(label):
    module = _load_module(label, SERVER_FILES[label])
    assert module.chroma_is_configured(CONFIG_WITH_CHROMA) is True
    assert module.chroma_is_configured(CONFIG_WITHOUT_CHROMA) is False
    assert (
        module.chroma_is_configured(
            {"chroma_cloud_enabled": "true", "chroma_cloud_api_key": ""}
        )
        is False
    )
    assert (
        module.chroma_is_configured(
            {"chroma_cloud_enabled": "false", "chroma_cloud_api_key": "k"}
        )
        is False
    )


# ---------------------------------------------------------------------------
# call_tool routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_dynamo_tool_works_without_chroma(label, monkeypatch):
    """A Dynamo-backed tool must succeed and never touch Chroma init."""
    module = _load_module(label, SERVER_FILES[label])
    module.get_dynamo_client = lambda: _StubDynamoClient()

    def _boom():
        raise AssertionError(
            "get_chroma_clients must not be called for a Dynamo tool"
        )

    module.get_chroma_clients = _boom

    result = _tool_result(module, "list_merchants", {})
    assert "error" not in result
    assert result["merchants"][0]["merchant"] == "Costco Wholesale"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
@pytest.mark.parametrize("tool_name", sorted(EXPECTED_CHROMA_TOOLS))
def test_chroma_tool_returns_structured_error(label, tool_name, monkeypatch):
    """Every Chroma tool fails cleanly at call time when unconfigured."""
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch, module)
    module._config = dict(CONFIG_WITHOUT_CHROMA)

    args = {
        "search_receipts": {"query": "COFFEE"},
        "list_all_receipts": {},
        "search_product_lines": {"query": "MILK"},
        "validate_word_similarity": {
            "image_id": "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8",
            "receipt_id": 1,
            "line_id": 1,
            "word_id": 1,
            "label": "GRAND_TOTAL",
        },
    }[tool_name]

    result = _tool_result(module, tool_name, args)
    assert result["error_type"] == "chroma_not_configured"
    assert result["tool"] == tool_name
    assert "Chroma Cloud not configured" in result["error"]


# ---------------------------------------------------------------------------
# Configured path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_configured_path_builds_chroma_client(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch, module)
    module._config = dict(CONFIG_WITH_CHROMA)

    dynamo_client, chroma_client, embed_fn = module.get_clients()
    assert dynamo_client.table_name == "ReceiptsTable-test"
    assert isinstance(chroma_client, _FakeChromaClient)
    assert chroma_client.kwargs == {
        "cloud_api_key": "ck-test-key",
        "cloud_tenant": "tenant-1",
        "cloud_database": "receipt_test",
        "mode": "read",
    }
    assert callable(embed_fn)

    # Cached: same objects on repeat and via the granular getters.
    assert module.get_clients() == (dynamo_client, chroma_client, embed_fn)
    assert module.get_chroma_clients() == (chroma_client, embed_fn)


def _install_fake_pulumi(monkeypatch, secrets):
    """Replace receipt_dynamo.data._pulumi so _load_config needs no CLI."""
    pulumi_mod = types.ModuleType("receipt_dynamo.data._pulumi")
    pulumi_mod.load_env = lambda env: {"dynamodb_table_name": "ReceiptsTable-test"}
    pulumi_mod.load_secrets = lambda env: dict(secrets)

    dynamo_pkg = types.ModuleType("receipt_dynamo")
    data_pkg = types.ModuleType("receipt_dynamo.data")
    dynamo_pkg.data = data_pkg
    data_pkg._pulumi = pulumi_mod

    monkeypatch.setitem(sys.modules, "receipt_dynamo", dynamo_pkg)
    monkeypatch.setitem(sys.modules, "receipt_dynamo.data", data_pkg)
    monkeypatch.setitem(sys.modules, "receipt_dynamo.data._pulumi", pulumi_mod)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_env_var_can_disable_chroma(label, monkeypatch):
    """CHROMA_CLOUD_ENABLED=false in the environment must override config
    that has full Chroma creds (Dynamo-only mode)."""
    module = _load_module(label, SERVER_FILES[label])
    _install_client_factories(monkeypatch, module)
    _install_fake_pulumi(
        monkeypatch,
        secrets={
            "portfolio:CHROMA_CLOUD_ENABLED": "true",
            "portfolio:CHROMA_CLOUD_API_KEY": "ck-test-key",
            "portfolio:CHROMA_CLOUD_TENANT": "tenant-1",
            "portfolio:CHROMA_CLOUD_DATABASE": "receipt_test",
        },
    )
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    monkeypatch.setenv("CHROMA_CLOUD_ENABLED", "false")
    for key in (
        "CHROMA_CLOUD_API_KEY",
        "CHROMA_CLOUD_TENANT",
        "CHROMA_CLOUD_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)

    config = module._load_config()
    assert config["chroma_cloud_enabled"] == "false"
    assert module.chroma_is_configured(config) is False

    # Dynamo still initializes; Chroma errors cleanly.
    assert module.get_dynamo_client().table_name == "ReceiptsTable-test"
    with pytest.raises(module.ChromaNotConfiguredError):
        module.get_chroma_clients()
