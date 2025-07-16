# MCP Server Implementation Guide

This document captures the lessons learned while implementing a Model Context Protocol (MCP) server for receipt validation using Python and FastMCP.

## Overview

The MCP (Model Context Protocol) allows building servers that expose tools and resources to LLM applications like Claude Desktop. This implementation provides tools for validating receipt labels and interacting with DynamoDB and Pinecone.

## Key Lessons Learned

### 1. STDIO Transport Challenges

When using STDIO transport (the default for Claude Desktop), the MCP protocol communicates via stdin/stdout using JSON-RPC. This creates several challenges:

**Problem**: Any output to stdout/stderr interferes with the protocol
- Print statements break the JSON-RPC communication
- Module imports that print messages cause protocol errors
- Even logging to stderr shows as "error output" in MCP Inspector

**Solution**: Complete output suppression
```python
# Disable all logging
import logging
logging.disable(logging.CRITICAL)

# Use subprocess with capture_output=True for external commands
subprocess.run(cmd, capture_output=True)

# Lazy imports to avoid module initialization output
def my_tool():
    from some_module import something  # Import only when needed
```

### 2. Lazy Initialization Pattern

To avoid any output during server startup, use lazy initialization:

```python
_initialized = False
_config = None
_clients = None

def _lazy_init():
    global _initialized, _config, _clients
    
    if _initialized:
        return _config, _clients
    
    # Initialize only when first tool is called
    # All initialization code here with no print statements
    
    _initialized = True
    return _config, _clients
```

### 3. Proper Tool Return Types

FastMCP expects tools to return dictionaries (JSON-serializable objects):

```python
@mcp.tool()
def my_tool(param: str) -> dict:  # Use 'dict' not 'Dict[str, Any]'
    return {
        "success": True,
        "data": "result"
    }
```

**Note**: FastMCP automatically wraps return values in a `{"result": your_data}` structure. This may cause schema warnings in MCP Inspector but works correctly.

### 4. File Organization

To avoid import conflicts and ensure clean initialization:

```
receipt_label/
├── mcp_isolated/
│   └── server.py          # Clean, isolated server
├── run_mcp_server.sh      # Script to run with MCP Inspector
└── claude_desktop_config.json  # Configuration for Claude Desktop
```

### 5. Transport Types

MCP supports three transport types:

1. **STDIO** (default for Claude Desktop)
   - Pros: Secure, simple, process isolation
   - Cons: Sensitive to any output, one instance per session
   - Best for: Desktop applications, local tools

2. **SSE** (Server-Sent Events)
   - Pros: Multiple clients, persistent server, can show debug output
   - Cons: Requires port management, security considerations
   - Best for: Web applications, shared servers

3. **Streamable HTTP**
   - Pros: Most flexible, bidirectional streaming
   - Cons: Most complex to implement
   - Best for: Production services

### 6. Claude Desktop Integration

To use with Claude Desktop:

1. Create server with no output during initialization
2. Configure in Claude Desktop's MCP servers settings:
```json
{
  "receipt-validation": {
    "command": "python",
    "args": ["/full/path/to/server.py"],
    "cwd": "/working/directory"
  }
}
```
3. Restart Claude Desktop

### 7. Testing with MCP Inspector

The MCP Inspector (`mcp dev`) is useful for testing but has quirks:
- Shows stderr as "error output" even if it's just logging
- May show schema warnings for valid responses
- Helps verify tools are working before Claude Desktop integration

Run with:
```bash
mcp dev server.py:mcp
```

## Implementation Checklist

- [ ] Disable all logging and print statements
- [ ] Use lazy initialization for all setup code
- [ ] Capture output from all subprocess calls
- [ ] Return proper JSON-serializable dictionaries from tools
- [ ] Test with MCP Inspector first
- [ ] Use isolated directory to avoid import conflicts
- [ ] Document all tools with clear descriptions
- [ ] Handle errors gracefully with structured error responses

## Common Pitfalls to Avoid

1. **Import-time side effects**: Any module that prints during import will break STDIO
2. **Global initialization**: Initializing clients/connections at module level
3. **Unhandled exceptions**: Always wrap operations in try/except
4. **Non-JSON types**: Ensure all return values are JSON-serializable
5. **Logging to stdout**: Never log to stdout, only stderr (and disable in production)

## Final Server Structure

The final working server (`mcp_isolated/server.py`) implements:
- Complete logging suppression
- Lazy initialization of all dependencies
- Proper error handling
- Clean JSON responses
- Tools for receipt validation operations

This architecture ensures reliable operation with Claude Desktop while maintaining clean separation of concerns and proper error handling.