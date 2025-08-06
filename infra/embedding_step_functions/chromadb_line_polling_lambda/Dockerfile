# syntax=docker/dockerfile:1
# Use AWS Lambda Python base image for ARM64 or base image if provided
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
ARG CACHE_DATE
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:3.12-arm64}

# Install system dependencies for ChromaDB and other packages (only if no base image)
RUN if [ -z "$BASE_IMAGE" ]; then \
        dnf install -y gcc-c++ python3-devel && \
        dnf clean all; \
    fi

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# ChromaDB is already included in receipt_label dependencies
# No additional pip installs needed when using base image
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -z "$BASE_IMAGE" ]; then \
        # No base image - need to install dependencies manually
        echo "Warning: Running without base image, installing minimal deps"; \
        pip install --no-cache-dir boto3>=1.34.0 pydantic>=2.0.0; \
    else \
        # Base image has receipt_label which includes chromadb>=0.5.0
        echo "Using base image - all dependencies already installed"; \
    fi

# Copy local packages
# These paths are relative to the build context (repository root)
COPY receipt_dynamo/ /lambda/receipt_dynamo/
COPY receipt_label/ /lambda/receipt_label/

# Install local packages only if not using base image
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -z "$BASE_IMAGE" ]; then \
        cd /lambda && \
        pip install --no-cache-dir ./receipt_dynamo && \
        pip install --no-cache-dir ./receipt_label; \
    else \
        echo "Packages already installed in base image"; \
    fi

# Copy handler code
COPY infra/embedding_step_functions/chromadb_line_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Add /lambda to Python path so imports work
ENV PYTHONPATH=/lambda:$PYTHONPATH

# Command to run the Lambda handler
CMD ["handler.poll_handler"]