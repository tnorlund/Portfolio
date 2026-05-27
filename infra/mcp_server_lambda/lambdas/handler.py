"""
Lambda handler that wraps the receipt MCP server for remote access.

Uses ``run-mcp-servers-with-aws-lambda`` (mcp_lambda) to translate
incoming Lambda Function URL HTTP requests into stdio MCP messages and
forward them to the receipt MCP server subprocess.
"""

import sys

from mcp.client.stdio import StdioServerParameters
from mcp_lambda import stdio_server_adapter

server_params = StdioServerParameters(
    command=sys.executable,
    args=["-m", "receipt_mcp_server"],
)


def handler(event, context):
    return stdio_server_adapter(server_params, event, context)
