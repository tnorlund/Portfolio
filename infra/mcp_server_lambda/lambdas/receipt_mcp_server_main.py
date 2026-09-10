"""Run the shared MCP server with the Lambda environment and capabilities."""

import asyncio

from receipt_mcp_server import load_config, set_active_model
from receipt_mcp_server.server import main

if __name__ == "__main__":
    asyncio.run(main(config=load_config(), model_activator=set_active_model))
