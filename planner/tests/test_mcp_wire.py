"""Actual stdio MCP to DynamoDB Local to HTTP, with isolated disposable data."""

import asyncio
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from uuid import uuid4

import boto3
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from planner.data.client import DynamoClient, table_definition
from planner.http import make_handler
from planner.service import Planner


@pytest.mark.skipif(
    not os.environ.get("PLANNER_TEST_ENDPOINT"),
    reason="Set a loopback DynamoDB Local endpoint for wire tests",
)
def test_mcp_agent_write_is_visible_over_http(monkeypatch):
    endpoint = os.environ["PLANNER_TEST_ENDPOINT"]
    assert endpoint.startswith("http://127.0.0.1:")
    table = "PlannerMcpWire-" + uuid4().hex[:12]
    for key, value in {
        "PLANNER_ENV": "local",
        "PLANNER_TABLE": table,
        "PLANNER_ENDPOINT": endpoint,
    }.items():
        monkeypatch.setenv(key, value)
    client = boto3.client(
        "dynamodb",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    client.create_table(**table_definition(table))
    service = Planner(DynamoClient(table, client=client))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "planner.mcp"],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert {
                    t.name for t in (await session.list_tools()).tools
                } == {
                    "get_planner",
                    "apply_change",
                    "propose_changes",
                    "resolve_proposal",
                }

                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError, result.content
                    return json.loads(result.content[0].text)

                command = {
                    "action": "save_item",
                    "text": "Wire evaluation task",
                    "week": "2026-09-07",
                }
                created = await call(
                    "apply_change",
                    {
                        "command": command,
                        "request_id": "mcp-create",
                    },
                )
                assert (
                    await call(
                        "apply_change",
                        {
                            "command": command,
                            "request_id": "mcp-create",
                        },
                    )
                    == created
                )
                item = created["result"]
                proposal = await call(
                    "propose_changes",
                    {
                        "title": "A little space on Tuesday",
                        "rationale": "The user requested a suggested placement.",
                        "changes": [
                            {
                                "action": "save_item",
                                "id": item["id"],
                                "revision": item["revision"],
                                "date": "2026-09-08",
                            }
                        ],
                        "request_id": "mcp-propose",
                    },
                )
                assert service.snapshot()["items"][0]["date"] is None
                await call(
                    "resolve_proposal",
                    {
                        "proposal_id": proposal["result"]["id"],
                        "decision": "accept",
                        "request_id": "mcp-accept",
                    },
                )
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/planner/api/snapshot"
                ) as response:
                    snapshot = json.load(response)
                assert len(snapshot["items"]) == 1
                assert snapshot["items"][0]["id"] == item["id"]
                assert snapshot["items"][0]["date"] == "2026-09-08"
                assert snapshot["proposals"][0]["status"] == "accepted"
                assert snapshot["version"] == 3
                assert snapshot["env"] == "local"
                assert snapshot["table"] == table

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        client.delete_table(TableName=table)
