ARG PYTHON_VERSION=3.12
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

# Install system dependencies
RUN dnf install -y gcc python3-devel && \
    dnf clean all && \
    rm -rf /var/cache/dnf

# Copy and install Python dependencies
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label

# Install packages
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_label && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy Lambda handler (using the existing file)
COPY infra/embedding_step_functions/list_pending_line_batches_for_polling.py ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["list_pending_line_batches_for_polling.poll_handler"]