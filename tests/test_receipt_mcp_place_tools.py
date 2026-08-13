"""Parity tests for the place tools across both MCP servers.

Both the local stdio server (``scripts/receipt_mcp_server.py``) and the
deployed Lambda server
(``infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py``) must
expose the same place tooling: find_places, set_receipt_place, and the
locally-executed fix_place. This guards the dual-server contract the
same way the line-item tool tests do.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_receipt_mcp_line_item_tools import (  # noqa: E402
    SERVER_FILES,
    _load_module,
)

EXPECTED_PLACE_TOOLS = {
    "find_places",
    "set_receipt_place",
    "fix_place",
}


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_place_tools_present_with_valid_schema(label):
    module = _load_module(f"{label}-place", SERVER_FILES[label])
    tools = asyncio.run(module.list_tools())
    by_name = {t.name: t for t in tools}

    missing = EXPECTED_PLACE_TOOLS - set(by_name)
    assert not missing, f"missing place tools in {label}: {missing}"

    for name in EXPECTED_PLACE_TOOLS:
        tool = by_name[name]
        schema = tool.inputSchema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
        assert isinstance(schema.get("properties"), dict)
        assert isinstance(tool.description, str) and tool.description.strip()

    # find_places requires no single field but needs at least one hint
    # documented; set_receipt_place must require the write essentials.
    assert set(by_name["set_receipt_place"].inputSchema["required"]) == {
        "image_id",
        "receipt_id",
        "merchant_name",
    }
    assert set(by_name["fix_place"].inputSchema["required"]) == {
        "image_id",
        "receipt_id",
        "reason",
    }
    # fix_place must be the local implementation, not the Lambda hop.
    assert "runs locally" in by_name["fix_place"].description


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_place_tool_impls_exist(label):
    module = _load_module(f"{label}-place-impl", SERVER_FILES[label])
    for name in EXPECTED_PLACE_TOOLS:
        impl = getattr(module, f"{name}_impl", None)
        assert callable(impl), f"missing {name}_impl in {label} server"
    # local helpers backing the tools
    for helper in (
        "_search_place_candidates",
        "_collect_receipt_hints",
        "_get_places_client",
    ):
        assert callable(
            getattr(module, helper, None)
        ), f"missing {helper} in {label} server"


def test_both_servers_expose_identical_place_tool_shape():
    stdio = _load_module("stdio-place-shape", SERVER_FILES["stdio"])
    lam = _load_module("lambda-place-shape", SERVER_FILES["lambda"])

    def shape(module):
        tools = asyncio.run(module.list_tools())
        return {
            t.name: (t.description, t.inputSchema)
            for t in tools
            if t.name in EXPECTED_PLACE_TOOLS
        }

    assert shape(stdio) == shape(lam)
