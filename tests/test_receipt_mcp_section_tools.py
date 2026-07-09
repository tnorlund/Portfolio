"""Tests for the ReceiptSection QA tools on both MCP server implementations.

The two servers (stdio `scripts/receipt_mcp_server.py` and the Lambda
`infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`) must expose the
same tool surface. These tests import each module with a minimal fake `mcp`
package (the real dependency is not installed in CI) and assert the four
section tools are registered with valid input schemas and matching impls.
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SERVER_FILES = {
    "stdio": REPO_ROOT / "scripts" / "receipt_mcp_server.py",
    "lambda": (
        REPO_ROOT
        / "infra"
        / "mcp_server_lambda"
        / "lambdas"
        / "receipt_mcp_server_server.py"
    ),
}

EXPECTED_SECTION_TOOLS = {
    "get_receipt_sections",
    "update_section_status",
    "create_receipt_section",
    "delete_receipt_section",
}


class _FakeTool:
    """Stand-in for mcp.types.Tool that just records its fields."""

    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


def _install_mcp_stubs():
    """Register a minimal fake `mcp` package so the servers import cleanly."""
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    stdio_mod = types.ModuleType("mcp.server.stdio")
    types_mod = types.ModuleType("mcp.types")

    class _FakeServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            def decorator(func):
                return func

            return decorator

        def call_tool(self):
            def decorator(func):
                return func

            return decorator

    def _fake_stdio_server(*args, **kwargs):  # pragma: no cover - unused
        raise RuntimeError("stdio_server is not exercised in tests")

    server_mod.Server = _FakeServer
    stdio_mod.stdio_server = _fake_stdio_server
    types_mod.Tool = _FakeTool
    types_mod.TextContent = object

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.stdio"] = stdio_mod
    sys.modules["mcp.types"] = types_mod


def _load_module(label, path):
    _install_mcp_stubs()
    spec = importlib.util.spec_from_file_location(
        f"receipt_mcp_server_{label}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_section_tools_present_with_valid_schema(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = asyncio.run(module.list_tools())
    by_name = {t.name: t for t in tools}

    missing = EXPECTED_SECTION_TOOLS - set(by_name)
    assert not missing, f"missing section tools in {label}: {missing}"

    for name in EXPECTED_SECTION_TOOLS:
        tool = by_name[name]
        schema = tool.inputSchema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
        assert isinstance(schema.get("properties"), dict)
        assert "image_id" in schema["properties"]
        assert "receipt_id" in schema["properties"]
        assert isinstance(schema.get("required"), list)
        assert "image_id" in schema["required"]
        assert "receipt_id" in schema["required"]
        assert isinstance(tool.description, str) and tool.description.strip()

    # get_receipt_sections is read-only: exactly the two identity keys.
    assert set(by_name["get_receipt_sections"].inputSchema["required"]) == {
        "image_id",
        "receipt_id",
    }

    # create/update/delete carry the section payload.
    assert {"section_type", "line_ids"} <= set(
        by_name["create_receipt_section"].inputSchema["required"]
    )
    assert {"section_type", "validation_status"} <= set(
        by_name["update_section_status"].inputSchema["required"]
    )
    assert "section_type" in set(
        by_name["delete_receipt_section"].inputSchema["required"]
    )


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_section_tool_impls_exist(label):
    module = _load_module(label, SERVER_FILES[label])
    for name in EXPECTED_SECTION_TOOLS:
        impl = getattr(module, f"{name}_impl", None)
        assert callable(impl), f"missing {name}_impl in {label} server"


def test_both_servers_expose_identical_section_tool_shape():
    stdio = _load_module("stdio", SERVER_FILES["stdio"])
    lam = _load_module("lambda", SERVER_FILES["lambda"])

    def shape(module):
        tools = asyncio.run(module.list_tools())
        return {
            t.name: (t.description, t.inputSchema)
            for t in tools
            if t.name in EXPECTED_SECTION_TOOLS
        }

    assert shape(stdio) == shape(lam)
