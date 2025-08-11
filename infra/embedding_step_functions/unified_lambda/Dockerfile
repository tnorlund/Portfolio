# syntax=docker/dockerfile:1
# Unified Lambda container for all embedding step functions
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

# Copy the unified handler module (simplified path in minimal context)
COPY unified_lambda/ ${LAMBDA_TASK_ROOT}/

# Copy original handlers for backward compatibility (pre-organized in legacy/ dir)
COPY legacy/ ${LAMBDA_TASK_ROOT}/legacy/

# Default handler is the router
# Individual Lambda functions will override this with environment variables
CMD ["router.lambda_handler"]