"""Lambda HTTP adapter for the Glyph Studio MCP server."""

import os
import sys

from mcp.client.stdio import (
    StdioServerParameters,  # type: ignore[import-not-found]
)
from mcp_lambda import (  # type: ignore[import-not-found]
    APIGatewayProxyEventHandler,
    LambdaFunctionURLEventHandler,
    StdioServerAdapterRequestHandler,
)

server_params = StdioServerParameters(
    command=sys.executable,
    args=["-m", "glyph_mcp_server"],
    env=dict(os.environ),
)
request_handler = StdioServerAdapterRequestHandler(server_params)
api_gateway_handler = APIGatewayProxyEventHandler(request_handler)
function_url_handler = LambdaFunctionURLEventHandler(request_handler)


def handler(event, context):
    """Dispatch REST API v1 and Function URL v2 event envelopes."""
    if event.get("version") == "2.0":
        return function_url_handler.handle(event, context)
    return api_gateway_handler.handle(event, context)
