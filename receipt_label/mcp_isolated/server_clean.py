"""Clean modular MCP server with auto-registration."""

import logging
from mcp.server import FastMCP
from typing import Optional

# Disable all logging to keep output clean for STDIO transport
logging.disable(logging.CRITICAL)

# Create the MCP server
mcp = FastMCP("receipt-validation")

# Auto-register tools by importing and decorating them
from tools.health import test_connection
from tools.labels import validate_label, get_receipt_labels, save_label
from tools.receipts import list_receipts
from core.client_manager import get_client_manager

# Register tools with their original names
mcp.tool()(test_connection)
mcp.tool()(validate_label)
mcp.tool()(get_receipt_labels)
mcp.tool()(save_label)
mcp.tool()(list_receipts)

if __name__ == "__main__":
    # Initialize clients at startup
    get_client_manager()
    mcp.run()