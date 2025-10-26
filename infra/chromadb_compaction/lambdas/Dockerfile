# syntax=docker/dockerfile:1
# Multi-stage build for ChromaDB Compaction Lambda
# Updated to fix compaction package import issues

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/

# Install dependencies
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir "/tmp/receipt_label[full]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the enhanced compaction handler and utils
COPY infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY infra/chromadb_compaction/lambdas/utils/ ${LAMBDA_TASK_ROOT}/utils/
# Ensure compaction package is available to the handler
COPY infra/chromadb_compaction/lambdas/compaction/ ${LAMBDA_TASK_ROOT}/compaction/

# Set the handler
CMD ["handler.lambda_handler"]