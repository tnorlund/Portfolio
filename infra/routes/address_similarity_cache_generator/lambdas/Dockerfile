# syntax=public.ecr.aws/docker/library/dockerfile:1.9.0
# Multi-stage build for Address Similarity Cache Generator Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_label/ /tmp/receipt_label/

# Install dependencies with full ChromaDB support
# receipt_label still needed for DynamoDB orchestration functions (not ChromaDB-related)
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir "/tmp/receipt_label[full]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma /tmp/receipt_label

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the cache generator handler module
# Match the pattern from compaction Lambda exactly: handler file is in the same directory as Dockerfile
# The rsync includes the entire infra/routes/address_similarity_cache_generator/lambdas/ directory
# So we need the full path from context root (same pattern as compaction Lambda)
COPY infra/routes/address_similarity_cache_generator/lambdas/index.py ${LAMBDA_TASK_ROOT}/index.py

# Set the handler
CMD ["index.handler"]

