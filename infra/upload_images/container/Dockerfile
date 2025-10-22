# syntax=docker/dockerfile:1
# Multi-stage build for Upload Images Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/

# Install dependencies with full ChromaDB support
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir "/tmp/receipt_label[full]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the upload images handler module
COPY infra/upload_images/container/ ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["handler.lambda_handler"]


