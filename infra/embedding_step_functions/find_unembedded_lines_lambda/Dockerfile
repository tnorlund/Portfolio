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
RUN if [ -z "$BASE_IMAGE" ]; then \
        echo "Installing packages..."; \
    fi

# Always copy and install packages to ensure we have the latest version
# even when using base image (base might have old version with pinecone)
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label

RUN pip install --no-cache-dir --force-reinstall /tmp/receipt_dynamo && \
    pip install --no-cache-dir --force-reinstall /tmp/receipt_label && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy Lambda handler
COPY infra/embedding_step_functions/find_unembedded_lines_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["handler.lambda_handler"]