# Use the base image with receipt_label (which includes receipt_dynamo)
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}}

# Always reinstall packages to ensure we have the latest version without pinecone
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label

RUN pip install --no-cache-dir --force-reinstall /tmp/receipt_dynamo && \
    pip install --no-cache-dir --force-reinstall /tmp/receipt_label && \
    pip install --no-cache-dir chromadb>=0.5.0 && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy handler code
COPY infra/word_label_step_functions/chromadb_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Command to run the Lambda handler
CMD ["handler.poll_handler"]