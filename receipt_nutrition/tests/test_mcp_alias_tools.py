"""confirm/reject alias MCP tools write user decisions through the DAL guard.

Both server copies (scripts/ and the hand-synced Lambda file) are loaded
with stubbed ``mcp`` modules, so the receipt_nutrition CI leg (which has no
mcp package) exercises the real tool schemas, dispatch, and DynamoDB writes
against moto. Every test runs against both copies.
"""

import asyncio
import importlib.util
import inspect
import json
import re
import sys
import types
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import NutritionConflictError
from receipt_dynamo.entities.food_product import FoodProduct
from receipt_dynamo.entities.nutrition_support import nutrition_json
from receipt_dynamo.entities.product_alias import ProductAlias

ROOT = Path(__file__).resolve().parents[2]
SERVERS = {
    "scripts": ROOT / "scripts" / "receipt_mcp_server.py",
    "lambda": ROOT
    / "infra"
    / "mcp_server_lambda"
    / "lambdas"
    / "receipt_mcp_server_server.py",
}
NEW_TOOLS = ("confirm_product_alias", "reject_product_alias")


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
            f"mcp_alias_tools_{name}", path
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


@pytest.fixture
def dynamodb_table():
    """Same shape as receipt_dynamo/tests/integration/conftest.py."""
    with mock_aws():
        yield create_table("MyMockedTable")


def create_table(table_name: str) -> str:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    indexes = [
        ("GSI1", "GSI1PK", "GSI1SK"),
        ("GSI2", "GSI2PK", "GSI2SK"),
        ("GSI3", "GSI3PK", "GSI3SK"),
        ("GSI4", "GSI4PK", "GSI4SK"),
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
def client(dynamodb_table, server):
    dynamo_client = DynamoClient(dynamodb_table)
    with patch.object(server, "_dynamo_client", dynamo_client):
        yield dynamo_client


def product(version: int = 1) -> FoodProduct:
    return FoodProduct(
        "tj:084621",
        nutrition_json({"product_id": "tj:084621", "version": version}),
    )


def automatic_alias(fact: FoodProduct | None = None, **changes):
    fields = dict(
        merchant_slug="trader-joes",
        kind="TEXT",
        text="TATER BITES POTATO WITH",
        revision=1,
        status="matched" if fact else "pending",
        method="lexical",
        changed_at="2026-09-09T00:00:00+00:00",
        expires_at=2_000_000_000,
        applicability_json='{"size":"4.6 oz"}',
        product_id=fact.product_id if fact else None,
        product_revision=fact.revision if fact else None,
    )
    fields.update(changes)
    return ProductAlias(**fields)


def save(client: DynamoClient, alias: ProductAlias, expected: int = 0):
    return client.save_product_alias(
        alias,
        expected_revision=expected,
        expected_table_name=client.table_name,
    )


def call(server, name: str, **arguments) -> dict:
    content = asyncio.run(server.call_tool(name, arguments))
    assert len(content) == 1
    return json.loads(content[0].text)


def confirm(server, fact: FoodProduct, expected, **changes) -> dict:
    arguments = dict(
        merchant_slug="trader-joes",
        kind="TEXT",
        text="TATER BITES POTATO WITH",
        product_id=fact.product_id,
        product_revision=fact.revision,
        expected_alias_revision=expected,
    )
    arguments.update(changes)
    return call(server, "confirm_product_alias", **arguments)


def reject(server, expected, **changes) -> dict:
    arguments = dict(
        merchant_slug="trader-joes",
        kind="TEXT",
        text="TATER BITES POTATO WITH",
        expected_alias_revision=expected,
    )
    arguments.update(changes)
    return call(server, "reject_product_alias", **arguments)


def stored(client: DynamoClient) -> ProductAlias | None:
    return client.get_product_alias(
        "trader-joes", "TEXT", "TATER BITES POTATO WITH"
    )


def test_both_servers_expose_identical_tool_schemas():
    schemas = {}
    for name, module in SERVER_MODULES.items():
        tools = {
            tool.name: (tool.description, tool.inputSchema)
            for tool in asyncio.run(module.list_tools())
        }
        assert set(NEW_TOOLS) <= set(tools)
        schemas[name] = {tool: tools[tool] for tool in NEW_TOOLS}
    assert schemas["scripts"] == schemas["lambda"]
    confirm_schema = schemas["scripts"]["confirm_product_alias"][1]
    assert set(confirm_schema["required"]) == {
        "merchant_slug",
        "kind",
        "text",
        "product_id",
        "product_revision",
        "expected_alias_revision",
    }
    assert confirm_schema["properties"]["kind"]["enum"] == ["TEXT", "ITEM"]
    reject_schema = schemas["scripts"]["reject_product_alias"][1]
    assert set(reject_schema["required"]) == {
        "merchant_slug",
        "kind",
        "text",
        "expected_alias_revision",
    }
    assert reject_schema["properties"]["status"]["enum"] == [
        "rejected",
        "not_food",
    ]


def test_both_servers_share_the_same_implementation():
    sources = {
        name: "".join(
            inspect.getsource(getattr(module, attr))
            for attr in (
                "_save_user_alias_decision",
                "confirm_product_alias_impl",
                "reject_product_alias_impl",
            )
        )
        for name, module in SERVER_MODULES.items()
    }
    assert sources["scripts"] == sources["lambda"]
    # DynamoDB is reached only through DynamoClient methods.
    assert not re.search(r"\._client\b", sources["scripts"])
    assert "boto3" not in sources["scripts"]
    assert "transact_write_items" not in sources["scripts"]


def test_confirm_creates_user_decision_pinned_to_revision(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)

    result = confirm(server, fact, None)

    assert result["success"] is True, result
    alias = result["alias"]
    assert alias["revision"] == 1
    assert alias["status"] == "matched"
    assert alias["method"] == "user"
    assert alias["confirmed_by_user"] is True
    assert alias["expires_at"] is None
    assert alias["product_id"] == fact.product_id
    assert alias["product_revision"] == fact.revision
    row = stored(client)
    assert row is not None
    assert (row.revision, row.status, row.method) == (1, "matched", "user")
    assert row.confirmed_by_user is True and row.expires_at is None
    assert (row.product_id, row.product_revision) == (
        fact.product_id,
        fact.revision,
    )
    assert json.loads(row.decision_json)["tool"] == "confirm_product_alias"


def test_confirm_replaces_automatic_pending_alias(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    pending = save(client, automatic_alias())

    result = confirm(server, fact, 1)

    assert result["success"] is True, result
    assert result["previous_revision"] == 1
    assert result["previous_status"] == "pending"
    row = stored(client)
    assert row.revision == 2 and row.method == "user"
    assert row.applicability_json == pending.applicability_json
    assert row.product_revision == fact.revision and row.expires_at is None


def test_confirm_uses_the_client_table_for_the_dal_guard(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    with patch.object(
        client, "save_product_alias", wraps=client.save_product_alias
    ) as spy:
        assert confirm(server, fact, None)["success"] is True
    assert spy.call_count == 1
    kwargs = spy.call_args.kwargs
    assert kwargs["expected_table_name"] == client.table_name
    assert kwargs["expected_revision"] == 0


def test_confirm_missing_product_revision_writes_nothing(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    other = product(2)  # a real product id, a revision never written

    result = confirm(server, other, None)

    assert "error" in result and "does not exist" in result["error"]
    assert other.revision in result["error"]
    assert stored(client) is None

    result = confirm(server, fact, None, product_id="fdc:nope")
    assert "error" in result and "does not exist" in result["error"]
    assert stored(client) is None

    result = confirm(server, fact, None, product_revision="not-a-hash")
    assert "error" in result and "invalid product pin" in result["error"]
    assert stored(client) is None


def test_confirm_missing_product_writes_nothing_over_pending(server, client):
    save(client, automatic_alias())
    result = confirm(server, product(), 1)
    assert "error" in result and "does not exist" in result["error"]
    row = stored(client)
    assert row.revision == 1 and row.method == "lexical"


def test_stale_expected_revision_surfaces_conflict(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    first = save(client, automatic_alias())
    second = save(client, replace(first, revision=2), 1)

    for stale in (None, 1, 3):
        result = confirm(server, fact, stale)
        assert result.get("conflict") is True, result
        assert result["current_revision"] == 2
        assert "expected_alias_revision=2" in result["error"]
        assert result["current"]["revision"] == 2
        assert stored(client) == second

    result = reject(server, 1)
    assert result.get("conflict") is True
    assert result["current_revision"] == 2
    assert stored(client) == second


def test_write_race_after_read_is_a_conflict_not_a_retry(server, client):
    """Pre-read sees revision 1, another writer lands 2, CAS refuses."""
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    first = save(client, automatic_alias())
    second = save(client, replace(first, revision=2), 1)
    with patch.object(
        client, "get_product_alias", side_effect=[first, second]
    ) as reads:
        with patch.object(
            client, "save_product_alias", wraps=client.save_product_alias
        ) as writes:
            result = confirm(server, fact, 1)
    assert result.get("conflict") is True, result
    assert result["current_revision"] == 2
    assert "expected_alias_revision=2" in result["error"]
    assert writes.call_count == 1
    assert reads.call_count == 2
    assert stored(client) == second


def test_reject_nulls_the_product_pin(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    save(client, automatic_alias(fact))

    result = reject(server, 1)

    assert result["success"] is True, result
    alias = result["alias"]
    assert alias["status"] == "rejected" and alias["revision"] == 2
    assert alias["method"] == "user" and alias["confirmed_by_user"] is True
    assert alias["expires_at"] is None
    assert alias["product_id"] is None and alias["product_revision"] is None
    row = stored(client)
    assert (row.status, row.product_id, row.product_revision) == (
        "rejected",
        None,
        None,
    )
    decision = json.loads(row.decision_json)
    assert decision["previous_product_revision"] == fact.revision

    result = reject(server, 2, status="not_food")
    assert result["success"] is True and stored(client).status == "not_food"

    result = reject(server, 3, status="matched")
    assert "error" in result and stored(client).revision == 3


def test_reject_creates_user_decision_when_absent(server, client):
    result = reject(server, None, kind="ITEM", text="84621")
    assert result["success"] is True, result
    row = client.get_product_alias("trader-joes", "ITEM", "84621")
    assert (row.revision, row.status, row.method) == (1, "rejected", "user")
    assert row.expires_at is None and row.product_id is None


def test_automatic_writes_cannot_overwrite_user_decisions(server, client):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    assert confirm(server, fact, None)["success"] is True
    confirmed = stored(client)

    with pytest.raises(NutritionConflictError):
        save(client, automatic_alias(revision=2), 1)
    with pytest.raises(NutritionConflictError):
        save(client, automatic_alias(fact, revision=2), 1)
    assert stored(client) == confirmed

    # A later user decision (reject after confirm) still goes through.
    assert reject(server, 1)["success"] is True
    assert stored(client).status == "rejected"
    with pytest.raises(NutritionConflictError):
        save(client, automatic_alias(fact, revision=3), 2)
    assert stored(client).status == "rejected"


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "SKU"},
        {"expected_alias_revision": -1},
        {"expected_alias_revision": True},
        {"expected_alias_revision": "1"},
        {"text": " "},
    ],
)
def test_invalid_arguments_write_nothing(server, client, arguments):
    fact = product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    result = confirm(server, fact, None, **arguments)
    assert "error" in result and "success" not in result
    assert stored(client) is None
    result = reject(server, None, **arguments)
    assert "error" in result and "success" not in result
    assert stored(client) is None


@pytest.mark.parametrize(
    "table_name", ["ReceiptsTable-d7ff76a", "copy-d7ff76a-of-prod"]
)
def test_prod_table_is_refused_before_any_write(server, table_name):
    """No nutrition row is ever seeded or written to a prod-named table.

    The client is built against the prod name (the moto table is empty)
    and the boto client is spied on: the tools must return a refusal (the
    server's or the DAL's) without a single DynamoDB call.
    """
    with mock_aws():
        prod_like = DynamoClient(create_table(table_name))
        fact = product()  # never written anywhere
        spied = {
            method: patch.object(
                prod_like._client,
                method,
                wraps=getattr(prod_like._client, method),
            )
            for method in (
                "get_item",
                "put_item",
                "query",
                "transact_write_items",
            )
        }
        spies = {name: spy.start() for name, spy in spied.items()}
        try:
            with patch.object(server, "_dynamo_client", prod_like):
                confirmed = confirm(server, fact, None)
                rejected = reject(server, None, kind="ITEM", text="84621")
        finally:
            for spy in spied.values():
                spy.stop()
        for result in (confirmed, rejected):
            assert "success" not in result
            assert (
                "refusing to write nutrition rows" in result["error"]
                or "prohibited" in result["error"]
            )
        assert {name: spy.call_count for name, spy in spies.items()} == {
            name: 0 for name in spies
        }
