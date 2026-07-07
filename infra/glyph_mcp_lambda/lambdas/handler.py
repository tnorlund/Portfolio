"""Lambda handler wrapping the Glyph Studio MCP server for remote access.

Uses ``run-mcp-servers-with-aws-lambda`` (mcp_lambda) to translate
incoming Lambda Function URL HTTP requests into stdio MCP messages and
forward them to the glyph MCP server subprocess.

Lambda Function URL wraps the HTTP body in an event envelope. This
handler extracts the JSON-RPC body before passing to the adapter.
"""

import json
import os
import sys

from mcp.client.stdio import StdioServerParameters
from mcp_lambda import stdio_server_adapter

server_params = StdioServerParameters(
    command=sys.executable,
    args=["-m", "glyph_mcp_server"],
    env=dict(os.environ),
)


def handler(event, context):
    # Function URL wraps the HTTP body in event["body"]; the mcp_lambda
    # adapter expects the raw JSON-RPC object as the event.
    if "body" in event:
        body = event["body"]
        if isinstance(body, str):
            event = json.loads(body)
    return stdio_server_adapter(server_params, event, context)
