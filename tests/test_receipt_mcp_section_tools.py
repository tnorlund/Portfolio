"""Tests for the ReceiptSection QA tools on both MCP server implementations.

The two servers (stdio `scripts/receipt_mcp_server.py` and the Lambda
`infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`) must expose the
same tool surface. These tests import each module with a minimal fake `mcp`
package (the real dependency is not installed in CI) and assert the four
section tools are registered with valid input schemas and matching impls.
"""

import asyncio
import importlib.util
import json
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


EXPECTED_RESEGMENT_TOOLS = {
    "plan_receipt_resegmentation",
    "get_receipt_resegmentation_plan",
    "revise_receipt_resegmentation_plan",
    "apply_receipt_resegmentation",
}

EXPECTED_ROUTINE_TOOLS = {
    "list_receipt_health_issues",
    "update_receipt_health_issue",
}


class _FakeTool:
    """Stand-in for mcp.types.Tool that just records its fields."""

    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


class _FakeContent:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


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
    types_mod.TextContent = _FakeContent
    types_mod.ImageContent = _FakeContent

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.stdio"] = stdio_mod
    sys.modules["mcp.types"] = types_mod


def _load_module(label, path):
    _install_mcp_stubs()
    spec = importlib.util.spec_from_file_location(f"receipt_mcp_server_{label}", path)
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


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_resegment_tools_present_with_valid_schema(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = asyncio.run(module.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert EXPECTED_RESEGMENT_TOOLS <= set(by_name)
    plan_schema = by_name["plan_receipt_resegmentation"].inputSchema
    assert set(plan_schema["required"]) == {
        "image_id",
        "source_receipt_id",
        "segments",
    }
    segment_schema = plan_schema["properties"]["segments"]["items"]
    assert "include_word_refs" in segment_schema["properties"]
    word_ref_schema = segment_schema["properties"]["include_word_refs"]["items"]
    assert word_ref_schema["oneOf"] == [
        {"required": ["word_id"]},
        {"required": ["word_ids"]},
    ]
    assert plan_schema["properties"]["schema_version"]["enum"] == [1, 2]
    assert "assignments" in plan_schema["properties"]
    assert "visualization" in plan_schema["properties"]
    assert "visible_regions" in segment_schema["properties"]
    revise_schema = by_name["revise_receipt_resegmentation_plan"].inputSchema
    assert set(revise_schema["required"]) == {
        "plan_id",
        "base_revision",
        "base_plan_hash",
        "segments",
        "assignments",
        "revision_reason",
    }
    get_schema = by_name["get_receipt_resegmentation_plan"].inputSchema
    assert get_schema["required"] == ["plan_id"]
    apply_schema = by_name["apply_receipt_resegmentation"].inputSchema
    assert set(apply_schema["required"]) == {"plan_id", "plan_hash"}


def test_both_servers_expose_identical_resegment_tool_shape():
    stdio = _load_module("stdio-resegment", SERVER_FILES["stdio"])
    lam = _load_module("lambda-resegment", SERVER_FILES["lambda"])

    def shape(module):
        tools = asyncio.run(module.list_tools())
        return {
            tool.name: (tool.description, tool.inputSchema)
            for tool in tools
            if tool.name in EXPECTED_RESEGMENT_TOOLS
        }

    assert shape(stdio) == shape(lam)
    for module in (stdio, lam):
        for name in EXPECTED_RESEGMENT_TOOLS:
            assert callable(getattr(module, f"{name}_impl"))


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_routine_tools_present_with_scoped_schema(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = asyncio.run(module.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert EXPECTED_ROUTINE_TOOLS <= set(by_name)
    list_schema = by_name["list_receipt_health_issues"].inputSchema
    assert list_schema["properties"]["limit"]["maximum"] == 10
    update_schema = by_name["update_receipt_health_issue"].inputSchema
    assert set(update_schema["required"]) == {"issue_id", "action"}
    assert "mark_attempted" in update_schema["properties"]["action"]["enum"]


def test_both_servers_expose_identical_routine_tool_shape():
    stdio = _load_module("stdio-routine", SERVER_FILES["stdio"])
    lam = _load_module("lambda-routine", SERVER_FILES["lambda"])

    def shape(module):
        tools = asyncio.run(module.list_tools())
        return {
            tool.name: (tool.description, tool.inputSchema)
            for tool in tools
            if tool.name in EXPECTED_ROUTINE_TOOLS
        }

    assert shape(stdio) == shape(lam)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_routine_tools_proxy_to_internal_ledger_lambda(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    calls = []

    async def fake_lambda_name():
        return "receipt-health-ledger"

    async def fake_invoke(function_name, payload):
        calls.append((function_name, payload))
        body = {"issues": [], "count": 0}
        if payload.get("operation"):
            body = {"issue": {"issue_id": payload["body"]["issue_id"]}}
        return {"statusCode": 200, "body": json.dumps(body)}

    monkeypatch.setattr(module, "_receipt_health_lambda_name", fake_lambda_name)
    monkeypatch.setattr(module, "_invoke_lambda", fake_invoke)

    listed = asyncio.run(
        module.list_receipt_health_issues_impl(
            {
                "state": "eligible",
                "check_id": "financial_math",
                "classification": "safe_exact_plan",
                "limit": 100,
            }
        )
    )
    updated = asyncio.run(
        module.update_receipt_health_issue_impl(
            {"issue_id": "issue-1", "action": "mark_attempted"}
        )
    )

    assert listed == {"issues": [], "count": 0}
    assert updated == {"issue": {"issue_id": "issue-1"}}
    assert [function_name for function_name, _ in calls] == [
        "receipt-health-ledger",
        "receipt-health-ledger",
    ]
    list_event = calls[0][1]
    assert list_event["requestContext"]["http"]["method"] == "GET"
    assert list_event["queryStringParameters"]["limit"] == "10"
    assert calls[1][1] == {
        "operation": "receipt_health_issue_update",
        "body": {"issue_id": "issue-1", "action": "mark_attempted"},
    }


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_resegment_proxies_force_lambda_mode(label, monkeypatch):
    """A caller-supplied `mode` key must never override the proxy's mode.

    Otherwise the read-only-looking plan/revise tools could be smuggled
    into invoking the destructive apply operation.
    """
    module = _load_module(label, SERVER_FILES[label])
    calls = []

    async def fake_invoke(function_name, payload):
        calls.append((function_name, payload))
        return {"ok": True}

    monkeypatch.setattr(module, "_invoke_lambda", fake_invoke)

    asyncio.run(
        module.plan_receipt_resegmentation_impl(
            {"image_id": "img", "mode": "apply", "plan_hash": "h"}
        )
    )
    asyncio.run(
        module.revise_receipt_resegmentation_plan_impl(
            {"plan_id": "p", "mode": "apply"}
        )
    )

    assert [payload["mode"] for _, payload in calls] == ["plan", "revise"]
    for function_name, _ in calls:
        assert function_name.endswith("-resegment-receipt")


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_resegment_plan_attaches_contact_sheet_as_image_content(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setattr(module, "get_clients", lambda: (object(), object(), object()))

    async def fake_plan(_arguments):
        return {
            "plan_id": "plan-1",
            "preview_urls": {"contact_sheet": "https://example.test/sheet.jpg"},
            "visualizations": {"contact_sheet": {"mime_type": "image/jpeg"}},
        }

    monkeypatch.setattr(module, "plan_receipt_resegmentation_impl", fake_plan)
    monkeypatch.setattr(module, "_fetch_mcp_preview", lambda _url: b"jpeg-bytes")

    content = asyncio.run(module.call_tool("plan_receipt_resegmentation", {}))

    assert [item.type for item in content] == ["text", "image"]
    assert content[1].mimeType == "image/jpeg"
    assert content[1].data == "anBlZy1ieXRlcw=="


# ---------------------------------------------------------------------------
# Corruption-path tests: the impls must refuse writes that would persist bad
# rows (noncanonical section_type SKs, orphan sections, invalid line refs).
# These exercise the impl functions with a stub dynamo client and need the
# real receipt_dynamo package for its enums/exceptions.
# ---------------------------------------------------------------------------

VALID_IMAGE_ID = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"


class _StubLine:
    def __init__(self, line_id):
        self.line_id = line_id
        self.text = f"line {line_id}"


class _StubDetails:
    def __init__(self, line_ids):
        self.lines = [_StubLine(i) for i in line_ids]


class _StubDynamoClient:
    """Stub client recording writes; configurable receipt lines."""

    def __init__(self, line_ids=(1, 2, 3), receipt_exists=True):
        self._line_ids = line_ids
        self._receipt_exists = receipt_exists
        self.added_sections = []

    def get_receipt_details(self, image_id, receipt_id):
        if not self._receipt_exists:
            from receipt_dynamo.data.shared_exceptions import (
                EntityNotFoundError,
            )

            raise EntityNotFoundError("receipt not found")
        return _StubDetails(self._line_ids)

    def add_receipt_section(self, section):
        self.added_sections.append(section)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_create_rejects_invalid_section_type(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient()
    result = asyncio.run(
        module.create_receipt_section_impl(client, VALID_IMAGE_ID, 1, "ITMES", [1, 2])
    )
    assert "error" in result
    assert "Invalid section_type" in result["error"]
    assert "ITEMS" in result["error"]  # lists the valid values
    assert client.added_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_create_rejects_line_ids_not_on_receipt(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(line_ids=(1, 2, 3))
    result = asyncio.run(
        module.create_receipt_section_impl(client, VALID_IMAGE_ID, 1, "ITEMS", [2, 99])
    )
    assert "error" in result
    assert "99" in result["error"]
    assert "do not exist on receipt" in result["error"]
    assert client.added_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_create_rejects_nonexistent_receipt(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(receipt_exists=False)
    result = asyncio.run(
        module.create_receipt_section_impl(client, VALID_IMAGE_ID, 42, "ITEMS", [1])
    )
    assert "error" in result
    assert "not found" in result["error"]
    assert client.added_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_create_succeeds_for_valid_input(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(line_ids=(1, 2, 3))
    result = asyncio.run(
        module.create_receipt_section_impl(client, VALID_IMAGE_ID, 1, " items ", [1, 2])
    )
    assert result.get("success") is True
    assert result["section_type"] == "ITEMS"  # stripped + uppercased
    assert len(client.added_sections) == 1
    assert client.added_sections[0].section_type == "ITEMS"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
@pytest.mark.parametrize(
    "impl_name,args",
    [
        ("update_section_status_impl", ("ITMES", "VALID")),
        ("delete_receipt_section_impl", ("ITMES",)),
    ],
)
def test_update_and_delete_reject_invalid_section_type(label, impl_name, args):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    impl = getattr(module, impl_name)

    class _ExplodingClient:
        def __getattr__(self, name):  # any client call means we validated late
            raise AssertionError(
                "client must not be called for an invalid section_type"
            )

    result = asyncio.run(impl(_ExplodingClient(), VALID_IMAGE_ID, 1, *args))
    assert "error" in result
    assert "Invalid section_type" in result["error"]
    assert "ITEMS" in result["error"]


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
@pytest.mark.parametrize("legacy_type", ["HEADER", "ITEMS_VALUE", "ITEMS_DESCRIPTION"])
def test_create_rejects_deprecated_section_types(label, legacy_type):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(line_ids=(1, 2, 3))
    result = asyncio.run(
        module.create_receipt_section_impl(client, VALID_IMAGE_ID, 1, legacy_type, [1])
    )
    assert "error" in result
    assert "deprecated" in result["error"]
    assert client.added_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_delete_still_accepts_deprecated_section_types(label):
    """QA must be able to remove stray legacy rows even though create can't
    mint new ones."""
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])

    deleted = []

    class _DeleteClient:
        def delete_receipt_section(self, receipt_id, image_id, section_type):
            deleted.append((receipt_id, image_id, section_type))

    result = asyncio.run(
        module.delete_receipt_section_impl(_DeleteClient(), VALID_IMAGE_ID, 1, "HEADER")
    )
    assert result.get("success") is True
    assert deleted == [(1, VALID_IMAGE_ID, "HEADER")]
