"""
receipt_mcp_server package for Lambda deployment.

Patches ``load_env`` and ``load_secrets`` from receipt_dynamo so
that the MCP server reads configuration from Lambda environment
variables instead of calling the Pulumi CLI (which is not available
in the Lambda container).
"""
