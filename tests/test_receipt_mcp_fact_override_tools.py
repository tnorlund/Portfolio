"""set_receipt_fact / get_receipt_fact_override through both MCP entry points.

The local server and the package staged by the Lambda Dockerfile are
loaded with a stubbed ``mcp`` package and exercised end to end
(list_tools schema -> call_tool dispatch -> impl -> DynamoClient) against
moto. Every test runs against both entry points.
"""

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from receipt_mcp_test_support import SERVER_FILES, load_server_module

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._receipt_fact_override import (
    PROTECTED_FACT_TABLE_MARKERS,
)
from receipt_dynamo.entities.receipt_fact_override import ReceiptFactOverride

NEW_TOOLS = ("set_receipt_fact", "get_receipt_fact_override")
IMAGE_ID = "b7eecdb7-9eaf-47c0-941a-b576604c2e9d"
RECEIPT_ID = 1


class _FakeTool:
    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


class _FakeContent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _install_mcp_stubs():
    class _FakeServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            return lambda func: func

        def call_tool(self):
            return lambda func: func

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    server_mod.Server = _FakeServer
    stdio_mod = types.ModuleType("mcp.server.stdio")
    stdio_mod.stdio_server = None
    types_mod = types.ModuleType("mcp.types")
    types_mod.Tool = _FakeTool
    types_mod.TextContent = _FakeContent
    types_mod.ImageContent = _FakeContent
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.stdio"] = stdio_mod
    sys.modules["mcp.types"] = types_mod


def _load_module(label: str, path: Path) -> types.ModuleType:
    _install_mcp_stubs()
    return load_server_module(f"receipt_mcp_fact_override_{label}", path)


SERVER_MODULES = {
    label: _load_module(label, path) for label, path in SERVER_FILES.items()
}


@pytest.fixture(params=sorted(SERVER_MODULES))
def server(request):
    return SERVER_MODULES[request.param]


def _create_table(table_name: str) -> str:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    throughput = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
    dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "TYPE", "AttributeType": "S"},
        ],
        ProvisionedThroughput=throughput,
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": throughput,
            }
        ],
    )
    dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)
    return table_name


@pytest.fixture(autouse=True)
def mock_aws_services(monkeypatch):
    """Replace the root conftest's boto3 MagicMock with moto (no creds)."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws():
        yield


@pytest.fixture
def client(server):
    dynamo_client = DynamoClient(_create_table("MyMockedTable"))
    with patch.object(server, "_dynamo_client", dynamo_client):
        yield dynamo_client


def call(server, name: str, **arguments) -> dict:
    content = asyncio.run(server.call_tool(name, arguments))
    assert len(content) == 1
    return json.loads(content[0].text)


def set_fact(server, field="date", value="2026-09-01", expected=None, **kw):
    arguments = dict(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        field=field,
        value=value,
        reference="Chase statement 2026-09-01 $47.18",
        expected_revision=expected,
    )
    arguments.update(kw)
    return call(server, "set_receipt_fact", **arguments)


def get_fact(server) -> dict:
    return call(
        server,
        "get_receipt_fact_override",
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
    )


def test_both_servers_expose_identical_tool_schemas():
    schemas = {}
    for label, module in SERVER_MODULES.items():
        tools = {
            tool.name: (tool.description, tool.inputSchema)
            for tool in asyncio.run(module.list_tools())
        }
        assert set(NEW_TOOLS) <= set(tools)
        schemas[label] = {name: tools[name] for name in NEW_TOOLS}
    assert schemas["stdio"] == schemas["lambda"]

    set_schema = schemas["stdio"]["set_receipt_fact"][1]
    assert set(set_schema["required"]) == {
        "image_id",
        "receipt_id",
        "field",
        "value",
        "reference",
        "expected_revision",
    }
    assert set_schema["properties"]["field"]["enum"] == [
        "date",
        "merchant_name",
    ]
    assert set_schema["properties"]["expected_revision"]["type"] == [
        "integer",
        "null",
    ]
    get_schema = schemas["stdio"]["get_receipt_fact_override"][1]
    assert set(get_schema["required"]) == {"image_id", "receipt_id"}


def test_both_servers_share_the_accessor_denylist():
    for module in SERVER_MODULES.values():
        assert tuple(module.FACT_OVERRIDE_TABLE_DENYLIST) == tuple(
            PROTECTED_FACT_TABLE_MARKERS
        )


def test_create_writes_a_revision_one_override(server, client):
    result = set_fact(server)

    assert result["action"] == "created"
    assert result["override"]["revision"] == 1
    assert result["override"]["date"] == "2026-09-01"
    assert result["override"]["date_reference"] == (
        "Chase statement 2026-09-01 $47.18"
    )
    assert result["override"]["merchant_name"] is None
    assert result["override"]["merchant_name_reference"] is None
    assert result["override"]["source"] == "owner"
    assert result["override"]["changed_at"].endswith("+00:00")
    assert result["override"]["facts"] == {"date": "2026-09-01"}
    assert result["override"]["references"] == {
        "date": "Chase statement 2026-09-01 $47.18"
    }
    assert "next_step" in result

    stored = client.get_receipt_fact_override(IMAGE_ID, RECEIPT_ID)
    assert isinstance(stored, ReceiptFactOverride)
    assert stored.date_reference == "Chase statement 2026-09-01 $47.18"
    assert get_fact(server)["override"] == result["override"]


def test_get_reports_none_when_nothing_stated(server, client):
    result = get_fact(server)

    assert result == {
        "image_id": IMAGE_ID,
        "receipt_id": RECEIPT_ID,
        "override": None,
    }


def test_create_refuses_when_an_override_exists(server, client):
    set_fact(server)

    result = set_fact(server, value="2026-09-02")

    assert "already exists" in result["error"]
    assert client.get_receipt_fact_override(IMAGE_ID, 1).date == "2026-09-01"


def test_update_with_current_revision_adds_a_second_fact(server, client):
    set_fact(server)

    result = set_fact(
        server,
        field="merchant_name",
        value="Trader Joe's",
        expected=1,
        reference="receipt header is cropped",
    )

    assert result["action"] == "updated"
    assert result["override"]["revision"] == 2
    assert result["override"]["date"] == "2026-09-01"
    assert result["override"]["merchant_name"] == "Trader Joe's"
    # Each fact keeps its own provenance.
    assert result["override"]["references"] == {
        "date": "Chase statement 2026-09-01 $47.18",
        "merchant_name": "receipt header is cropped",
    }
    stored = client.get_receipt_fact_override(IMAGE_ID, RECEIPT_ID)
    assert stored.revision == 2
    assert stored.merchant_name == "Trader Joe's"
    assert stored.date_reference == "Chase statement 2026-09-01 $47.18"


def test_update_with_stale_revision_is_refused(server, client):
    set_fact(server)
    set_fact(server, value="2026-09-02", expected=1)

    result = set_fact(server, value="2026-09-03", expected=1)

    assert "revision conflict" in result["error"]
    assert result["current_revision"] == 2
    assert client.get_receipt_fact_override(IMAGE_ID, 1).date == "2026-09-02"


def test_update_without_an_override_is_refused(server, client):
    result = set_fact(server, expected=1)

    assert "no override exists" in result["error"]
    assert client.get_receipt_fact_override(IMAGE_ID, RECEIPT_ID) is None


def test_retracting_the_last_fact_keeps_the_row_and_revision(server, client):
    set_fact(server)

    result = set_fact(server, value=None, expected=1)

    assert result["action"] == "retracted"
    assert result["override"]["revision"] == 2
    assert result["override"]["facts"] == {}
    assert result["override"]["date_reference"] is None
    stored = client.get_receipt_fact_override(IMAGE_ID, RECEIPT_ID)
    assert stored is not None and stored.facts == {}
    assert get_fact(server)["override"]["revision"] == 2


def test_stale_revision_never_becomes_valid_again(server, client):
    # Editor A reads revision 1; editor B retracts and re-states the date.
    set_fact(server)
    set_fact(server, value=None, expected=1)
    restated = set_fact(server, value="2026-09-09", expected=2)
    assert restated["override"]["revision"] == 3

    # A's stale write with revision 1 is refused, and so is a fresh create.
    stale = set_fact(server, value="2026-09-02", expected=1)
    recreate = set_fact(server, value="2026-09-02")

    assert "revision conflict" in stale["error"]
    assert "already exists" in recreate["error"]
    assert client.get_receipt_fact_override(IMAGE_ID, 1).date == "2026-09-09"


def test_retracting_one_of_two_facts_keeps_the_other_provenance(
    server, client
):
    set_fact(server)
    set_fact(
        server,
        field="merchant_name",
        value="Trader Joe's",
        expected=1,
        reference="cropped header",
    )

    result = set_fact(server, value=None, expected=2)

    assert result["action"] == "retracted"
    assert result["override"]["revision"] == 3
    assert result["override"]["facts"] == {"merchant_name": "Trader Joe's"}
    assert result["override"]["references"] == {
        "merchant_name": "cropped header"
    }


def test_create_requires_a_value(server, client):
    result = set_fact(server, value=None)

    assert "value is required" in result["error"]
    assert client.get_receipt_fact_override(IMAGE_ID, RECEIPT_ID) is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"value": "09/01/2026"},
        {"value": "2026-13-01"},
        {"field": "merchant_name", "value": "   "},
        {"reference": ""},
        {"field": "grand_total", "value": "47.18"},
        {"expected": 0},
        {"expected": "1"},
        {"value": 20260901},
        {"image_id": "not-a-uuid"},
        {"receipt_id": 0},
    ],
)
def test_invalid_input_is_refused_without_a_write(server, client, arguments):
    result = set_fact(server, **arguments)

    assert "error" in result
    assert client.get_receipt_fact_override(IMAGE_ID, RECEIPT_ID) is None


def test_protected_table_is_refused_before_any_read_or_write(server):
    def _forbidden(*args, **kwargs):
        raise AssertionError("the protected table must not be touched")

    guard = SimpleNamespace(
        table_name="ReceiptsTable-d7ff76a",
        get_receipt_fact_override=_forbidden,
        add_receipt_fact_override=_forbidden,
        update_receipt_fact_override=_forbidden,
        delete_receipt_fact_override=_forbidden,
    )
    with patch.object(server, "_dynamo_client", guard):
        created = set_fact(server)
        updated = set_fact(server, expected=1)
        cleared = set_fact(server, value=None, expected=1)

    for result in (created, updated, cleared):
        assert "refuses the configured table" in result["error"]


def test_dev_table_names_are_not_refused(server):
    assert (
        server._refuse_protected_fact_table(
            SimpleNamespace(table_name="ReceiptsTable-dc5be22")
        )
        is None
    )
