"""glyph_mcp_server package for Lambda deployment.

Read-only hosted Glyph Studio MCP server. Unlike the receipt MCP
server, this one needs no Pulumi/DynamoDB config patching: it reads
baked-in font sources and lazily pulls letterform corpora from S3
using the Lambda's own IAM role.
"""
