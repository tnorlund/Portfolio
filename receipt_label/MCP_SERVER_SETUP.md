# Modern MCP Server Setup (2025)

This guide explains how to set up and run the receipt validation MCP server using the modern Python MCP SDK.

## Installation

1. **Install dependencies** (if not already installed):
```bash
cd /Users/tnorlund/claude_b/Portfolio/receipt_label
pip install -e .
```

2. **Test Pulumi access**:
```bash
python test_pulumi_access.py
```

You should see:
```
✅ All tests passed! The MCP server should work correctly.
```

## Running the Server

### Option 1: Using UV (Recommended)

If you have `uv` installed:
```bash
# Install uv if you don't have it
pip install uv

# Run the MCP server
uv run mcp dev mcp_validation_server_simple.py
```

### Option 2: Using Python MCP CLI

```bash
# The mcp[cli] package should be installed from pyproject.toml
python -m mcp dev mcp_validation_server_simple.py
```

### Option 3: Direct Python (for testing)

```bash
# This will test initialization but won't start the MCP server
python mcp_validation_server_simple.py
```

## Connecting to Claude Desktop

Once your server is running with `uv run mcp dev` or `python -m mcp dev`, it will show you:
1. A connection URL or instructions
2. How to add it to Claude Desktop

The modern MCP approach handles all the communication protocols automatically - you don't need to configure stdio, SSE, or other transport methods manually.

## Available Tools

The server provides these tools:

1. **test_connection** - Test DynamoDB and Pinecone connectivity
2. **validate_label** - Check if a label is valid according to CORE_LABELS
3. **get_receipt_labels** - Retrieve all labels for a specific receipt
4. **save_label** - Save a new label to DynamoDB

## Architecture

The modern MCP server uses FastMCP, which provides:
- Automatic tool registration with decorators
- Built-in type validation
- Simplified server setup
- No manual stdio/SSE configuration needed

## Troubleshooting

### Server won't start
1. Check Pulumi access: `python test_pulumi_access.py`
2. Verify you're in the right directory
3. Ensure all dependencies are installed: `pip install -e .`

### Can't connect to Claude Desktop
1. Make sure server is running with `mcp dev` command
2. Check Claude Desktop is up to date
3. Follow the connection instructions shown by the MCP dev server

### Tools not working
1. Use `test_connection` tool first to verify services
2. Check AWS credentials are configured
3. Verify Pulumi stack has required secrets

## Key Differences from Legacy MCP

- No more manual Server() class instantiation
- Uses FastMCP with decorator-based tool definitions
- No asyncio boilerplate needed
- Automatic transport handling (stdio/SSE)
- Built-in CLI tools for development