# syntax=docker/dockerfile:1
# Unified Lambda container for all embedding step functions
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}}

# Install system dependencies if not using base image
RUN if [ -z "$BASE_IMAGE" ]; then \
        dnf install -y gcc gcc-c++ python3-devel && \
        dnf clean all && \
        rm -rf /var/cache/dnf; \
    fi

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy and install Python packages
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

# Copy the unified handler module
COPY infra/embedding_step_functions/unified_lambda/ ${LAMBDA_TASK_ROOT}/

# Copy original handlers for backward compatibility during migration
# These can be removed once migration is complete
COPY infra/embedding_step_functions/chromadb_word_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/legacy/word_polling.py
COPY infra/embedding_step_functions/chromadb_compaction_lambda/handler.py ${LAMBDA_TASK_ROOT}/legacy/compaction.py
COPY infra/embedding_step_functions/chromadb_line_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/legacy/line_polling.py
COPY infra/embedding_step_functions/find_unembedded_lines_lambda/handler.py ${LAMBDA_TASK_ROOT}/legacy/find_unembedded.py
COPY infra/embedding_step_functions/submit_to_openai_lambda/handler.py ${LAMBDA_TASK_ROOT}/legacy/submit_openai.py
COPY infra/embedding_step_functions/list_pending_batches_lambda/handler.py ${LAMBDA_TASK_ROOT}/legacy/list_pending.py

# Default handler is the router
# Individual Lambda functions will override this with environment variables
CMD ["router.lambda_handler"]