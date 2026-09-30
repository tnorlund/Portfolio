"""Exercise the actual local stdio protocol; no receipt or network fixture."""

import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = (
    Path(__file__).resolve().parents[1] / "scripts/receipt_link_mcp_server.py"
)


def test_local_tool_has_structured_safe_read_only_results() -> None:
    async def verify() -> None:
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(SERVER)]
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 1
                assert tools[0].name == "get_clover_receipt"
                assert tools[0].annotations.readOnlyHint is True
                assert tools[0].annotations.destructiveHint is False
                assert tools[0].outputSchema is not None
                for url in (
                    "https://127.0.0.1/security-test",
                    ["https://127.0.0.1/security-test"],
                ):
                    result = await session.call_tool(
                        "get_clover_receipt", {"url": url}
                    )
                    assert result.isError
                    assert result.structuredContent["status"] == "error"
                    assert result.structuredContent["error_code"] == (
                        "unsupported_url"
                    )
                    assert "security-test" not in json.dumps(
                        result.model_dump()
                    )

    asyncio.run(verify())


def test_malformed_protocol_does_not_log_access_link() -> None:
    async def verify() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SERVER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "security-test", "version": "1"},
                },
            }
            process.stdin.write((json.dumps(initialize) + "\n").encode())
            await process.stdin.drain()
            await asyncio.wait_for(process.stdout.readline(), 5)
            malformed = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_clover_receipt",
                    "arguments": ["https://www.clover.com/p/secret-marker"],
                },
            }
            process.stdin.write(
                (
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized",
                        }
                    )
                    + "\n"
                    + json.dumps(malformed)
                    + "\n"
                ).encode()
            )
            await process.stdin.drain()
            output = await asyncio.wait_for(process.stdout.readline(), 5)
            assert "error" in json.loads(output)
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        errors = await process.stderr.read()
        assert b"secret-marker" not in output + errors

    asyncio.run(verify())


def test_cancellation_holds_slots_until_workers_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(SERVER.parent))
    spec = importlib.util.spec_from_file_location("receipt_link_test", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release = threading.Event()
    started = threading.Event()
    lock = threading.Lock()
    counts = {"active": 0, "started": 0, "peak": 0}

    def fetch(_url: str) -> dict:
        with lock:
            counts["active"] += 1
            counts["started"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            if counts["started"] == 2:
                started.set()
        release.wait(5)
        with lock:
            counts["active"] -= 1
        return {"status": "error"}

    monkeypatch.setattr(module, "fetch_clover_receipt", fetch)

    async def verify() -> None:
        first = [
            asyncio.create_task(module._bounded_fetch("")) for _ in range(2)
        ]
        assert await asyncio.to_thread(started.wait, 3)
        for task in first:
            task.cancel()
        await asyncio.gather(*first, return_exceptions=True)
        third = asyncio.create_task(module._bounded_fetch(""))
        await asyncio.sleep(0)
        assert counts["started"] == 2
        release.set()
        await asyncio.wait_for(third, 3)
        assert counts["peak"] == 2
        assert counts["started"] == 3

    try:
        asyncio.run(verify())
    finally:
        release.set()
