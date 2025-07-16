#!/usr/bin/env python3
"""Test script to verify MCP server works correctly."""

import subprocess
import time
import os

print("Testing MCP server...")
print("=" * 50)

# Start the MCP dev server
proc = subprocess.Popen(
    ["mcp", "dev", "mcp_validation_server_simple.py:mcp"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd=os.path.dirname(os.path.abspath(__file__))
)

# Give it some time to start
time.sleep(3)

# Check if it's still running
if proc.poll() is None:
    print("✅ MCP server started successfully!")
    print("\nTo connect:")
    print("1. Open the MCP Inspector URL shown in the terminal")
    print("2. Use these settings:")
    print("   - Command: python")
    print("   - Arguments: /Users/tnorlund/claude_b/Portfolio/receipt_label/mcp_validation_server_simple.py")
    print("   - Transport: STDIO")
    print("\nPress Ctrl+C to stop the server")
    
    try:
        # Keep it running
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping server...")
        proc.terminate()
else:
    # It exited, show error
    stdout, stderr = proc.communicate()
    print("❌ MCP server failed to start")
    print("\nSTDOUT:")
    print(stdout)
    print("\nSTDERR:")
    print(stderr)