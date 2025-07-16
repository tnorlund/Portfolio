# Claude Desktop MCP Server Setup

This guide explains how to connect the receipt validation MCP server to Claude Desktop.

## Prerequisites

1. Claude Desktop app installed
2. Python environment with dependencies installed
3. Pulumi CLI configured and logged in
4. Access to the `tnorlund/portfolio/dev` stack

## Setup Steps

### 1. Locate Claude Desktop Configuration

On macOS, the Claude Desktop configuration file is located at:
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

### 2. Add MCP Server Configuration

Edit the configuration file to add your MCP server. You can either:

**Option A: Add to existing config**
```json
{
  "mcpServers": {
    "receipt-validation": {
      "command": "python",
      "args": [
        "/Users/tnorlund/claude_b/Portfolio/receipt_label/mcp_validation_server_simple.py"
      ],
      "env": {
        "PYTHONPATH": "/Users/tnorlund/claude_b/Portfolio"
      }
    }
  }
}
```

**Option B: Use the provided config**
```bash
# Copy the provided config to Claude Desktop
cp claude_desktop_config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

### 3. Install Dependencies

Make sure all Python dependencies are installed:
```bash
cd /Users/tnorlund/claude_b/Portfolio/receipt_label
pip install -e .
```

### 4. Test the Server Standalone

Before connecting to Claude Desktop, test that the server works:
```bash
python test_pulumi_access.py
```

You should see:
```
✅ All tests passed! The MCP server should work correctly.
```

### 5. Restart Claude Desktop

After updating the configuration:
1. Quit Claude Desktop completely (Cmd+Q)
2. Start Claude Desktop again
3. The MCP server should start automatically

### 6. Verify Connection

In Claude Desktop, you should see:
- A new tools icon (🔧) in the chat interface
- When clicked, it should show tools like:
  - `test_connection`
  - `validate_label`
  - `get_receipt_labels`
  - `save_label`

## Using the MCP Server

Once connected, you can use the tools in your conversations:

### Test Connection
```
Use the test_connection tool to verify DynamoDB and Pinecone access
```

### Validate Labels
```
Use validate_label with label "MERCHANT_NAME" to check if it's valid
```

### Get Receipt Labels
```
Use get_receipt_labels with:
- image_id: "550e8400-e29b-41d4-a716-446655440000"
- receipt_id: 12345
```

### Save a Label
```
Use save_label with:
- image_id: "550e8400-e29b-41d4-a716-446655440000"
- receipt_id: 12345
- line_id: 1
- word_id: 1
- label: "MERCHANT_NAME"
- reasoning: "Top text identified as merchant"
```

## Troubleshooting

### Server doesn't appear in Claude Desktop

1. Check the config file syntax is valid JSON
2. Ensure the Python path is correct
3. Check Claude Desktop logs:
   ```
   ~/Library/Logs/Claude/mcp.log
   ```

### Server fails to start

1. Test the server standalone first:
   ```bash
   python mcp_validation_server_simple.py
   ```

2. Check that Pulumi is accessible:
   ```bash
   pulumi whoami
   pulumi stack select tnorlund/portfolio/dev
   ```

3. Verify Python environment:
   ```bash
   which python
   python --version
   ```

### Tools don't work

1. Use the `test_connection` tool first to verify services are initialized
2. Check that the Pulumi stack has the required configuration
3. Verify AWS credentials are set up for DynamoDB access

## Alternative: Using npx

If you prefer, you can also run the server with npx:

1. First create a wrapper script:
```bash
cat > run_mcp_server.sh << 'EOF'
#!/bin/bash
cd /Users/tnorlund/claude_b/Portfolio/receipt_label
python mcp_validation_server_simple.py
EOF

chmod +x run_mcp_server.sh
```

2. Then use in Claude Desktop config:
```json
{
  "mcpServers": {
    "receipt-validation": {
      "command": "bash",
      "args": ["/Users/tnorlund/claude_b/Portfolio/receipt_label/run_mcp_server.sh"]
    }
  }
}
```

## Development Tips

- The server auto-initializes with the "dev" stack on startup
- All Pulumi configuration is fetched automatically
- No manual environment variables needed
- Check the terminal output when testing to see initialization status

## Server Features

The simple MCP server provides:
- Automatic Pulumi stack configuration
- Connection testing for DynamoDB and Pinecone
- Label validation against CORE_LABELS
- Receipt label retrieval and storage
- Clear error messages if initialization fails