# syntax=docker/dockerfile:1
# Multi-stage build for Word Similarity Cache Generator Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/

# Install dependencies with full ChromaDB support
# receipt_dynamo: DynamoDB client and constants
# receipt_chroma: ChromaDB snapshot management and compaction logic
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the cache generator handler module
COPY infra/routes/word_similarity_cache_generator/lambdas/index.py ${LAMBDA_TASK_ROOT}/index.py

# Set the handler
CMD ["index.handler"]
