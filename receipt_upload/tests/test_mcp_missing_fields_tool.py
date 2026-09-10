"""list_receipts_missing_fields is a read-only, exactly-paginated worklist.

Both server copies (scripts/ and the hand-synced Lambda file) are loaded
with stubbed ``mcp`` modules, so the receipt_upload CI leg (which has no
mcp package) exercises the real tool schema, dispatch, and DynamoDB reads
against moto. Every test runs against both copies.
"""

import asyncio
import importlib.util
import inspect
import json
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

ROOT = Path(__file__).resolve().parents[2]
SERVERS = {
    "scripts": ROOT / "scripts" / "receipt_mcp_server.py",
    "lambda": ROOT
    / "infra"
    / "mcp_server_lambda"
    / "lambdas"
    / "receipt_mcp_server_server.py",
}
TOOL = "list_receipts_missing_fields"
ALL_FIELDS = ["date", "merchant_name", "line_merchant"]


class _StubServer:
    def __init__(self, name: str) -> None:
        self.name = name

    def list_tools(self):
        return lambda func: func

    def call_tool(self):
        return lambda func: func


class _StubType:
    """Stands in for mcp.types.Tool / TextContent / ImageContent."""

    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


def _stub_mcp() -> dict[str, types.ModuleType]:
    mcp = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    server.Server = _StubServer
    stdio = types.ModuleType("mcp.server.stdio")
    stdio.stdio_server = None
    mcp_types = types.ModuleType("mcp.types")
    mcp_types.Tool = _StubType
    mcp_types.TextContent = _StubType
    mcp_types.ImageContent = _StubType
    return {
        "mcp": mcp,
        "mcp.server": server,
        "mcp.server.stdio": stdio,
        "mcp.types": mcp_types,
    }


def _load_server(name: str, path: Path):
    saved = {key: sys.modules.get(key) for key in _stub_mcp()}
    sys.modules.update(_stub_mcp())
    try:
        spec = importlib.util.spec_from_file_location(
            f"mcp_missing_fields_{name}", path
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    return module


SERVER_MODULES = {
    name: _load_server(name, path) for name, path in SERVERS.items()
}


@pytest.fixture(params=sorted(SERVER_MODULES))
def server(request):
    return SERVER_MODULES[request.param]


def create_table(table_name: str) -> str:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    indexes = [
        ("GSI1", "GSI1PK", "GSI1SK"),
        ("GSI2", "GSI2PK", "GSI2SK"),
        ("GSI3", "GSI3PK", "GSI3SK"),
    ]
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
        ]
        + [
            {"AttributeName": attr, "AttributeType": "S"}
            for _, pk, sk in indexes
            for attr in (pk, sk)
        ],
        ProvisionedThroughput=throughput,
        GlobalSecondaryIndexes=[
            {
                "IndexName": index,
                "KeySchema": [
                    {"AttributeName": pk, "KeyType": "HASH"},
                    {"AttributeName": sk, "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": throughput,
            }
            for index, pk, sk in indexes
        ]
        + [
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


@pytest.fixture
def client(server):
    with mock_aws():
        dynamo_client = DynamoClient(create_table("MyMockedTable"))
        with patch.object(server, "_dynamo_client", dynamo_client):
            yield dynamo_client


def image_id(n: int) -> str:
    return f"{n:08d}-0000-4000-8000-000000000000"


DATE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def summary(n, merchant_name, date=DATE, item_count=0):
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=image_id(n),
            receipt_id=1,
            merchant_name=merchant_name,
            date=date,
            totals=MonetaryTotals(grand_total=float(n), subtotal=float(n)),
            item_count=item_count,
        ),
        timestamp_computed="2026-09-01T00:00:00+00:00",
    )


def line(n, item_index, merchant_name):
    return ReceiptLineItem(
        image_id=image_id(n),
        receipt_id=1,
        item_index=item_index,
        name=f"ITEM {item_index}",
        price="1.00",
        line_ids=[item_index + 1],
        extractor_version="line-items-blocks-v2",
        extracted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        merchant_name=merchant_name,
    )


# Receipt n -> (merchant, date, per-line merchants). Gaps are deliberate:
#   1 complete            2 no date            3 no merchant, lines bare
#   4 two of three lines bare                  5 no date, no merchant, no rows
#   6 complete            7 complete, no rows
CORPUS = {
    1: ("Costco", DATE, ["Costco", "Costco"]),
    2: ("Sprouts", None, ["Sprouts"]),
    3: (None, DATE, [None, ""]),
    4: ("Trader Joe's", DATE, ["Trader Joe's", None, ""]),
    5: (None, None, []),
    6: ("Target", DATE, ["Target"]),
    7: ("Vons", DATE, []),
}
EXPECTED = {
    "date": {2, 5},
    "merchant_name": {3, 5},
    "line_merchant": {3, 4},
}


def seed(client: DynamoClient) -> None:
    for n, (merchant, date, lines) in CORPUS.items():
        client.upsert_receipt_summary(
            summary(n, merchant, date=date, item_count=len(lines))
        )
        if lines:
            client.add_receipt_line_items(
                [line(n, i, m) for i, m in enumerate(lines)]
            )


def scan_all(client: DynamoClient) -> list[dict]:
    raw = boto3.client("dynamodb", region_name="us-east-1")
    items = []
    key = None
    while True:
        kwargs = {"TableName": client.table_name}
        if key:
            kwargs["ExclusiveStartKey"] = key
        page = raw.scan(**kwargs)
        items.extend(page["Items"])
        key = page.get("LastEvaluatedKey")
        if not key:
            return sorted(items, key=lambda i: (i["PK"]["S"], i["SK"]["S"]))


def call(server, **arguments) -> dict:
    content = asyncio.run(server.call_tool(TOOL, arguments))
    assert len(content) == 1
    return json.loads(content[0].text)


def receipt_numbers(result: dict) -> list[int]:
    return [int(r["image_id"][:8]) for r in result["receipts"]]


def test_both_servers_expose_identical_tool_schemas():
    schemas = {}
    for name, module in SERVER_MODULES.items():
        tools = {
            tool.name: (tool.description, tool.inputSchema)
            for tool in asyncio.run(module.list_tools())
        }
        assert TOOL in tools
        schemas[name] = tools[TOOL]
    assert schemas["scripts"] == schemas["lambda"]
    schema = schemas["scripts"][1]
    assert schema["required"] == ["fields"]
    assert schema["properties"]["fields"]["items"]["enum"] == ALL_FIELDS
    assert schema["properties"]["limit"]["default"] == 50


def test_both_servers_share_the_same_read_only_implementation():
    sources = {
        name: "".join(
            inspect.getsource(getattr(module, attr))
            for attr in (
                "_encode_summary_cursor",
                "_decode_summary_cursor",
                "list_receipts_missing_fields_impl",
            )
        )
        for name, module in SERVER_MODULES.items()
    }
    assert sources["scripts"] == sources["lambda"]
    source = sources["scripts"]
    # DynamoDB is reached only through DynamoClient read accessors.
    assert not re.search(r"\._client\b", source)
    assert "boto3" not in source
    assert not re.search(
        r"\b(add|upsert|update|delete|put|set|batch_write|transact)_?\w*\(",
        source,
    )


def test_reports_each_requested_gap_with_line_counts(server, client):
    seed(client)

    result = call(server, fields=ALL_FIELDS, limit=1000)

    assert result["fields"] == ALL_FIELDS
    assert result["scanned"] == 7
    assert result["next_cursor"] is None
    by_number = {int(r["image_id"][:8]): r for r in result["receipts"]}
    assert set(by_number) == {2, 3, 4, 5}
    assert result["count"] == 4
    assert by_number[2]["missing_fields"] == ["date"]
    assert by_number[2]["merchant_name"] == "Sprouts"
    assert by_number[2]["date"] is None
    assert by_number[3]["missing_fields"] == ["merchant_name", "line_merchant"]
    assert (
        by_number[3]["line_count"],
        by_number[3]["lines_missing_merchant"],
    ) == (2, 2)
    assert by_number[4]["missing_fields"] == ["line_merchant"]
    assert (
        by_number[4]["line_count"],
        by_number[4]["lines_missing_merchant"],
    ) == (3, 2)
    assert by_number[4]["date"] == DATE.isoformat()
    assert by_number[4]["grand_total"] == 4.0
    assert by_number[4]["item_count"] == 3
    assert by_number[5]["missing_fields"] == ["date", "merchant_name"]
    assert (
        by_number[5]["line_count"],
        by_number[5]["lines_missing_merchant"],
    ) == (0, 0)


@pytest.mark.parametrize("field", ALL_FIELDS)
def test_a_single_field_reports_only_that_gap(server, client, field):
    seed(client)

    result = call(server, fields=[field], limit=1000)

    assert set(receipt_numbers(result)) == EXPECTED[field]
    assert all(r["missing_fields"] == [field] for r in result["receipts"])
    has_line_counts = field == "line_merchant"
    assert all(
        ("line_count" in r) is has_line_counts for r in result["receipts"]
    )


@pytest.mark.parametrize("limit", [1, 2, 3, 7, 50])
def test_pagination_is_exact_across_pages(server, client, limit):
    seed(client)
    seen: list[int] = []
    scanned = 0
    cursor = None
    pages = 0
    while True:
        result = call(server, fields=ALL_FIELDS, limit=limit, cursor=cursor)
        pages += 1
        assert "error" not in result, result
        assert result["scanned"] <= limit
        seen.extend(receipt_numbers(result))
        scanned += result["scanned"]
        cursor = result["next_cursor"]
        if cursor is None:
            break
        assert pages < 20, "cursor never terminated"
    # Every receipt examined exactly once; every gap reported exactly once.
    assert scanned == len(CORPUS)
    assert sorted(seen) == sorted({2, 3, 4, 5})
    assert pages >= -(-len(CORPUS) // limit)


def test_tool_never_writes(server, client):
    seed(client)
    before = scan_all(client)

    call(server, fields=ALL_FIELDS, limit=3)
    call(server, fields=["line_merchant"], limit=1000)

    assert scan_all(client) == before


def test_empty_table_returns_an_empty_page(server, client):
    result = call(server, fields=ALL_FIELDS)
    assert result == {
        "fields": ALL_FIELDS,
        "scanned": 0,
        "count": 0,
        "receipts": [],
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"fields": []},
        {"fields": ["merchant"]},
        {"fields": ALL_FIELDS, "limit": 0},
        {"fields": ALL_FIELDS, "limit": 1001},
        {"fields": ALL_FIELDS, "limit": True},
        {"fields": ALL_FIELDS, "cursor": "not-a-token"},
        {"fields": ALL_FIELDS, "cursor": "e30="},
    ],
)
def test_rejects_bad_arguments_without_reading(server, client, arguments):
    seed(client)
    result = call(server, **arguments)
    assert set(result) == {"error"}, result


def test_cursor_round_trips_the_accessor_key(server):
    key = {"PK": {"S": "IMAGE#x"}, "SK": {"S": "RECEIPT#00001#SUMMARY"}}
    token = server._encode_summary_cursor(key)
    assert isinstance(token, str)
    assert server._decode_summary_cursor(token) == key
    assert server._encode_summary_cursor(None) is None
    assert server._decode_summary_cursor(None) is None
