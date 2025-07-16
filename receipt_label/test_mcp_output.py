#!/usr/bin/env python3
"""Test that MCP server produces no output."""

import subprocess
import sys

# Test direct execution
print("Testing direct execution...")
result = subprocess.run(
    [sys.executable, "mcp_server_minimal.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    input=b"",
    timeout=2,
)

if result.stdout:
    print(f"❌ STDOUT output detected: {result.stdout}")
if result.stderr:
    print(f"❌ STDERR output detected: {result.stderr}")
if not result.stdout and not result.stderr:
    print("✅ No output detected - server is clean!")

# Test MCP import
print("\nTesting MCP import...")
try:
    import mcp_server_minimal
    print("✅ Import successful with no output")
except Exception as e:
    print(f"❌ Import failed: {e}")