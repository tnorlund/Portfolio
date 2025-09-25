# syntax=docker/dockerfile:1
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Copy the embed worker container handler from repo root context
COPY infra/upload_images/container/ ${LAMBDA_TASK_ROOT}/

# Default handler (container-style, like validate)
CMD ["handler.lambda_handler"]


