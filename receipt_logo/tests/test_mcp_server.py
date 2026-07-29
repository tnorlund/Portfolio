from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest

from receipt_logo.exceptions import UnknownLogoToolError


def test_call_tool_raises_specific_error_for_unknown_tool(monkeypatch) -> None:
    """Exercise dispatch independently of the MCP transport runtime."""

    class FakeServer:
        def __init__(self, _name: str) -> None:
            pass

        @staticmethod
        def _decorator():
            return lambda function: function

        list_tools = _decorator
        call_tool = _decorator

    class FakeTextContent:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    mcp_module = ModuleType("mcp")
    server_module = ModuleType("mcp.server")
    stdio_module = ModuleType("mcp.server.stdio")
    types_module = ModuleType("mcp.types")
    server_module.Server = FakeServer
    stdio_module.stdio_server = object()
    types_module.TextContent = FakeTextContent
    types_module.Tool = object
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.stdio", stdio_module)
    monkeypatch.setitem(sys.modules, "mcp.types", types_module)
    sys.modules.pop("receipt_logo.mcp_server", None)
    try:
        mcp_server = importlib.import_module("receipt_logo.mcp_server")

        with pytest.raises(UnknownLogoToolError) as raised:
            asyncio.run(mcp_server.call_tool("does_not_exist", {}))

        assert str(raised.value) == (
            "Unknown receipt-logo tool: 'does_not_exist'"
        )
        assert raised.value.__cause__ is None
    finally:
        sys.modules.pop("receipt_logo.mcp_server", None)
