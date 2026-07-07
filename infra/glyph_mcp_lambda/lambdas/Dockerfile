# Glyph Studio MCP Server Lambda (read-only).
# Wraps the Glyph Studio inspection tools for remote HTTP access via a
# Lambda Function URL. numpy + PIL only — no scipy/skimage/node.

# Stage 1: dependencies (cached layer — rebuilds only when these change)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# The glyphstudio package (numpy + PIL) plus the MCP-over-Lambda adapter.
COPY tools/glyph-studio/py/ /tmp/glyphstudio_pkg/

RUN pip install --no-cache-dir /tmp/glyphstudio_pkg && \
    pip install --no-cache-dir \
        mcp \
        boto3 \
        run-mcp-servers-with-aws-lambda && \
    rm -rf /tmp/glyphstudio_pkg

# Stage 2: Lambda-specific code (changes often; doesn't bust the deps cache)
FROM dependencies

# Baked-in font sources (skeleton JSON the tools read).
COPY tools/glyph-studio/fonts/ ${LAMBDA_TASK_ROOT}/fonts/

# Minimal receipt_agent shim: compile_report's self-check instantiates the
# renderer's BitmapFont (numpy + PIL only). Vendoring just this module keeps
# the heavy receipt_agent deps out of the image while satisfying the import
# `receipt_agent.agents.label_evaluator.rendering.bitmap_font`.
RUN mkdir -p ${LAMBDA_TASK_ROOT}/receipt_agent/agents/label_evaluator/rendering && \
    touch ${LAMBDA_TASK_ROOT}/receipt_agent/__init__.py \
          ${LAMBDA_TASK_ROOT}/receipt_agent/agents/__init__.py \
          ${LAMBDA_TASK_ROOT}/receipt_agent/agents/label_evaluator/__init__.py \
          ${LAMBDA_TASK_ROOT}/receipt_agent/agents/label_evaluator/rendering/__init__.py
COPY receipt_agent/receipt_agent/agents/label_evaluator/rendering/bitmap_font.py \
     ${LAMBDA_TASK_ROOT}/receipt_agent/agents/label_evaluator/rendering/bitmap_font.py

# The glyph MCP server package.
RUN mkdir -p ${LAMBDA_TASK_ROOT}/glyph_mcp_server
COPY infra/glyph_mcp_lambda/lambdas/glyph_mcp_server_server.py ${LAMBDA_TASK_ROOT}/glyph_mcp_server/server.py
COPY infra/glyph_mcp_lambda/lambdas/glyph_mcp_server_init.py ${LAMBDA_TASK_ROOT}/glyph_mcp_server/__init__.py
COPY infra/glyph_mcp_lambda/lambdas/glyph_mcp_server_main.py ${LAMBDA_TASK_ROOT}/glyph_mcp_server/__main__.py

# The Lambda handler.
COPY infra/glyph_mcp_lambda/lambdas/handler.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
