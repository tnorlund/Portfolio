"""Entry point for ``python -m glyph_mcp_server``.

Delegates to the server ``main()``. The stdio_server_adapter in the
Lambda handler spawns this module as a subprocess and bridges the
Function URL HTTP request to its stdio MCP transport.
"""

import asyncio

from glyph_mcp_server.server import main

asyncio.run(main())
