# syntax=docker/dockerfile:1
# Use the base image with receipt_label (which includes receipt_dynamo)
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
ARG CACHE_DATE
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}}

# Conditionally install packages based on base image presence
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label

RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -z "$BASE_IMAGE" ]; then \
        # No base image - install everything
        pip install --no-cache-dir /tmp/receipt_dynamo && \
        pip install --no-cache-dir /tmp/receipt_label; \
    else \
        # Base image has receipt_label which already includes chromadb>=0.5.0
        echo "Using base image - all dependencies including chromadb already installed"; \
    fi && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy handler code
COPY infra/word_label_step_functions/chromadb_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Command to run the Lambda handler
CMD ["handler.poll_handler"]