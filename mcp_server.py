"""Simple MCP stdio server for development tools."""

import asyncio
from pathlib import Path
from typing import Any, Iterable, Sequence

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


async def _run_pulumi(
    action: str, stack: str | None = None, args: Sequence[str] | None = None
) -> str:
    """Execute a Pulumi command inside the infra directory."""

    cmd = ["pulumi", action]
    if stack:
        cmd.extend(["--stack", stack])
    if args:
        cmd.extend(list(args))
    return await _run_cmd(cmd, ROOT / "infra")


async def run_pulumi_preview(
    stack: str | None = None, args: Sequence[str] | None = None
) -> str:
    """Run ``pulumi preview`` in the infra directory."""

    return await _run_pulumi("preview", stack, args)


async def run_pulumi_up(
    stack: str | None = None, args: Sequence[str] | None = None
) -> str:
    """Run ``pulumi up`` in the infra directory."""

    return await _run_pulumi("up", stack, args)


async def run_pulumi_stack_list() -> str:
    """List all Pulumi stacks."""

    return await _run_cmd(["pulumi", "stack", "ls"], ROOT / "infra")


async def run_pulumi_stack_output(
    stack: str | None = None, output_name: str | None = None
) -> str:
    """Get outputs from a Pulumi stack."""

    cmd = ["pulumi", "stack", "output"]
    if stack:
        cmd.extend(["--stack", stack])
    if output_name:
        cmd.append(output_name)
    return await _run_cmd(cmd, ROOT / "infra")


async def run_pulumi_refresh(
    stack: str | None = None, args: Sequence[str] | None = None
) -> str:
    """Refresh Pulumi state to match actual infrastructure."""

    return await _run_pulumi("refresh", stack, args)


async def run_pulumi_logs(
    stack: str | None = None, args: Sequence[str] | None = None
) -> str:
    """Get logs from the last Pulumi operation."""

    return await _run_pulumi("logs", stack, args)


async def run_pulumi_config_get(key: str, stack: str | None = None) -> str:
    """Get a configuration value from Pulumi."""

    cmd = ["pulumi", "config", "get", key]
    if stack:
        cmd.extend(["--stack", stack])
    return await _run_cmd(cmd, ROOT / "infra")


async def handle_tool(name: str, arguments: dict[str, Any]) -> Iterable[TextContent]:
    """Dispatch a tool call based on its name."""

    if name == "run_tests":
        result = await run_tests(str(arguments["package"]))
    elif name == "run_lint":
        result = await run_lint(str(arguments["package"]))
    elif name == "pulumi_preview":
        stack = arguments.get("stack")
        args = arguments.get("args")
        result = await run_pulumi_preview(stack, args)
    elif name == "pulumi_up":
        stack = arguments.get("stack")
        args = arguments.get("args")
        result = await run_pulumi_up(stack, args)
    elif name == "pulumi_stack_list":
        result = await run_pulumi_stack_list()
    elif name == "pulumi_stack_output":
        stack = arguments.get("stack")
        output_name = arguments.get("output_name")
        result = await run_pulumi_stack_output(stack, output_name)
    elif name == "pulumi_refresh":
        stack = arguments.get("stack")
        args = arguments.get("args")
        result = await run_pulumi_refresh(stack, args)
    elif name == "pulumi_logs":
        stack = arguments.get("stack")
        args = arguments.get("args")
        result = await run_pulumi_logs(stack, args)
    elif name == "pulumi_config_get":
        key = str(arguments["key"])
        stack = arguments.get("stack")
        result = await run_pulumi_config_get(key, stack)
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
    pulumi_schema = {
        "type": "object",
        "properties": {
            "stack": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
    }
    pulumi_output_schema = {
        "type": "object",
        "properties": {
            "stack": {"type": "string"},
            "output_name": {"type": "string"},
        },
    }
    pulumi_config_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "stack": {"type": "string"},
        },
        "required": ["key"],
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
        Tool(
            name="pulumi_preview",
            description="Run 'pulumi preview' in the infra directory",
            inputSchema=pulumi_schema,
        ),
        Tool(
            name="pulumi_up",
            description="Run 'pulumi up' in the infra directory",
            inputSchema=pulumi_schema,
        ),
        Tool(
            name="pulumi_stack_list",
            description="List all Pulumi stacks",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="pulumi_stack_output",
            description="Get outputs from a Pulumi stack",
            inputSchema=pulumi_output_schema,
        ),
        Tool(
            name="pulumi_refresh",
            description="Refresh Pulumi state to match actual infrastructure",
            inputSchema=pulumi_schema,
        ),
        Tool(
            name="pulumi_logs",
            description="Get logs from the last Pulumi operation",
            inputSchema=pulumi_schema,
        ),
        Tool(
            name="pulumi_config_get",
            description="Get a configuration value from Pulumi",
            inputSchema=pulumi_config_schema,
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
