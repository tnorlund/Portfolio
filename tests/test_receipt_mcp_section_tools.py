"""Cross-server contract tests for duplicated Receipt MCP implementations.

The two servers (stdio `scripts/receipt_mcp_server.py` and the Lambda
`infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`) must expose the
same tool surface. These tests import each module with a minimal fake `mcp`
package (the real dependency is not installed in CI) and assert the section
and real-time analytics tools have matching schemas, dispatch, and impls.
"""

import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timezone
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

EXPECTED_LIVE_ANALYTICS_TOOLS = {
    "analytics_live",
    "analytics_attribution",
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


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_live_analytics_tools_present_with_valid_schema(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = asyncio.run(module.list_tools())
    by_name = {tool.name: tool for tool in tools}

    missing = EXPECTED_LIVE_ANALYTICS_TOOLS - set(by_name)
    assert not missing, f"missing live analytics tools in {label}: {missing}"

    live_schema = by_name["analytics_live"].inputSchema
    assert live_schema["type"] == "object"
    assert live_schema["properties"]["minutes"]["default"] == 60
    assert live_schema["properties"]["minutes"]["minimum"] == 1
    assert live_schema["properties"]["minutes"]["maximum"] == 129600
    assert live_schema["properties"]["humans_only"]["default"] is True

    attribution_schema = by_name["analytics_attribution"].inputSchema
    assert attribution_schema["type"] == "object"
    assert {"campaign", "since", "humans_only"} <= set(
        attribution_schema["properties"]
    )
    assert attribution_schema["anyOf"] == [
        {"required": ["campaign"]},
        {"required": ["since"]},
    ]

    for name in EXPECTED_LIVE_ANALYTICS_TOOLS:
        assert callable(getattr(module, f"{name}_impl", None))


def test_both_servers_share_identical_tool_and_impl_suffix():
    """Only get_clients differs; tool schemas/routes/impls must stay in sync."""

    def common_suffix(path):
        source = path.read_text(encoding="utf-8")
        marker = "@server.list_tools()"
        assert marker in source
        return source[source.index(marker):]

    assert common_suffix(SERVER_FILES["stdio"]) == common_suffix(
        SERVER_FILES["lambda"]
    )


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_live_analytics_tools_are_dispatched(label):
    source = SERVER_FILES[label].read_text(encoding="utf-8")
    dispatch = source.split("async def call_tool", 1)[1].split(
        "async def search_receipts_impl", 1
    )[0]
    assert 'elif name == "analytics_live"' in dispatch
    assert 'elif name == "analytics_attribution"' in dispatch


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_live_human_filter_and_dynamo_normalization(label):
    from decimal import Decimal

    module = _load_module(label, SERVER_FILES[label])
    assert module._analytics_live_is_human({}) is True
    assert module._analytics_live_is_human({"is_bot": True}) is False
    assert module._analytics_live_is_human({"is_warp": "true"}) is False
    assert module._analytics_live_is_human({"is_hosting": Decimal("1")}) is False

    normalized = module._analytics_normalize_dynamo(
        {"whole": Decimal("42"), "fraction": Decimal("1.25")}
    )
    assert normalized == {"whole": 42, "fraction": 1.25}


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_attribution_keeps_full_session_after_campaign_landing(
    label, monkeypatch
):
    module = _load_module(label, SERVER_FILES[label])
    events = [
        {
            "dt": "2026-07-10",
            "sk": "1783700000000#session-1#landing",
            "epoch_ms": 1783700000000,
            "ts": "2026-07-10T00:13:20.000Z",
            "sid": "session-1",
            "eid": "landing",
            "event": "page_view",
            "path": "/receipt",
            "ref": "https://www.linkedin.com/messaging/thread/123",
            "utm_source": "li",
            "utm_medium": "dm",
            "utm_campaign": "arthur-babylist",
            "country": "US",
            "region": "WA",
            "city": "Vancouver",
        },
        {
            "dt": "2026-07-10",
            "sk": "1783700060000#session-1#next-page",
            "epoch_ms": 1783700060000,
            "ts": "2026-07-10T00:14:20.000Z",
            "sid": "session-1",
            "eid": "next-page",
            "event": "page_view",
            "path": "/resume",
        },
        {
            "dt": "2026-07-10",
            "sk": "1783700070000#hosting-session#blocked",
            "epoch_ms": 1783700070000,
            "ts": "2026-07-10T00:14:30.000Z",
            "sid": "hosting-session",
            "eid": "blocked",
            "event": "page_view",
            "path": "/receipt",
            "utm_campaign": "arthur-babylist",
            "is_hosting": True,
        },
    ]
    monkeypatch.setattr(
        module,
        "_analytics_live_query",
        lambda _start, _end: (events, False),
    )

    result = asyncio.run(
        module.analytics_attribution_impl(campaign="arthur-babylist")
    )

    assert result["session_count"] == 1
    assert result["event_count"] == 2
    assert result["truncated"] is False
    session = result["sessions"][0]
    assert session["sid"] == "session-1"
    assert session["pages"] == ["/receipt", "/resume"]
    assert session["utm_campaign"] == "arthur-babylist"
    assert session["referrer_host"] == "www.linkedin.com"
    assert session["referrer_path"] == "/messaging/thread/123"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_live_query_uses_partition_queries_not_scan(label):
    source = SERVER_FILES[label].read_text(encoding="utf-8")
    query_source = source.split("def _analytics_live_query", 1)[1].split(
        "def _analytics_live_flag", 1
    )[0]
    assert 'Key("dt").eq' in query_source
    assert 'Key("sk").between' in query_source
    assert '"ConsistentRead": True' in query_source
    assert '"ScanIndexForward": False' in query_source
    assert 'query_args["Limit"]' in query_source
    assert "table.query(" in query_source
    assert "scan(" not in query_source


class _FakeDynamoCondition:
    def __init__(self, parts):
        self.parts = list(parts)

    def __and__(self, other):
        return _FakeDynamoCondition(self.parts + other.parts)


def _install_fake_boto3_query(monkeypatch, table, queried_days):
    class _FakeKey:
        def __init__(self, name):
            self.name = name

        def eq(self, value):
            if self.name == "dt":
                queried_days.append(value)
            return _FakeDynamoCondition([(self.name, "eq", value)])

        def between(self, lower, upper):
            return _FakeDynamoCondition(
                [(self.name, "between", lower, upper)]
            )

    boto3_module = types.ModuleType("boto3")
    dynamodb_module = types.ModuleType("boto3.dynamodb")
    conditions_module = types.ModuleType("boto3.dynamodb.conditions")
    conditions_module.Key = _FakeKey
    dynamodb_module.conditions = conditions_module
    boto3_module.dynamodb = dynamodb_module
    boto3_module.resource = lambda *_args, **_kwargs: types.SimpleNamespace(
        Table=lambda _name: table
    )
    monkeypatch.setitem(sys.modules, "boto3", boto3_module)
    monkeypatch.setitem(sys.modules, "boto3.dynamodb", dynamodb_module)
    monkeypatch.setitem(
        sys.modules, "boto3.dynamodb.conditions", conditions_module
    )


def _live_test_event(ts, sid, campaign=None):
    epoch_ms = int(ts.timestamp() * 1000)
    event = {
        "dt": ts.date().isoformat(),
        "sk": f"{epoch_ms:013d}#{sid}#event",
        "epoch_ms": epoch_ms,
        "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "sid": sid,
        "eid": f"{sid}-event",
        "event": "page_view",
        "path": f"/{sid}",
    }
    if campaign is not None:
        event["utm_campaign"] = campaign
    return event


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_live_query_caps_newest_page_and_reports_unread_page(
    label, monkeypatch
):
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setattr(module, "ANALYTICS_LIVE_MAX_EVENTS", 3)
    queried_days = []
    responses = [
        {
            "Items": [
                _live_test_event(
                    datetime(2026, 7, 10, 12, 3, tzinfo=timezone.utc),
                    "newest",
                ),
                _live_test_event(
                    datetime(2026, 7, 10, 12, 2, tzinfo=timezone.utc),
                    "middle",
                ),
            ],
            "LastEvaluatedKey": {"dt": "2026-07-10", "sk": "page-2"},
        },
        {
            "Items": [
                _live_test_event(
                    datetime(2026, 7, 10, 12, 1, tzinfo=timezone.utc),
                    "oldest-returned",
                )
            ],
            "LastEvaluatedKey": {"dt": "2026-07-10", "sk": "unread"},
        },
    ]

    class _FakeTable:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            return responses.pop(0)

    table = _FakeTable()
    _install_fake_boto3_query(monkeypatch, table, queried_days)
    start = datetime(2026, 7, 9, tzinfo=timezone.utc)
    end = datetime(2026, 7, 10, 23, 59, tzinfo=timezone.utc)

    events, truncated = module._analytics_live_query(start, end)

    assert truncated is True
    assert [event["sid"] for event in events] == [
        "newest",
        "middle",
        "oldest-returned",
    ]
    assert queried_days == ["2026-07-10"]
    assert [call["Limit"] for call in table.calls] == [3, 1]
    assert all(call["ConsistentRead"] is True for call in table.calls)
    assert all(call["ScanIndexForward"] is False for call in table.calls)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_live_query_reports_unread_older_date_at_exact_cap(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setattr(module, "ANALYTICS_LIVE_MAX_EVENTS", 2)
    queried_days = []

    class _FakeTable:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "Items": [
                    _live_test_event(
                        datetime(2026, 7, 10, 12, 2, tzinfo=timezone.utc),
                        "newest",
                    ),
                    _live_test_event(
                        datetime(2026, 7, 10, 12, 1, tzinfo=timezone.utc),
                        "next",
                    ),
                ]
            }

    table = _FakeTable()
    _install_fake_boto3_query(monkeypatch, table, queried_days)
    start = datetime(2026, 7, 9, tzinfo=timezone.utc)
    end = datetime(2026, 7, 10, 23, 59, tzinfo=timezone.utc)

    events, truncated = module._analytics_live_query(start, end)

    assert len(events) == 2
    assert truncated is True
    assert queried_days == ["2026-07-10"]
    assert len(table.calls) == 1


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_analytics_live_caps_to_newest_sessions(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setattr(module, "ANALYTICS_LIVE_MAX_SESSIONS", 2)
    events = [
        _live_test_event(
            datetime(2026, 7, 10, 12, minute, tzinfo=timezone.utc),
            sid,
        )
        for minute, sid in [(1, "oldest"), (2, "middle"), (3, "newest")]
    ]
    monkeypatch.setattr(
        module,
        "_analytics_live_query",
        lambda _start, _end: (events, False),
    )

    result = asyncio.run(module.analytics_live_impl(humans_only=False))

    assert result["truncated"] is True
    assert result["event_count"] == 3
    assert result["session_count"] == 2
    assert [session["sid"] for session in result["sessions"]] == [
        "newest",
        "middle",
    ]


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_attribution_filters_then_caps_to_newest_campaign_sessions(
    label, monkeypatch
):
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setattr(module, "ANALYTICS_LIVE_MAX_SESSIONS", 2)
    events = [
        _live_test_event(
            datetime(2026, 7, 10, 12, minute, tzinfo=timezone.utc),
            sid,
            campaign,
        )
        for minute, sid, campaign in [
            (1, "campaign-oldest", "launch"),
            (2, "campaign-middle", "launch"),
            (3, "campaign-newest", "launch"),
            (4, "unrelated-newest", "other"),
        ]
    ]
    monkeypatch.setattr(
        module,
        "_analytics_live_query",
        lambda _start, _end: (events, False),
    )

    result = asyncio.run(
        module.analytics_attribution_impl(
            campaign="launch", humans_only=False
        )
    )

    assert result["truncated"] is True
    assert result["event_count"] == 2
    assert result["session_count"] == 2
    assert [session["sid"] for session in result["sessions"]] == [
        "campaign-newest",
        "campaign-middle",
    ]


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
        module.create_receipt_section_impl(
            client, VALID_IMAGE_ID, 1, "ITMES", [1, 2]
        )
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
        module.create_receipt_section_impl(
            client, VALID_IMAGE_ID, 1, "ITEMS", [2, 99]
        )
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
        module.create_receipt_section_impl(
            client, VALID_IMAGE_ID, 42, "ITEMS", [1]
        )
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
        module.create_receipt_section_impl(
            client, VALID_IMAGE_ID, 1, " items ", [1, 2]
        )
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
@pytest.mark.parametrize(
    "legacy_type", ["HEADER", "ITEMS_VALUE", "ITEMS_DESCRIPTION"]
)
def test_create_rejects_deprecated_section_types(label, legacy_type):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(line_ids=(1, 2, 3))
    result = asyncio.run(
        module.create_receipt_section_impl(
            client, VALID_IMAGE_ID, 1, legacy_type, [1]
        )
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
        module.delete_receipt_section_impl(
            _DeleteClient(), VALID_IMAGE_ID, 1, "HEADER"
        )
    )
    assert result.get("success") is True
    assert deleted == [(1, VALID_IMAGE_ID, "HEADER")]
