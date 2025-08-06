# syntax=docker/dockerfile:1
# Use AWS Lambda Python base image for ARM64 or base image if provided
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
ARG CACHE_DATE
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:3.12-arm64}

# Install system dependencies for ChromaDB compilation (only if no base image)
RUN if [ -z "$BASE_IMAGE" ]; then \
        dnf install -y gcc-c++ python3-devel && \
        dnf clean all; \
    fi

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy handler code
# These paths are relative to the build context (repository root)
COPY infra/word_label_step_functions/chromadb_compaction_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Dependencies are already in base image or will be installed with packages
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -z "$BASE_IMAGE" ]; then \
        # No base image - install minimal dependencies
        echo "Warning: Running without base image, installing minimal deps"; \
        pip install --no-cache-dir boto3>=1.34.0 numpy>=1.24.0; \
    else \
        # Base image has all dependencies via receipt_label
        echo "Using base image - all dependencies already installed"; \
    fi

# Command to run the Lambda handler
CMD ["handler.compact_handler"]