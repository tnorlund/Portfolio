#!/bin/bash
# Script to run the MCP server with proper setup

cd /Users/tnorlund/claude_b/Portfolio/receipt_label

echo "Starting Receipt Validation MCP Server..."
echo "=========================================="
echo ""
echo "This will start the MCP Inspector for testing your server."
echo ""
echo "To test in MCP Inspector:"
echo "  1. When the browser opens, you'll see the connection form"
echo "  2. Command: python"
echo "  3. Arguments: /Users/tnorlund/claude_b/Portfolio/receipt_label/mcp_isolated/server_clean.py"
echo "  4. Transport: STDIO (default)"
echo "  5. Click 'Connect'"
echo ""
echo "To use with Claude Desktop:"
echo "  1. Add this configuration to Claude Desktop's MCP servers:"
echo '     {
       "receipt-validation": {
         "command": "python",
         "args": ["/Users/tnorlund/claude_b/Portfolio/receipt_label/mcp_isolated/server_clean.py"],
         "cwd": "/Users/tnorlund/claude_b/Portfolio/receipt_label"
       }
     }'
echo "  2. Restart Claude Desktop"
echo ""
echo "Starting MCP Inspector..."
echo ""

# Run the MCP dev server with the isolated server
cd mcp_isolated
mcp dev server_clean.py:mcp