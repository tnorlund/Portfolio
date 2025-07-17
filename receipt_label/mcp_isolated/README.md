# MCP Server - Modular Architecture

This directory contains a modular MCP (Model Context Protocol) server for receipt validation.

## Structure

```
mcp_isolated/
├── server_clean.py          # Main server entry point (recommended)
├── server.py               # Original monolithic server (deprecated)
├── server_modular.py       # Alternative modular approach
├── core/                   # Core infrastructure
│   ├── __init__.py
│   ├── client_manager.py   # Client management and singleton
│   └── config.py          # Pulumi configuration loading
├── tools/                  # Individual tool implementations
│   ├── __init__.py
│   ├── health.py          # Health check tools
│   ├── labels.py          # Label validation and management
│   └── receipts.py        # Receipt listing and management
└── README.md              # This file
```

## Usage

### Running the Server

```bash
# From receipt_label directory
./run_mcp_server.sh

# Or manually from mcp_isolated directory
cd mcp_isolated
mcp dev server_clean.py:mcp
```

### Available Tools

1. **test_connection** - Test connection to DynamoDB and Pinecone
2. **validate_label** - Check if a label is valid according to CORE_LABELS
3. **get_receipt_labels** - Get all labels for a specific receipt
4. **save_label** - Save a validated label to DynamoDB
5. **list_receipts** - List receipts with pagination support

## Architecture Benefits

### Modular Design
- Each tool is in its own file for better organization
- Easy to add new tools without modifying existing code
- Clear separation of concerns

### Scalable Structure
- Core functionality (config, client management) is centralized
- Tools can be independently developed and tested
- Import system allows for easy tool registration

### Development Workflow
To add a new tool:

1. Create the tool function in the appropriate file in `tools/`
2. Add it to `tools/__init__.py`
3. Import and register it in `server_clean.py`

Example:
```python
# tools/new_feature.py
from core.client_manager import get_client_manager

def my_new_tool(param: str) -> dict:
    """My new tool description."""
    manager = get_client_manager()
    # Tool implementation
    return {"result": "success"}

# tools/__init__.py
from .new_feature import my_new_tool

# server_clean.py
from tools.new_feature import my_new_tool
mcp.tool()(my_new_tool)
```

## Configuration

The server uses Pulumi for configuration management:
- Automatically loads DynamoDB table name and API keys
- Sets environment variables for the existing ClientManager
- Provides fallback paths for different deployment scenarios

## Error Handling

All tools follow a consistent error handling pattern:
- Return `{"success": True, ...}` for successful operations
- Return `{"success": False, "error": "message"}` for failures
- Include relevant data in success responses
- Provide empty defaults for list operations on failure