# syntax=public.ecr.aws/docker/library/dockerfile:1.9.0
# Multi-stage build for Process OCR Results Lambda with Merchant Validation

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_label/ /tmp/receipt_label/
COPY receipt_upload/ /tmp/receipt_upload/

# Install dependencies with full ChromaDB support
# receipt_upload depends on receipt_dynamo, receipt_label depends on receipt_dynamo
# receipt_label still needed for DynamoDB orchestration functions (not ChromaDB-related)
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_upload && \
    pip install --no-cache-dir "/tmp/receipt_label[full]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma /tmp/receipt_label /tmp/receipt_upload

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Bust cache for handler code changes
ARG CACHE_BUST=unknown
RUN echo "Cache bust: $CACHE_BUST"

# Copy the handler module
COPY infra/upload_images/container_ocr/handler/ ${LAMBDA_TASK_ROOT}/handler/

# Set the handler
CMD ["handler.handler.lambda_handler"]

