# syntax=docker/dockerfile:1
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
ARG CACHE_DATE
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}}

# Only install system dependencies if not using base image
RUN if [ -z "$BASE_IMAGE" ]; then \
        dnf install -y gcc python3-devel && \
        dnf clean all && \
        rm -rf /var/cache/dnf; \
    fi

# Only copy and install packages if not using base image
# When using base image, packages are already installed
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label

RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -z "$BASE_IMAGE" ]; then \
        pip install --no-cache-dir /tmp/receipt_dynamo && \
        pip install --no-cache-dir /tmp/receipt_label; \
    else \
        echo "Packages already installed in base image"; \
    fi && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy Lambda handler
COPY infra/embedding_step_functions/list_pending_batches_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["handler.poll_handler"]