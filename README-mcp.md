# MCP Development Tools

This repository defines a Python MCP server for the receipts
packages. The server is registered as `python-receipts` in
`mcp-config.json`.

## Starting the server

Inside Cursor you can launch the server with the `@mcp` command:

```text
@mcp start python-receipts
```

This runs `python mcp_server.py` using the environment variables
listed in [README.md](README.md). Ensure your virtual environment is
active so that the correct Python interpreter is used.

## Example commands

Once running, the server exposes tools for testing and linting the
Python packages:

```text
@mcp run_tests receipt_label
@mcp run_lint receipt_dynamo
```

Pulumi operations will also be available through MCP once those tools
are implemented.

See [portfolio/README-cursor-integration.md](portfolio/README-cursor-integration.md)
for instructions on setting up Cursor with MCP.
