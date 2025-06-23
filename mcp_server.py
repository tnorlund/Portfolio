"""Simple MCP stdio server for development tools."""

import asyncio
from pathlib import Path
from typing import Any, Iterable

from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

ROOT = Path(__file__).resolve().parent


async def _run_cmd(cmd: list[str], cwd: Path) -> str:
    """Run a subprocess command and capture output."""

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    return stdout.decode()


async def run_tests(package: str) -> str:
    """Execute pytest for the given package."""

    path = ROOT / package
    if not path.is_dir():
        raise ValueError(f"Unknown package: {package}")
    return await _run_cmd(["pytest", "-n", "auto"], path)


async def run_lint(package: str) -> str:
    """Run formatting, linting and type checks for the package."""

    path = ROOT / package
    if not path.is_dir():
        raise ValueError(f"Unknown package: {package}")

    output = []
    output.append(await _run_cmd(["isort", "--profile", "black", "."], path))
    output.append(await _run_cmd(["black", "-l", "79", "."], path))
    output.append(await _run_cmd(["pylint", package], path))
    output.append(await _run_cmd(["mypy", package], path))
    return "\n".join(output)


async def handle_tool(
    name: str, arguments: dict[str, Any]
) -> Iterable[TextContent]:
    """Dispatch a tool call based on its name."""

    if name == "run_tests":
        result = await run_tests(str(arguments["package"]))
    elif name == "run_lint":
        result = await run_lint(str(arguments["package"]))
    else:
        result = f"Unknown tool: {name}"
    return [TextContent(type="text", text=result)]


def create_tools() -> list[Tool]:
    """Return the list of tools supported by the server."""

    package_schema = {
        "type": "object",
        "properties": {"package": {"type": "string"}},
        "required": ["package"],
    }
    return [
        Tool(
            name="run_tests",
            description="Run pytest for a package",
            inputSchema=package_schema,
        ),
        Tool(
            name="run_lint",
            description="Run linting tools for a package",
            inputSchema=package_schema,
        ),
    ]


async def main() -> None:
    """Run the MCP server with stdio transport."""

    server: Server = Server("portfolio-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return create_tools()

    @server.call_tool()
    async def tool_runner(
        name: str, arguments: dict[str, Any]
    ) -> Iterable[TextContent]:
        return await handle_tool(name, arguments)

    init_options = server.create_initialization_options(NotificationOptions())

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
