# Multi-stage build for Receipt MCP Server Lambda
# Wraps the receipt MCP server for remote HTTP access via Lambda Function URL

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues with hnswlib
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_dynamo_stream/ /tmp/receipt_dynamo_stream/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_upload/ /tmp/receipt_upload/
COPY receipt_places/ /tmp/receipt_places/
COPY receipt_agent/ /tmp/receipt_agent/

# Install dependencies from pyproject.toml files in dependency order
# receipt_dynamo_stream depends on receipt_dynamo
# receipt_chroma depends on receipt_dynamo_stream
# receipt_places depends on receipt_dynamo
# receipt_upload depends on receipt_dynamo, receipt_chroma, and receipt_places
# receipt_agent depends on receipt_dynamo, receipt_chroma, receipt_upload, and receipt_places
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_dynamo_stream && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_upload && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    pip install --no-cache-dir run-mcp-servers-with-aws-lambda && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_dynamo_stream /tmp/receipt_chroma /tmp/receipt_upload /tmp/receipt_places /tmp/receipt_agent

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Create receipt_mcp_server package directory
RUN mkdir -p ${LAMBDA_TASK_ROOT}/receipt_mcp_server

# Copy the receipt MCP server as the package's server module
COPY infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py ${LAMBDA_TASK_ROOT}/receipt_mcp_server/server.py

# Copy the Lambda-aware __init__ and __main__ for the package
COPY infra/mcp_server_lambda/lambdas/receipt_mcp_server_init.py ${LAMBDA_TASK_ROOT}/receipt_mcp_server/__init__.py
COPY infra/mcp_server_lambda/lambdas/receipt_mcp_server_main.py ${LAMBDA_TASK_ROOT}/receipt_mcp_server/__main__.py

# Copy the Lambda handler
COPY infra/mcp_server_lambda/lambdas/handler.py ${LAMBDA_TASK_ROOT}/handler.py

# Set the handler
CMD ["handler.handler"]
