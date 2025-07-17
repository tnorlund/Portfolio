"""Modular MCP server with clean architecture."""

import logging
from mcp.server import FastMCP
from typing import Optional

# Import tools
from tools.health import test_connection
from tools.labels import validate_label, get_receipt_labels, save_label
from tools.receipts import list_receipts
from core.client_manager import get_client_manager

# Disable all logging to keep output clean for STDIO transport
logging.disable(logging.CRITICAL)

# Create the MCP server
mcp = FastMCP("receipt-validation")

# Register tools with MCP decorators
@mcp.tool()
def test_connection_tool() -> dict:
    """Test connection to DynamoDB and Pinecone."""
    return test_connection()

@mcp.tool()
def validate_label_tool(label: str) -> dict:
    """Check if a label is valid according to CORE_LABELS."""
    return validate_label(label)

@mcp.tool()
def get_receipt_labels_tool(image_id: str, receipt_id: int) -> dict:
    """Get all labels for a receipt from DynamoDB."""
    return get_receipt_labels(image_id, receipt_id)

@mcp.tool()
def save_label_tool(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    reasoning: str = "Manual validation",
) -> dict:
    """Save a single label to DynamoDB."""
    return save_label(image_id, receipt_id, line_id, word_id, label, reasoning)

@mcp.tool()
def list_receipts_tool(
    limit: Optional[int] = None,
    last_evaluated_key: Optional[dict] = None,
) -> dict:
    """List receipts from DynamoDB with pagination support."""
    return list_receipts(limit, last_evaluated_key)

if __name__ == "__main__":
    # Initialize clients at startup
    get_client_manager()
    mcp.run()