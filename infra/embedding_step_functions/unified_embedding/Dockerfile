# syntax=docker/dockerfile:1
# Unified Lambda container for embedding step functions
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy the unified embedding handler module
COPY infra/embedding_step_functions/unified_embedding/ ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["handler.lambda_handler"]