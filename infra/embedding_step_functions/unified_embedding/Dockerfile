# syntax=docker/dockerfile:1
# Unified Lambda container for embedding step functions
ARG PYTHON_VERSION=3.12
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

# Install system dependencies required for Python packages
RUN dnf install -y gcc gcc-c++ python3-devel && \
    dnf clean all && \
    rm -rf /var/cache/dnf

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy and install Python packages
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_label && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy the unified embedding handler module
COPY infra/embedding_step_functions/unified_embedding/ ${LAMBDA_TASK_ROOT}/

# Also copy the old unified_lambda for temporary compatibility
# (since we're using wrappers for complex handlers)
# TODO: Remove this once handlers are fully migrated
COPY infra/embedding_step_functions/unified_lambda/ ${LAMBDA_TASK_ROOT}/unified_lambda/

# Set the handler
CMD ["handler.lambda_handler"]