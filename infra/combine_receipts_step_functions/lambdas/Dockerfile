# syntax=docker/dockerfile:1
# Multi-stage build for Combine Receipts Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Install system dependencies for image processing
# Use microdnf (lightweight, C-based) instead of dnf (Python-based) for smaller image size
# microdnf is optimized for containers and reduces Lambda cold start time
# Add libpng for Pillow PNG support and libheif for AVIF plugin usage.
RUN microdnf install -y \
    libjpeg-turbo-devel \
    libpng-devel \
    zlib-devel \
    libheif \
    && microdnf clean all

# Ensure Pillow wheel is present before local package installs
RUN pip install --no-cache-dir Pillow

# Set environment variable to avoid compilation issues with hnswlib
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_places/ /tmp/receipt_places/
COPY receipt_agent/ /tmp/receipt_agent/
COPY receipt_upload/ /tmp/receipt_upload/

# Install dependencies from pyproject.toml files
# Install in dependency order
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_upload && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma /tmp/receipt_places /tmp/receipt_upload /tmp/receipt_agent

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the combine receipts handler and shared logic
COPY infra/combine_receipts_step_functions/lambdas/combine_receipts.py ${LAMBDA_TASK_ROOT}/handler.py
COPY infra/combine_receipts_step_functions/lambdas/combine_receipts_logic.py ${LAMBDA_TASK_ROOT}/combine_receipts_logic.py
COPY infra/combine_receipts_step_functions/lambdas/geometry_utils.py ${LAMBDA_TASK_ROOT}/geometry_utils.py
COPY infra/combine_receipts_step_functions/lambdas/records_builder.py ${LAMBDA_TASK_ROOT}/records_builder.py
COPY infra/combine_receipts_step_functions/lambdas/metadata_utils.py ${LAMBDA_TASK_ROOT}/metadata_utils.py
COPY infra/combine_receipts_step_functions/lambdas/s3_io.py ${LAMBDA_TASK_ROOT}/s3_io.py
COPY infra/combine_receipts_step_functions/lambdas/embedding_utils.py ${LAMBDA_TASK_ROOT}/embedding_utils.py

# Set the handler (Lambda container image format)
CMD ["handler.handler"]

