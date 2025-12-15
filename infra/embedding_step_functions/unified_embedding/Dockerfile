# syntax=docker/dockerfile:1
# Multi-stage build for Embedding Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_places/ /tmp/receipt_places/
COPY receipt_agent/ /tmp/receipt_agent/

# Install dependencies (receipt_label no longer required for ingest handlers)
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma /tmp/receipt_places /tmp/receipt_agent

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the embedding handler and supporting files (matches compactor pattern)
COPY infra/embedding_step_functions/unified_embedding/handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY infra/embedding_step_functions/unified_embedding/router.py ${LAMBDA_TASK_ROOT}/router.py
COPY infra/embedding_step_functions/unified_embedding/config.py ${LAMBDA_TASK_ROOT}/config.py
COPY infra/embedding_step_functions/unified_embedding/embedding_ingest.py ${LAMBDA_TASK_ROOT}/embedding_ingest.py
COPY infra/embedding_step_functions/unified_embedding/__init__.py ${LAMBDA_TASK_ROOT}/__init__.py
# Copy handlers directory
COPY infra/embedding_step_functions/unified_embedding/handlers/ ${LAMBDA_TASK_ROOT}/handlers/
# Copy utils directory
COPY infra/embedding_step_functions/unified_embedding/utils/ ${LAMBDA_TASK_ROOT}/utils/

# Set the handler
CMD ["handler.lambda_handler"]
